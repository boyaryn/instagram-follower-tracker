"""Read-only report queries (design D13).

Nothing here imports `igft.fetchers`, so a report cannot make an Instagram request.
"First seen" means the scan in which igft first recorded the person as a follower of the target.
Followers first seen in the target's baseline scan are never reported by the `first-seen` queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Connection, Engine, select

from igft.db import scans as scans_repo
from igft.db import targets as targets_repo
from igft.db.tables import follows, persons, scans
from igft.domain import IgftError, UnknownTarget


class UnknownScan(IgftError):
    def __init__(self, scan_id: int) -> None:
        super().__init__(f"Scan {scan_id} does not exist.")


class ScanOfAnotherTarget(IgftError):
    def __init__(self, scan_id: int, username: str) -> None:
        super().__init__(f"Scan {scan_id} is not a scan of @{username}.")


@dataclass(frozen=True)
class FollowerRow:
    id: int
    username: str
    full_name: str | None
    first_seen_at: datetime
    tags: tuple[str, ...]
    is_marked: bool


@dataclass(frozen=True)
class Report:
    rows: list[FollowerRow]
    notes: list[str] = field(default_factory=list)


def _target(conn: Connection, username: str) -> targets_repo.Target:
    name = username.strip().removeprefix("@")
    target = targets_repo.get_target_by_username(conn, name)
    if target is None:
        raise UnknownTarget(name)
    return target


def _rows(conn: Connection, target_id: int, *conditions) -> list[FollowerRow]:
    stmt = (
        select(
            persons.c.id,
            persons.c.username,
            persons.c.full_name,
            follows.c.first_seen_at,
            persons.c.tags,
            persons.c.is_marked,
        )
        .select_from(
            follows.join(persons, persons.c.id == follows.c.person_id).join(
                scans, scans.c.id == follows.c.first_seen_scan_id
            )
        )
        .where(follows.c.target_id == target_id, *conditions)
        .order_by(follows.c.first_seen_at, persons.c.id)
    )
    return [
        FollowerRow(
            id=row.id,
            username=row.username,
            full_name=row.full_name,
            first_seen_at=row.first_seen_at,
            tags=tuple(row.tags or ()),
            is_marked=row.is_marked,
        )
        for row in conn.execute(stmt)
    ]


def list_followers(engine: Engine, username: str) -> Report:
    """Every person recorded as a follower of the target."""
    with engine.connect() as conn:
        target = _target(conn, username)
        return Report(_rows(conn, target.id))


def first_seen_latest(engine: Engine, username: str) -> Report:
    """Followers first seen in the target's most recent scan, which may be unfinished."""
    with engine.connect() as conn:
        target = _target(conn, username)
        scan = scans_repo.latest_scan(conn, target.id)
        if scan is None:
            return Report([], [f"@{target.username} has not been scanned yet."])
        notes: list[str] = []
        rows: list[FollowerRow] = []
        if scan.is_baseline:
            notes.append(
                f"The latest scan of @{target.username} (scan {scan.id}) was the baseline. "
                "Baseline followers are never reported as first seen."
            )
        else:
            rows = _rows(conn, target.id, follows.c.first_seen_scan_id == scan.id)
        if scan.status == "unfinished":
            note = f"Scan {scan.id} is not complete."
            if not scan.is_baseline:
                note += " The followers first seen in it so far are shown."
            notes.append(f"{note} Continue it with `igft resume {target.username}`.")
        return Report(rows, notes)


def first_seen_since(engine: Engine, username: str, since: datetime) -> Report:
    """Followers first seen at or after `since` (a timezone-aware moment), leaving out the baseline."""
    with engine.connect() as conn:
        target = _target(conn, username)
        return Report(_rows(conn, target.id, follows.c.first_seen_at >= since, scans.c.is_baseline.is_(False)))


def first_seen_since_scan(engine: Engine, username: str, scan_id: int) -> Report:
    """Followers first seen in any scan of the target after scan `scan_id`."""
    with engine.connect() as conn:
        target = _target(conn, username)
        scan = scans_repo.get_scan(conn, scan_id)
        if scan is None:
            raise UnknownScan(scan_id)
        if scan.target_id != target.id:
            raise ScanOfAnotherTarget(scan_id, target.username)
        return Report(_rows(conn, target.id, follows.c.first_seen_scan_id > scan_id))
