from datetime import datetime

import pytest
from sqlalchemy import text

from igft.db.follows import upsert_follows
from igft.db.persons import upsert_persons
from igft.db.scans import create_scan, finish_scan
from igft.db.targets import add_target
from igft.domain import Backend, FollowerRecord, UnknownTarget
from igft.reporting.queries import (
    ScanOfAnotherTarget,
    UnknownScan,
    first_seen_latest,
    first_seen_since,
    first_seen_since_scan,
    list_followers,
)

pytestmark = pytest.mark.db

A, B = 100, 200


def new_scan(conn, target_id, *, complete=True) -> int:
    scan_id = create_scan(conn, target_id, Backend.INSTALOADER, "default").id
    if complete:
        finish_scan(conn, scan_id, status="complete", stop_reason="end_of_list")
    return scan_id


def see(conn, target_id, scan_id, *pks):
    upsert_persons(conn, [FollowerRecord(pk, f"user{pk}", f"User {pk}") for pk in pks])
    upsert_follows(conn, target_id, list(pks), scan_id)


def usernames(report):
    return [row.username for row in report.rows]


@pytest.fixture
def engine(db_engine):
    with db_engine.begin() as conn:
        add_target(conn, A, "alice")
        add_target(conn, B, "bob")
    return db_engine


def test_list_shows_one_row_per_recorded_follower(engine):
    with engine.begin() as conn:
        scan = new_scan(conn, A)
        see(conn, A, scan, *range(1, 121))

    report = list_followers(engine, "alice")

    assert len(report.rows) == 120
    assert len({row.id for row in report.rows}) == 120


def test_list_leaves_out_followers_of_other_targets(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A), 1, 2)
        see(conn, B, new_scan(conn, B), 2, 3)

    assert sorted(usernames(list_followers(engine, "alice"))) == ["user1", "user2"]
    assert sorted(usernames(list_followers(engine, "bob"))) == ["user2", "user3"]


def test_the_username_is_matched_without_case_or_at_sign(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A), 1)

    assert usernames(list_followers(engine, "@Alice")) == ["user1"]


@pytest.mark.parametrize("report", [list_followers, first_seen_latest])
def test_an_unknown_target_is_an_error(engine, report):
    with pytest.raises(UnknownTarget) as excinfo:
        report(engine, "nobody")

    assert "nobody" in str(excinfo.value)


def test_rows_carry_the_id_names_first_seen_time_tags_and_mark(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A), 7)
        conn.execute(text("UPDATE persons SET tags = '{lab,pilot}', is_marked = true WHERE id = 7"))

    (row,) = list_followers(engine, "alice").rows

    assert (row.id, row.username, row.full_name) == (7, "user7", "User 7")
    assert row.tags == ("lab", "pilot")
    assert row.is_marked is True
    assert isinstance(row.first_seen_at, datetime) and row.first_seen_at.tzinfo is not None


def test_an_untagged_follower_has_no_tags_and_no_mark(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A), 7)

    (row,) = list_followers(engine, "alice").rows

    assert row.tags == () and row.is_marked is False


def test_first_seen_shows_the_followers_the_latest_scan_saw_first(engine):
    with engine.begin() as conn:
        baseline = new_scan(conn, A)
        see(conn, A, baseline, 1, 2, 3, 4)
        second = new_scan(conn, A)
        see(conn, A, second, 1, 2, 3, 4, 10, 11, 12)

    report = first_seen_latest(engine, "alice")

    assert sorted(usernames(report)) == ["user10", "user11", "user12"]
    assert report.notes == []


def test_first_seen_after_only_a_baseline_is_empty_and_says_so(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A), 1, 2, 3)

    report = first_seen_latest(engine, "alice")

    assert report.rows == []
    assert any("baseline" in note for note in report.notes)


def test_first_seen_for_a_target_never_scanned_is_empty_and_says_so(engine):
    report = first_seen_latest(engine, "alice")

    assert report.rows == []
    assert any("not been scanned" in note for note in report.notes)


