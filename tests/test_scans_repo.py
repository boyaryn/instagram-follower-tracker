import pytest
from sqlalchemy.exc import IntegrityError

from igft.db.follows import upsert_follows
from igft.db.persons import upsert_persons
from igft.db.scans import (
    create_scan,
    find_unfinished_scan,
    finish_scan,
    get_scan,
    latest_scan,
    record_error,
    record_page,
    record_signal,
)
from igft.db.targets import add_target
from igft.domain import Backend, FollowerRecord, SignalKind

pytestmark = pytest.mark.db


@pytest.fixture
def engine(db_engine):
    with db_engine.begin() as conn:
        add_target(conn, 100, "one")
        add_target(conn, 200, "two")
    return db_engine


def test_first_scan_of_a_target_is_the_baseline_and_starts_unfinished(engine):
    with engine.begin() as conn:
        scan = create_scan(conn, 100, Backend.INSTALOADER, "default")
    assert scan.is_baseline is True
    assert (scan.status, scan.stop_reason, scan.ended_at) == ("unfinished", None, None)
    assert (scan.backend, scan.mode, scan.runs) == ("instaloader", "default", 1)
    assert (scan.pages_fetched, scan.followers_seen, scan.first_seen_followers, scan.cursor) == (0, 0, 0, None)
    assert scan.started_at is not None


def test_later_scans_are_not_baselines(engine):
    with engine.begin() as conn:
        first = create_scan(conn, 100, Backend.INSTALOADER, "default")
        finish_scan(conn, first.id, status="complete", stop_reason="end_of_list")
    with engine.begin() as conn:
        second = create_scan(conn, 100, Backend.INSTALOADER, "full")
    assert second.is_baseline is False
    assert second.mode == "full"


def test_baseline_is_decided_per_target(engine):
    with engine.begin() as conn:
        create_scan(conn, 100, Backend.INSTALOADER, "default")
        other = create_scan(conn, 200, Backend.INSTALOADER, "default")
    assert other.is_baseline is True


def test_record_page_updates_cursor_and_counters(engine):
    with engine.begin() as conn:
        scan = create_scan(conn, 100, Backend.INSTALOADER, "default")
        upsert_persons(conn, [FollowerRecord(i, f"user{i}") for i in (1, 2, 3)])
        upsert_follows(conn, 100, [1, 2, 3], scan.id)
        record_page(conn, scan.id, cursor="12", followers_in_page=3)
        upsert_follows(conn, 100, [2, 3], scan.id)
        record_page(conn, scan.id, cursor="24", followers_in_page=2)
        updated = get_scan(conn, scan.id)
    assert (updated.cursor, updated.pages_fetched, updated.followers_seen) == ("24", 2, 5)
    assert updated.first_seen_followers == 3


def test_first_seen_followers_only_counts_followers_first_seen_in_this_scan(engine):
    with engine.begin() as conn:
        upsert_persons(conn, [FollowerRecord(i, f"user{i}") for i in (1, 2, 3)])
        baseline = create_scan(conn, 100, Backend.INSTALOADER, "default")
        upsert_follows(conn, 100, [1, 2], baseline.id)
        finish_scan(conn, baseline.id, status="complete", stop_reason="end_of_list")
        later = create_scan(conn, 100, Backend.INSTALOADER, "default")
        upsert_follows(conn, 100, [1, 2, 3], later.id)
        record_page(conn, later.id, cursor=None, followers_in_page=3)
        assert get_scan(conn, later.id).first_seen_followers == 1


def test_first_seen_followers_is_recounted_after_a_restart(engine):
    with engine.begin() as conn:
        upsert_persons(conn, [FollowerRecord(1, "a"), FollowerRecord(2, "b")])
        scan = create_scan(conn, 100, Backend.INSTALOADER, "default")
        upsert_follows(conn, 100, [1, 2], scan.id)
        record_page(conn, scan.id, cursor="12", followers_in_page=2)
        # Restarted from page 1: the same two followers come back.
        upsert_follows(conn, 100, [1, 2], scan.id)
        record_page(conn, scan.id, cursor="12", followers_in_page=2)
        assert get_scan(conn, scan.id).first_seen_followers == 2


