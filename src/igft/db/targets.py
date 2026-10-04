"""Targets. There is deliberately no operation that removes a target or its followers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, func, insert, select, update

from igft.db.tables import targets


@dataclass(frozen=True)
class Target:
    id: int
    username: str
    added_at: datetime


def _target(row) -> Target | None:
    return Target(**row._mapping) if row is not None else None


def add_target(conn: Connection, target_id: int, username: str) -> Target:
    conn.execute(insert(targets).values(id=target_id, username=username))
    return get_target_by_id(conn, target_id)


def get_target_by_id(conn: Connection, target_id: int) -> Target | None:
    return _target(conn.execute(select(targets).where(targets.c.id == target_id)).one_or_none())


def get_target_by_username(conn: Connection, username: str) -> Target | None:
    return _target(conn.execute(select(targets).where(func.lower(targets.c.username) == username.lower())).one_or_none())


def rename_target(conn: Connection, target_id: int, username: str) -> None:
    conn.execute(update(targets).where(targets.c.id == target_id).values(username=username))


def list_targets(conn: Connection) -> list[Target]:
    return [_target(row) for row in conn.execute(select(targets).order_by(targets.c.added_at, targets.c.id))]
