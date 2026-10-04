"""Follow relationships between a target and a person (design D7, D9)."""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.postgresql import insert

from igft.db.tables import follows


def upsert_follows(conn: Connection, target_id: int, person_ids: Collection[int], scan_id: int) -> None:
    """Record that these people were seen in this scan.

    A new row gets first-seen = last-seen = this scan. An existing row only gets its last-seen
    moved: first-seen is set once and never changed, and nothing removes a row.
    """
    if not person_ids:
        return
    stmt = insert(follows).values(
        [
            {
                "target_id": target_id,
                "person_id": person_id,
                "first_seen_scan_id": scan_id,
                "last_seen_scan_id": scan_id,
            }
            for person_id in person_ids
        ]
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[follows.c.target_id, follows.c.person_id],
            set_={"last_seen_scan_id": stmt.excluded.last_seen_scan_id, "last_seen_at": func.now()},
        )
    )


def known_person_ids(
    conn: Connection, target_id: int, person_ids: Collection[int], excluding_scan_id: int
) -> set[int]:
    """People in `person_ids` already followers of this target from a scan other than `excluding_scan_id`.

    Followers first stored by the current scan do not count, so a restarted scan never mistakes its
    own earlier pages for old followers.
    """
    if not person_ids:
        return set()
    rows = conn.execute(
        select(follows.c.person_id).where(
            follows.c.target_id == target_id,
            follows.c.person_id.in_(list(person_ids)),
            follows.c.first_seen_scan_id != excluding_scan_id,
        )
    )
    return {person_id for (person_id,) in rows}
