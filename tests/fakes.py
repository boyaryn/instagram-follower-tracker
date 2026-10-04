"""Test doubles shared by the unit, database and CLI tests."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from igft.domain import (
    Backend,
    FollowerPage,
    FollowerRecord,
    ProfileInfo,
    ProfileNotFound,
)


def followers(*pks: int) -> list[FollowerRecord]:
    """Build follower records with predictable usernames, e.g. followers(1, 2) -> user1, user2."""
    return [FollowerRecord(pk=pk, username=f"user{pk}", full_name=f"User {pk}", is_private=False, is_verified=False)
            for pk in pks]


class FakeFetcher:
    """Serves scripted follower pages and profiles and counts every call as one request.

    Pages are addressed by cursors "c1", "c2", ...: the cursor None returns page 0 and
    "cN" returns page N. Exceptions can be scripted for the Nth call overall (1-based)
    or for the Nth call of one method.
    """

    def __init__(
        self,
        pages: Sequence[Iterable[FollowerRecord]] = (),
        *,
        profiles: Iterable[ProfileInfo] = (),
        account: str = "research_account",
        backend: Backend = Backend.INSTALOADER,
    ) -> None:
        self.backend = backend
        self.pages = [tuple(p) for p in pages]
        self.profiles = {p.username.lower(): p for p in profiles}
        self.account = account
        self.calls: Counter[str] = Counter()
        self.log: list[tuple[str, tuple]] = []
        self.follower_page_usernames: list[str] = []
        self._raise_on_call: dict[int, BaseException] = {}
        self._raise_on_method_call: dict[tuple[str, int], BaseException] = {}

    # --- scripting ----------------------------------------------------------

    def raise_on_call(self, n: int, exc: BaseException) -> FakeFetcher:
        self._raise_on_call[n] = exc
        return self

    def raise_on(self, method: str, n: int, exc: BaseException) -> FakeFetcher:
        self._raise_on_method_call[(method, n)] = exc
        return self

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    def _record(self, method: str, *args) -> None:
        self.calls[method] += 1
        self.log.append((method, args))
        exc = self._raise_on_call.pop(self.total_calls, None) or self._raise_on_method_call.pop(
            (method, self.calls[method]), None
        )
        if exc is not None:
            raise exc

    # --- Fetcher protocol ---------------------------------------------------

    def get_profile(self, username: str) -> ProfileInfo:
        self._record("get_profile", username)
        try:
            return self.profiles[username.lower()]
        except KeyError:
            raise ProfileNotFound(username) from None

    def fetch_followers_page(self, user_id: int, cursor: str | None, *, username: str) -> FollowerPage:
        self._record("fetch_followers_page", user_id, cursor)
        self.follower_page_usernames.append(username)
        index = 0 if cursor is None else int(cursor.removeprefix("c"))
        page = self.pages[index] if index < len(self.pages) else ()
        next_cursor = f"c{index + 1}" if index + 1 < len(self.pages) else None
        return FollowerPage(followers=page, next_cursor=next_cursor)

    def check_session(self) -> str:
        self._record("check_session")
        return self.account