def test_first_seen_with_an_unfinished_latest_scan_shows_what_it_has_and_says_it_is_incomplete(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A), 1, 2)
        unfinished = new_scan(conn, A, complete=False)
        see(conn, A, unfinished, 1, 2, 10)

    report = first_seen_latest(engine, "alice")

    assert usernames(report) == ["user10"]
    assert any("not complete" in note for note in report.notes)


def test_first_seen_with_an_unfinished_baseline_reports_no_one(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A, complete=False), 1, 2)

    report = first_seen_latest(engine, "alice")

    assert report.rows == []
    assert any("baseline" in note for note in report.notes)
    assert any("not complete" in note for note in report.notes)


def test_a_person_is_first_seen_once_and_not_again_in_the_scan_after(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A), 1)
        see(conn, A, new_scan(conn, A), 1, 5)
        see(conn, A, new_scan(conn, A), 1, 5)

    assert usernames(first_seen_latest(engine, "alice")) == []


def test_a_follower_missed_by_earlier_scans_is_first_seen_when_it_appears(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A), 1, 2)
        see(conn, A, new_scan(conn, A), 1, 2)
        see(conn, A, new_scan(conn, A), 1, 2)
        see(conn, A, new_scan(conn, A), 1, 2, 99)

    assert usernames(first_seen_latest(engine, "alice")) == ["user99"]


def test_a_person_first_seen_for_a_second_target_is_reported_for_that_target(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A), 1)
        see(conn, B, new_scan(conn, B), 50)
        see(conn, B, new_scan(conn, B), 50, 1)

    assert usernames(first_seen_latest(engine, "bob")) == ["user1"]
    assert usernames(first_seen_latest(engine, "alice")) == []


def set_first_seen(engine, person_id, moment):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE follows SET first_seen_at = :at WHERE person_id = :p"), {"at": moment, "p": person_id}
        )


def test_since_a_date_uses_local_midnight_and_leaves_out_the_baseline(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A), 1)
        see(conn, A, new_scan(conn, A), 1, 2, 3, 4)
    midnight = datetime(2026, 9, 1).astimezone()
    set_first_seen(engine, 1, datetime(2026, 9, 10).astimezone())  # baseline follower, after the cutoff
    set_first_seen(engine, 2, datetime(2026, 8, 31, 23, 59).astimezone())
    set_first_seen(engine, 3, midnight)
    set_first_seen(engine, 4, datetime(2026, 9, 15, 12).astimezone())

    report = first_seen_since(engine, "alice", midnight)

    assert usernames(report) == ["user3", "user4"]


def test_since_a_scan_shows_followers_first_seen_in_later_scans_of_the_target(engine):
    with engine.begin() as conn:
        first = new_scan(conn, A)
        see(conn, A, first, 1)
        second = new_scan(conn, A)
        see(conn, A, second, 1, 2)
        third = new_scan(conn, A, complete=False)
        see(conn, A, third, 1, 2, 3)

    assert usernames(first_seen_since_scan(engine, "alice", first)) == ["user2", "user3"]
    assert usernames(first_seen_since_scan(engine, "alice", second)) == ["user3"]
    assert usernames(first_seen_since_scan(engine, "alice", third)) == []


def test_since_a_scan_ignores_scans_of_other_targets(engine):
    with engine.begin() as conn:
        first = new_scan(conn, A)
        see(conn, A, first, 1)
        see(conn, B, new_scan(conn, B), 60)
        see(conn, A, new_scan(conn, A), 1, 2)

    assert usernames(first_seen_since_scan(engine, "alice", first)) == ["user2"]


def test_a_scan_of_another_target_is_an_error(engine):
    with engine.begin() as conn:
        see(conn, A, new_scan(conn, A), 1)
        bobs_scan = new_scan(conn, B)

    with pytest.raises(ScanOfAnotherTarget) as excinfo:
        first_seen_since_scan(engine, "alice", bobs_scan)

    assert str(bobs_scan) in str(excinfo.value) and "alice" in str(excinfo.value)


def test_a_scan_that_does_not_exist_is_an_error(engine):
    with pytest.raises(UnknownScan):
        first_seen_since_scan(engine, "alice", 9999)
