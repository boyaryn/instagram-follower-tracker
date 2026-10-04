"""Cooldowns and session checks (design D10). Times are compared against the database's `now()`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Connection, func, insert, select

from igft.db.tables import cooldowns, session_checks
from igft.domain import Backend, SignalKind


@dataclass(frozen=True)
class Cooldown:
    id: int
    kind: str
    raw_message: str | None
    source_scan_id: int | None
    command: str
    started_at: datetime
    ends_at: datetime
    requires_session_check: bool


@dataclass(frozen=True)
class SessionCheck:
    id: int
    backend: str
    checked_at: datetime
    succeeded: bool
    account_username: str | None
    message: str | None


def add_cooldown(
    conn: Connection,
    *,
    kind: SignalKind,
    raw_message: str | None,
    source_scan_id: int | None,
    command: str,
    duration: timedelta,
    requires_session_check: bool = False,
) -> Cooldown:
    cooldown_id = conn.execute(
        insert(cooldowns)
        .values(
            kind=str(kind),
            raw_message=raw_message,
            source_scan_id=source_scan_id,
            command=command,
            ends_at=func.now() + duration,
            requires_session_check=requires_session_check,
        )
        .returning(cooldowns.c.id)
    ).scalar_one()
    return Cooldown(**conn.execute(select(cooldowns).where(cooldowns.c.id == cooldown_id)).one()._mapping)


def active_cooldowns(conn: Connection) -> list[Cooldown]:
    """Cooldowns that have not ended yet, the one ending last first."""
    rows = conn.execute(
        select(cooldowns).where(cooldowns.c.ends_at > func.now()).order_by(cooldowns.c.ends_at.desc())
    )
    return [Cooldown(**row._mapping) for row in rows]


def holds_missing_check(conn: Connection) -> list[Cooldown]:
    """Challenge holds with no successful session check recorded after the challenge, newest first."""
    checked_since = (
        select(session_checks.c.id)
        .where(session_checks.c.succeeded, session_checks.c.checked_at > cooldowns.c.started_at)
        .exists()
    )
    rows = conn.execute(
        select(cooldowns)
        .where(cooldowns.c.requires_session_check, ~checked_since)
        .order_by(cooldowns.c.started_at.desc(), cooldowns.c.id.desc())
    )
    return [Cooldown(**row._mapping) for row in rows]


def add_session_check(
    conn: Connection,
    *,
    backend: Backend,
    succeeded: bool,
    account_username: str | None = None,
    message: str | None = None,
) -> SessionCheck:
    check_id = conn.execute(
        insert(session_checks)
        .values(backend=str(backend), succeeded=succeeded, account_username=account_username, message=message)
        .returning(session_checks.c.id)
    ).scalar_one()
    return SessionCheck(**conn.execute(select(session_checks).where(session_checks.c.id == check_id)).one()._mapping)


def latest_successful_check(conn: Connection) -> SessionCheck | None:
    row = conn.execute(
        select(session_checks)
        .where(session_checks.c.succeeded)
        .order_by(session_checks.c.checked_at.desc(), session_checks.c.id.desc())
        .limit(1)
    ).one_or_none()
    return SessionCheck(**row._mapping) if row is not None else None
