import pytest
from sqlalchemy import select, text

from igft.db.follows import known_person_ids, upsert_follows
from igft.db.persons import upsert_persons
from igft.db.scans import create_scan, finish_scan
from igft.db.tables import follows, persons
from igft.db.targets import add_target
from igft.domain import Backend, FollowerRecord

pytestmark = pytest.mark.db


def _new_scan(conn, target_id):
    return create_scan(conn, target_id, Backend.INSTALOADER, "default").id


def _complete(conn, scan_id):
    finish_scan(conn, scan_id, status="complete", stop_reason="end_of_list")


def _follow_row(engine, target_id, person_id):
    with engine.connect() as conn:
        return conn.execute(
            select(follows).where(follows.c.target_id == target_id, follows.c.person_id == person_id)
        ).one()


def test_person_seen_in_two_scans_has_one_follow_with_first_and_last_seen(db_engine):
    with db_engine.begin() as conn:
        add_target(conn, 100, "target")
        upsert_persons(conn, [FollowerRecord(1, "alice")])
        first = _new_scan(conn, 100)
        upsert_follows(conn, 100, [1], first)
        _complete(conn, first)
    before = _follow_row(db_engine, 100, 1)

    with db_engine.begin() as conn:
        second = _new_scan(conn, 100)
        upsert_follows(conn, 100, [1], second)

    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM follows")).scalar_one() == 1
    after = _follow_row(db_engine, 100, 1)
    assert before.first_seen_scan_id == before.last_seen_scan_id == first
    assert after.first_seen_scan_id == first
    assert after.first_seen_at == before.first_seen_at
    assert after.last_seen_scan_id == second
    assert after.last_seen_at > before.last_seen_at


def test_follower_of_two_targets_is_one_stored_person(db_engine):
    with db_engine.begin() as conn:
        add_target(conn, 100, "one")
        add_target(conn, 200, "two")
        for target_id in (100, 200):
            upsert_persons(conn, [FollowerRecord(1, "alice")])
            scan = _new_scan(conn, target_id)
            upsert_follows(conn, target_id, [1], scan)
    with db_engine.connect() as conn:
        assert conn.execute(select(persons.c.id)).all() == [(1,)]
        assert conn.execute(select(follows.c.target_id).order_by(follows.c.target_id)).all() == [(100,), (200,)]


def test_known_excludes_people_first_seen_in_the_current_scan(db_engine):
    with db_engine.begin() as conn:
        add_target(conn, 100, "target")
        upsert_persons(conn, [FollowerRecord(i, f"user{i}") for i in (1, 2, 3)])
        earlier = _new_scan(conn, 100)
        upsert_follows(conn, 100, [1, 2], earlier)
        _complete(conn, earlier)
        current = _new_scan(conn, 100)
        upsert_follows(conn, 100, [3], current)

        assert known_person_ids(conn, 100, [1, 2, 3, 4], current) == {1, 2}
        # Seen again by the current scan, 1 is still known from the earlier one.
        upsert_follows(conn, 100, [1, 3], current)
        assert known_person_ids(conn, 100, [1, 2, 3, 4], current) == {1, 2}


def test_known_is_per_target(db_engine):
    with db_engine.begin() as conn:
        add_target(conn, 100, "one")
        add_target(conn, 200, "two")
        upsert_persons(conn, [FollowerRecord(1, "alice")])
        on_one = _new_scan(conn, 100)
        upsert_follows(conn, 100, [1], on_one)
        on_two = _new_scan(conn, 200)
        assert known_person_ids(conn, 200, [1], on_two) == set()


def test_known_with_nothing_asked_is_empty(db_engine):
    with db_engine.begin() as conn:
        assert known_person_ids(conn, 100, [], 1) == set()


def test_person_missing_from_a_later_full_scan_keeps_their_follow_row(db_engine):
    with db_engine.begin() as conn:
        add_target(conn, 100, "target")
        upsert_persons(conn, [FollowerRecord(1, "alice"), FollowerRecord(2, "bob")])
        first = _new_scan(conn, 100)
        upsert_follows(conn, 100, [1, 2], first)
        _complete(conn, first)
    gone_before = _follow_row(db_engine, 100, 2)

    with db_engine.begin() as conn:
        full = create_scan(conn, 100, Backend.INSTALOADER, "full").id
        upsert_follows(conn, 100, [1], full)
        _complete(conn, full)

    assert _follow_row(db_engine, 100, 2) == gone_before


def test_empty_page_writes_nothing(db_engine):
    with db_engine.begin() as conn:
        add_target(conn, 100, "target")
        scan = _new_scan(conn, 100)
        upsert_follows(conn, 100, [], scan)
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM follows")).scalar_one() == 0