def test_record_page_can_clear_the_cursor_at_the_end_of_the_list(engine):
    with engine.begin() as conn:
        scan = create_scan(conn, 100, Backend.INSTALOADER, "default")
        record_page(conn, scan.id, cursor="12", followers_in_page=12)
        record_page(conn, scan.id, cursor=None, followers_in_page=5)
        assert get_scan(conn, scan.id).cursor is None


def test_finish_completes_a_scan_with_a_stop_reason(engine):
    with engine.begin() as conn:
        scan = create_scan(conn, 100, Backend.INSTALOADER, "default")
        finish_scan(conn, scan.id, status="complete", stop_reason="early_stop")
        done = get_scan(conn, scan.id)
    assert (done.status, done.stop_reason) == ("complete", "early_stop")
    assert done.ended_at is not None


def test_finish_can_leave_a_scan_unfinished_and_resumable(engine):
    with engine.begin() as conn:
        scan = create_scan(conn, 100, Backend.INSTALOADER, "default")
        record_page(conn, scan.id, cursor="12", followers_in_page=12)
        finish_scan(conn, scan.id, status="unfinished", stop_reason="page_cap")
        stopped = find_unfinished_scan(conn, 100)
    assert (stopped.id, stopped.stop_reason, stopped.cursor) == (scan.id, "page_cap", "12")


def test_record_signal_stores_the_type_and_raw_message(engine):
    with engine.begin() as conn:
        scan = create_scan(conn, 100, Backend.INSTALOADER, "default")
        record_signal(conn, scan.id, SignalKind.RATE_LIMIT, "Please wait a few minutes")
        finish_scan(conn, scan.id, status="unfinished", stop_reason="block_signal")
        stored = get_scan(conn, scan.id)
    assert (stored.signal_type, stored.signal_message) == ("rate_limit", "Please wait a few minutes")
    assert (stored.status, stored.stop_reason) == ("unfinished", "block_signal")


def test_record_error_stores_the_raw_message_without_a_signal(engine):
    with engine.begin() as conn:
        scan = create_scan(conn, 100, Backend.INSTALOADER, "default")
        record_error(conn, scan.id, "connection reset")
        stored = get_scan(conn, scan.id)
    assert stored.error_message == "connection reset"
    assert (stored.signal_type, stored.signal_message) == (None, None)


def test_find_unfinished_and_latest_scan(engine):
    with engine.begin() as conn:
        assert find_unfinished_scan(conn, 100) is None
        assert latest_scan(conn, 100) is None
        first = create_scan(conn, 100, Backend.INSTALOADER, "default")
        finish_scan(conn, first.id, status="complete", stop_reason="end_of_list")
        assert find_unfinished_scan(conn, 100) is None
        assert latest_scan(conn, 100).id == first.id
        second = create_scan(conn, 100, Backend.INSTALOADER, "default")
        assert find_unfinished_scan(conn, 100).id == second.id
        assert latest_scan(conn, 100).id == second.id
        assert find_unfinished_scan(conn, 200) is None


def test_get_scan_of_an_unknown_id_is_none(engine):
    with engine.begin() as conn:
        assert get_scan(conn, 12345) is None


def test_second_unfinished_scan_for_one_target_is_rejected(engine):
    with engine.begin() as conn:
        create_scan(conn, 100, Backend.INSTALOADER, "default")
    with pytest.raises(IntegrityError, match="uq_scans_one_unfinished_per_target"):
        with engine.begin() as conn:
            create_scan(conn, 100, Backend.INSTALOADER, "default")
    with engine.begin() as conn:
        create_scan(conn, 200, Backend.INSTALOADER, "default")
