"""The only code that writes `persons` (design D8).

The upsert's SET list names only `username`, `updated_at` and the COALESCEd
`full_name`, `is_private` and `is_verified`. The user-owned columns are left out on purpose: a new row takes their
database defaults and an existing row keeps whatever the user set.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Connection, func
from sqlalchemy.dialects.postgresql import insert

from igft.db.tables import persons
from igft.domain import FollowerRecord


def upsert_persons(conn: Connection, followers: Sequence[FollowerRecord]) -> None:
    if not followers:
        return
    stmt = insert(persons).values(
        [
            {
                "id": f.pk,
                "username": f.username,
                "full_name": f.full_name,
                "is_private": f.is_private,
                "is_verified": f.is_verified,
            }
            for f in followers
        ]
    )
    excluded = stmt.excluded
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[persons.c.id],
            set_={
                "username": excluded.username,
                "full_name": func.coalesce(excluded.full_name, persons.c.full_name),
                "is_private": func.coalesce(excluded.is_private, persons.c.is_private),
                "is_verified": func.coalesce(excluded.is_verified, persons.c.is_verified),
                "updated_at": func.now(),
            },
        )
    )
