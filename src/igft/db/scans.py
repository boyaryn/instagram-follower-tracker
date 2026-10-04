"""Scan records (design D7, D9). A scan is `unfinished` from the moment it is created."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, exists, func, insert, select, update

from igft.db.tables import follows, scans
from igft.domain import Backend, SignalKind


@dataclass(frozen=True)
class Scan:
    id: int
    target_id: int
    backend: str
    mode: str
    is_baseline: bool
    status: str
    stop_reason: str | None
    started_at: datetime
    ended_at: datetime | None
    runs: int
    pages_fetched: int
    followers_seen: int
    first_seen_followers: int
    cursor: str | None
    signal_type: str | None
    signal_message: str | None
    error_message: str | None


def _scan(row) -> Scan | None:
    return Scan(**row._mapping) if row is not None else None


def get_scan(conn: Connection, scan_id: int) -> Scan | None:
    return _scan(conn.execute(select(scans).where(scans.c.id == scan_id)).one_or_none())


def create_scan(conn: Connection, target_id: int, backend: Backend, mode: str) -> Scan:
    """Create an unfinished scan. It is the baseline when the target has no scans at all."""
    is_baseline = ~exists().where(scans.c.target_id == target_id)
    scan_id = conn.execute(
        insert(scans)
        .values(target_id=target_id, backend=str(backend), mode=mode, is_baseline=is_baseline)
        .returning(scans.c.id)
    ).scalar_one()
    return get_scan(conn, scan_id)


def record_page(conn: Connection, scan_id: int, *, cursor: str | None, followers_in_page: int) -> None:
    """Add one saved page to the counters and store the cursor for the next one.

    `first_seen_followers` is counted from `follows` rather than incremented, so it stays right
    after a restart from page 1.
    """
    first_seen = (
        select(func.count())
        .select_from(follows)
        .where(follows.c.first_seen_scan_id == scan_id)
        .scalar_subquery()
    )
    conn.execute(
        update(scans)
        .where(scans.c.id == scan_id)
        .values(
            cursor=cursor,
            pages_fetched=scans.c.pages_fetched + 1,
            followers_seen=scans.c.followers_seen + followers_in_page,
            first_seen_followers=first_seen,
        )
    )


def finish_scan(conn: Connection, scan_id: int, *, status: str, stop_reason: str) -> None:
    conn.execute(
        update(scans)
        .where(scans.c.id == scan_id)
        .values(status=status, stop_reason=stop_reason, ended_at=func.now())
    )


def record_signal(conn: Connection, scan_id: int, kind: SignalKind, raw_message: str) -> None:
    conn.execute(
        update(scans).where(scans.c.id == scan_id).values(signal_type=str(kind), signal_message=raw_message)
    )


def record_error(conn: Connection, scan_id: int, raw_message: str) -> None:
    conn.execute(update(scans).where(scans.c.id == scan_id).values(error_message=raw_message))


def find_unfinished_scan(conn: Connection, target_id: int) -> Scan | None:
    return _scan(
        conn.execute(
            select(scans).where(scans.c.target_id == target_id, scans.c.status == "unfinished")
        ).one_or_none()
    )


def latest_scan(conn: Connection, target_id: int) -> Scan | None:
    return _scan(
        conn.execute(
            select(scans).where(scans.c.target_id == target_id).order_by(scans.c.id.desc()).limit(1)
        ).one_or_none()
    )


def mark_stopped(conn: Connection, scan_id: int, stop_reason: str) -> None:
    """Record why a run stopped, leaving the scan unfinished so `resume` can continue it."""
    conn.execute(update(scans).where(scans.c.id == scan_id).values(stop_reason=stop_reason))


def begin_run(conn: Connection, scan_id: int, *, restart: bool) -> None:
    """Start another run of an unfinished scan: count it, forget the last stop and maybe the cursor."""
    values = {"runs": scans.c.runs + 1, "stop_reason": None}
    if restart:
        values["cursor"] = None
    conn.execute(update(scans).where(scans.c.id == scan_id).values(**values))
