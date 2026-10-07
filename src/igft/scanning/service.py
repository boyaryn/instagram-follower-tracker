"""The scan loop (design D9): one page per request, each page saved in one transaction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine

from igft.db import follows as follows_repo
from igft.db import persons as persons_repo
from igft.db import scans as scans_repo
from igft.db import targets as targets_repo
from igft.domain import BlockSignal, FetchError, IgftError, SignalKind, UnknownTarget
from igft.safety.gate import GuardedFetcher
from igft.safety.pacer import Pacer

LIST_UNAVAILABLE_MESSAGE = (
    "The follower list came back empty. If the target is private, make sure the research account "
    "follows it. The scan is kept; run `igft resume {username}` once that is fixed."
)


class ScanUnfinished(IgftError):
    def __init__(self, username: str) -> None:
        super().__init__(f"The last scan of @{username} is unfinished. Continue it with `igft resume {username}`.")


class NothingToResume(IgftError):
    def __init__(self, username: str) -> None:
        super().__init__(f"@{username} has no unfinished scan to resume. Start one with `igft scan {username}`.")


@dataclass(frozen=True)
class ScanResult:
    """How a run that ended without an exception stopped.

    `stop_reason` is one of `end_of_list`, `early_stop`, `page_cap` or `list_unavailable`; the first
    two mean the scan is complete.
    """

    scan: scans_repo.Scan
    username: str
    pages_this_run: int
    message: str | None = None

    @property
    def complete(self) -> bool:
        return self.scan.status == "complete"


@dataclass(frozen=True)
class ScanProgress:
    """What a run has done after saving a page that did not end it.

    `page_limit` is the page cap, an upper bound: the list may end, or an early stop may apply, sooner.
    `first_seen` is None for a baseline scan, where every follower is new.
    """

    run_pages: int
    page_limit: int
    followers_saved: int
    first_seen: int | None
    next_request_in: float


class ScanService:
    def __init__(
        self,
        engine: Engine,
        fetcher: GuardedFetcher,
        pacer: Pacer,
        *,
        early_stop_pages: int,
        page_cap: int,
        on_progress: Callable[[ScanProgress], None] | None = None,
    ) -> None:
        self._engine = engine
        self._fetcher = fetcher
        self._pacer = pacer
        self._early_stop_pages = early_stop_pages
        self._page_cap = page_cap
        self._on_progress = on_progress
        self.scan_id: int | None = None
        self.username: str | None = None

    def snapshot(self) -> scans_repo.Scan:
        """The current database state of the scan this service is running or last ran."""
        with self._engine.connect() as conn:
            return scans_repo.get_scan(conn, self.scan_id)

    def start(self, username: str, *, full: bool = False) -> ScanResult:
        name = _normalise(username)
        with self._engine.begin() as conn:
            target = targets_repo.get_target_by_username(conn, name)
            if target is None:
                raise UnknownTarget(name)
            if scans_repo.find_unfinished_scan(conn, target.id) is not None:
                raise ScanUnfinished(target.username)
            scan = scans_repo.create_scan(conn, target.id, self._fetcher.backend, "full" if full else "default")
        return self._run(target, scan)

    def resume(self, username: str, *, restart: bool = False) -> ScanResult:
        name = _normalise(username)
        with self._engine.begin() as conn:
            target = targets_repo.get_target_by_username(conn, name)
            if target is None:
                raise UnknownTarget(name)
            scan = scans_repo.find_unfinished_scan(conn, target.id)
            if scan is None:
                raise NothingToResume(target.username)
            scans_repo.begin_run(conn, scan.id, restart=restart)
            scan = scans_repo.get_scan(conn, scan.id)
        return self._run(target, scan)

    def _run(self, target: targets_repo.Target, scan: scans_repo.Scan) -> ScanResult:
        self.scan_id = scan.id
        self.username = target.username
        self._fetcher.scan_id = scan.id
        early_stop_applies = not scan.is_baseline and scan.mode != "full"
        cursor = scan.cursor
        pages = 0
        fully_known_in_a_row = 0
        withheld: BlockSignal | None = None
        try:
            while True:
                self._pacer.wait()
                page = self._fetcher.fetch_followers_page(target.id, cursor, username=target.username)
                if not page.followers:
                    saved_pages = scan.pages_fetched + pages
                    if saved_pages == 0:
                        return self._stop(target, pages, "list_unavailable", LIST_UNAVAILABLE_MESSAGE)
                    withheld = BlockSignal(
                        SignalKind.RATE_LIMIT,
                        f"empty follower page after {saved_pages} pages were saved",
                        withheld_list=True,
                    )
                    self._fetcher.record_block(withheld)
                    self._mark_stopped(scan.id, "list_unavailable")
                    raise withheld
                fully_known = self._save_page(target.id, scan.id, page)
                pages += 1
                cursor = page.next_cursor
                fully_known_in_a_row = fully_known_in_a_row + 1 if fully_known else 0

                if cursor is None:
                    return self._finish(target, pages, "end_of_list")
                if early_stop_applies and fully_known_in_a_row >= self._early_stop_pages:
                    return self._finish(target, pages, "early_stop")
                if pages >= self._page_cap:
                    return self._stop(target, pages, "page_cap")
                self._report_progress(pages)
        except BlockSignal as signal:
            if signal is not withheld:
                self._mark_stopped(scan.id, "block_signal")
            raise
        except FetchError as exc:
            self._mark_stopped(scan.id, "error", error=exc.raw_message)
            raise
        except KeyboardInterrupt:
            self._mark_stopped(scan.id, "interrupted")
            raise

    def _report_progress(self, run_pages: int) -> None:
        if self._on_progress is None:
            return
        scan = self.snapshot()
        self._on_progress(
            ScanProgress(
                run_pages=run_pages,
                page_limit=self._page_cap,
                followers_saved=scan.followers_seen,
                first_seen=None if scan.is_baseline else scan.first_seen_followers,
                next_request_in=self._pacer.plan_next(),
            )
        )

    def _save_page(self, target_id: int, scan_id: int, page) -> bool:
        """Save one page and its cursor in one transaction. True when every follower was already known."""
        followers = list({f.pk: f for f in page.followers}.values())
        ids = [f.pk for f in followers]
        with self._engine.begin() as conn:
            known = follows_repo.known_person_ids(conn, target_id, ids, scan_id)
            persons_repo.upsert_persons(conn, followers)
            follows_repo.upsert_follows(conn, target_id, ids, scan_id)
            scans_repo.record_page(conn, scan_id, cursor=page.next_cursor, followers_in_page=len(followers))
        return len(known) == len(ids)

    def _finish(self, target: targets_repo.Target, pages: int, reason: str) -> ScanResult:
        with self._engine.begin() as conn:
            scans_repo.finish_scan(conn, self.scan_id, status="complete", stop_reason=reason)
        return ScanResult(self.snapshot(), target.username, pages)

    def _stop(self, target: targets_repo.Target, pages: int, reason: str, message: str | None = None) -> ScanResult:
        self._mark_stopped(self.scan_id, reason)
        text = message.format(username=target.username) if message else None
        return ScanResult(self.snapshot(), target.username, pages, text)

    def _mark_stopped(self, scan_id: int, reason: str, *, error: str | None = None) -> None:
        with self._engine.begin() as conn:
            if error is not None:
                scans_repo.record_error(conn, scan_id, error)
            scans_repo.mark_stopped(conn, scan_id, reason)


def _normalise(username: str) -> str:
    return username.strip().removeprefix("@").lower()
