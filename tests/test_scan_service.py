from datetime import timedelta

import pytest
from sqlalchemy import text

from fakes import FakeFetcher, followers
from igft.db.scans import get_scan
from igft.db.targets import add_target
from igft.domain import BlockSignal, CommandRefused, FetchError, SignalKind, UnknownTarget
from igft.safety.gate import GuardedFetcher, SafetyGate
from igft.safety.pacer import Pacer
from igft.scanning.service import NothingToResume, ScanProgress, ScanService, ScanUnfinished

pytestmark = pytest.mark.db

TARGET_ID = 1001


@pytest.fixture
def engine(db_engine):
    with db_engine.begin() as conn:
        add_target(conn, TARGET_ID, "alice")
    return db_engine


def service(engine, fetcher, *, early_stop_pages=2, page_cap=300):
    guarded = GuardedFetcher(fetcher, SafetyGate(engine, timedelta(hours=24)), "scan")
    return ScanService(
        engine, guarded, Pacer(0, 0), early_stop_pages=early_stop_pages, page_cap=page_cap
    )


def pages(*groups):
    return FakeFetcher([followers(*g) for g in groups])


def scan_row(engine, scan_id=None):
    with engine.connect() as conn:
        if scan_id is None:
            scan_id = conn.execute(text("SELECT max(id) FROM scans")).scalar_one()
        return get_scan(conn, scan_id)


def count(engine, table):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def baseline(engine, *groups):
    """Complete a baseline scan over `groups` so later scans are non-baseline."""
    result = service(engine, pages(*groups)).start("alice")
    assert result.complete and result.scan.is_baseline
    return result


# --- 10.1 scan start ---------------------------------------------------------


def test_unknown_target_is_rejected_without_a_request(engine):
    fetcher = pages([1])

    with pytest.raises(UnknownTarget) as excinfo:
        service(engine, fetcher).start("nobody")

    assert "target add nobody" in excinfo.value.message
    assert fetcher.total_calls == 0
    assert count(engine, "scans") == 0


def test_an_unfinished_scan_blocks_a_new_one_and_points_to_resume(engine):
    first = pages([1], [2], [3]).raise_on("fetch_followers_page", 2, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(engine, first).start("alice")
    fetcher = pages([1])

    with pytest.raises(ScanUnfinished) as excinfo:
        service(engine, fetcher).start("alice")

    assert "igft resume alice" in excinfo.value.message
    assert "--restart" not in excinfo.value.message
    assert fetcher.total_calls == 0
    assert count(engine, "scans") == 1


def test_the_target_name_is_matched_ignoring_case_and_a_leading_at_sign(engine):
    result = service(engine, pages([1])).start("@Alice")

    assert result.scan.target_id == TARGET_ID


def test_first_scan_is_the_baseline_and_later_ones_are_not(engine):
    first = baseline(engine, [1, 2])

    second = service(engine, pages([1, 2])).start("alice")

    assert first.scan.is_baseline and not second.scan.is_baseline


# --- 10.2 the page loop ------------------------------------------------------


def test_an_interrupted_scan_keeps_five_pages_and_the_cursor_for_the_sixth(engine):
    fetcher = pages([1, 2], [3, 4], [5, 6], [7, 8], [9, 10], [11, 12], [13, 14])
    fetcher.raise_on("fetch_followers_page", 6, KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        service(engine, fetcher).start("alice")

    scan = scan_row(engine)
    assert scan.status == "unfinished"
    assert scan.stop_reason == "interrupted"
    assert scan.pages_fetched == 5
    assert scan.followers_seen == 10
    assert scan.cursor == "c5"
    assert count(engine, "persons") == 10
    assert count(engine, "follows") == 10


def test_only_page_requests_are_made(engine):
    fetcher = pages([1, 2], [3, 4], [5])

    service(engine, fetcher).start("alice")

    assert set(fetcher.calls) == {"fetch_followers_page"}
    assert fetcher.calls["fetch_followers_page"] == 3


def test_every_page_request_carries_the_targets_username(engine):
    fetcher = pages([1, 2], [3, 4], [5])

    service(engine, fetcher).start("alice")

    assert fetcher.follower_page_usernames == ["alice", "alice", "alice"]


def test_the_pacer_waits_before_every_page(engine):
    waits = []

    class CountingPacer:
        def wait(self):
            waits.append(1)

    guarded = GuardedFetcher(pages([1], [2], [3]), SafetyGate(engine, timedelta(hours=24)), "scan")
    ScanService(engine, guarded, CountingPacer(), early_stop_pages=2, page_cap=300).start("alice")

    assert len(waits) == 3


def test_progress_is_reported_after_each_page_that_does_not_end_the_run(engine):
    seen = []
    guarded = GuardedFetcher(pages([1, 2], [3, 4], [5]), SafetyGate(engine, timedelta(hours=24)), "scan")
    ScanService(
        engine, guarded, Pacer(0, 0), early_stop_pages=2, page_cap=300, on_progress=seen.append
    ).start("alice")

    assert seen == [
        ScanProgress(run_pages=1, page_limit=300, followers_saved=2, first_seen=None, next_request_in=0.0),
        ScanProgress(run_pages=2, page_limit=300, followers_saved=4, first_seen=None, next_request_in=0.0),
    ]


def test_progress_counts_followers_seen_for_the_first_time_after_the_baseline(engine):
    baseline(engine, [1, 2])
    seen = []
    guarded = GuardedFetcher(pages([3, 1], [2], [4]), SafetyGate(engine, timedelta(hours=24)), "scan")
    ScanService(
        engine, guarded, Pacer(0, 0), early_stop_pages=5, page_cap=300, on_progress=seen.append
    ).start("alice")

    assert [(p.followers_saved, p.first_seen) for p in seen] == [(2, 1), (3, 1)]


def test_no_progress_is_reported_for_the_page_that_hits_the_page_cap(engine):
    seen = []
    guarded = GuardedFetcher(pages([1], [2], [3]), SafetyGate(engine, timedelta(hours=24)), "scan")
    ScanService(
        engine, guarded, Pacer(0, 0), early_stop_pages=2, page_cap=2, on_progress=seen.append
    ).start("alice")

    assert [p.run_pages for p in seen] == [1]
    assert seen[0].page_limit == 2


def test_a_follower_repeated_within_a_page_is_stored_once(engine):
    service(engine, pages([1, 1, 2])).start("alice")

    assert count(engine, "follows") == 2
    assert scan_row(engine).followers_seen == 2


# --- 10.3 stop conditions ----------------------------------------------------


def test_baseline_reads_the_whole_list(engine):
    result = service(engine, pages([1, 2], [3, 4], [5, 6], [7, 8])).start("alice")

    assert result.scan.is_baseline
    assert (result.scan.status, result.scan.stop_reason) == ("complete", "end_of_list")
    assert result.scan.pages_fetched == 4
    assert result.scan.first_seen_followers == 8


def test_two_known_pages_in_a_row_stop_the_scan_after_the_third_page(engine):
    baseline(engine, [1, 2], [3, 4], [5, 6])
    fetcher = pages([10, 11], [1, 2], [3, 4], [5, 6])

    result = service(engine, fetcher).start("alice")

    assert (result.scan.status, result.scan.stop_reason) == ("complete", "early_stop")
    assert result.scan.pages_fetched == 3
    assert fetcher.total_calls == 3
    assert result.scan.first_seen_followers == 2


def test_known_pages_broken_by_an_unseen_follower_do_not_stop_the_scan(engine):
    baseline(engine, [1, 2], [3, 4])
    fetcher = pages([1, 2], [99], [3, 4], [98])

    result = service(engine, fetcher).start("alice")

    assert result.scan.stop_reason == "end_of_list"
    assert result.scan.pages_fetched == 4


def test_the_list_ending_first_is_end_of_list(engine):
    baseline(engine, [1, 2], [3, 4])

    result = service(engine, pages([50, 51], [1, 2])).start("alice")

    assert result.scan.stop_reason == "end_of_list"
    assert result.scan.pages_fetched == 2


def test_followers_first_stored_in_this_scan_do_not_count_as_known(engine):
    baseline(engine, [1])
    # Pages two and three repeat page one's new follower, which this scan stored itself.
    result = service(engine, pages([50], [50], [50], [51]), early_stop_pages=2).start("alice")

    assert result.scan.stop_reason == "end_of_list"
    assert result.scan.pages_fetched == 4


def test_full_scan_reads_everything_even_when_all_followers_are_known(engine):
    baseline(engine, [1, 2], [3, 4], [5, 6], [7, 8])

    result = service(engine, pages([1, 2], [3, 4], [5, 6], [7, 8])).start("alice", full=True)

    assert result.scan.mode == "full"
    assert result.scan.stop_reason == "end_of_list"
    assert result.scan.pages_fetched == 4
    assert result.scan.first_seen_followers == 0


def test_early_stop_pages_is_configurable(engine):
    baseline(engine, [1, 2], [3, 4], [5, 6])

    result = service(engine, pages([1, 2], [3, 4], [5, 6]), early_stop_pages=1).start("alice")

    assert result.scan.stop_reason == "early_stop"
    assert result.scan.pages_fetched == 1


def test_page_cap_leaves_the_scan_unfinished_with_no_extra_request(engine):
    fetcher = pages(*[[n] for n in range(1, 11)])

    result = service(engine, fetcher, page_cap=3).start("alice")

    assert fetcher.total_calls == 3
    assert (result.scan.status, result.scan.stop_reason) == ("unfinished", "page_cap")
    assert result.scan.cursor == "c3"
    assert not result.complete


def test_the_cap_reached_at_page_300_makes_no_301st_request(engine):
    fetcher = pages(*[[n] for n in range(1, 306)])

    result = service(engine, fetcher, page_cap=300).start("alice")

    assert fetcher.total_calls == 300
    assert result.scan.pages_fetched == 300
    assert (result.scan.status, result.scan.stop_reason) == ("unfinished", "page_cap")


def test_a_list_that_ends_exactly_at_the_cap_is_complete(engine):
    result = service(engine, pages([1], [2], [3]), page_cap=3).start("alice")

    assert (result.scan.status, result.scan.stop_reason) == ("complete", "end_of_list")


# --- 10.4 failures -----------------------------------------------------------


def test_a_rate_limit_on_page_10_stops_the_scan_and_keeps_pages_1_to_9(engine):
    fetcher = pages(*[[n] for n in range(1, 13)])
    fetcher.raise_on("fetch_followers_page", 10, BlockSignal(SignalKind.RATE_LIMIT, "Please wait a few minutes"))

    with pytest.raises(BlockSignal):
        service(engine, fetcher).start("alice")

    assert fetcher.total_calls == 10
    scan = scan_row(engine)
    assert (scan.status, scan.stop_reason) == ("unfinished", "block_signal")
    assert scan.pages_fetched == 9
    assert scan.cursor == "c9"
    assert scan.signal_type == "rate_limit"
    assert scan.signal_message == "Please wait a few minutes"
    assert count(engine, "cooldowns") == 1


def test_a_network_error_records_its_message_and_sets_no_cooldown(engine):
    fetcher = pages([1], [2], [3], [4])
    fetcher.raise_on("fetch_followers_page", 3, FetchError("connection reset"))

    with pytest.raises(FetchError):
        service(engine, fetcher).start("alice")

    scan = scan_row(engine)
    assert (scan.status, scan.stop_reason) == ("unfinished", "error")
    assert scan.error_message == "connection reset"
    assert scan.pages_fetched == 2
    assert count(engine, "cooldowns") == 0


def test_no_request_is_repeated_after_a_failure(engine):
    fetcher = pages([1], [2]).raise_on("fetch_followers_page", 1, FetchError("boom"))

    with pytest.raises(FetchError):
        service(engine, fetcher).start("alice")

    assert fetcher.total_calls == 1


def test_ctrl_c_records_interrupted_and_the_scan_can_be_resumed(engine):
    fetcher = pages([1], [2], [3]).raise_on("fetch_followers_page", 2, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(engine, fetcher).start("alice")

    result = service(engine, pages([1], [2], [3])).resume("alice")

    assert result.complete


def test_an_empty_first_page_of_a_baseline_is_not_the_end_of_the_list(engine):
    fetcher = pages([])

    result = service(engine, fetcher).start("alice")

    scan = result.scan
    assert (scan.status, scan.stop_reason) == ("unfinished", "list_unavailable")
    assert scan.is_baseline
    assert scan.pages_fetched == 0 and scan.cursor is None
    assert count(engine, "persons") == 0 and count(engine, "follows") == 0
    assert count(engine, "cooldowns") == 0
    assert "came back empty" in result.message and "follows it" in result.message

    later = service(engine, pages([1, 2], [3])).resume("alice")

    assert later.complete and later.scan.is_baseline
    assert later.scan.first_seen_followers == 3
    assert later.scan.runs == 2


def cooldown_rows(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT kind, requires_session_check FROM cooldowns")).all()


def test_an_empty_page_after_five_saved_pages_is_a_withheld_list_that_keeps_them_and_the_cursor(engine):
    fetcher = pages([1], [2], [3], [4], [5], [])

    with pytest.raises(BlockSignal) as excinfo:
        service(engine, fetcher).start("alice")

    assert excinfo.value.kind is SignalKind.RATE_LIMIT
    assert excinfo.value.withheld_list
    assert "5 pages" in excinfo.value.raw_message
    assert "withholding" in excinfo.value.guidance
    scan = scan_row(engine)
    assert (scan.status, scan.stop_reason) == ("unfinished", "list_unavailable")
    assert scan.pages_fetched == 5
    assert scan.cursor == "c5"
    assert (scan.signal_type, scan.signal_message) == ("rate_limit", excinfo.value.raw_message)
    assert count(engine, "follows") == 5
    assert cooldown_rows(engine) == [("rate_limit", False)]
    assert fetcher.total_calls == 6


def test_an_empty_first_page_of_a_resumed_run_after_saved_pages_is_a_withheld_list(engine):
    first = pages([1], [2], [3]).raise_on("fetch_followers_page", 3, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(engine, first).start("alice")
    assert count(engine, "cooldowns") == 0
    second = pages([1], [2], [])

    with pytest.raises(BlockSignal) as excinfo:
        service(engine, second).resume("alice")

    assert excinfo.value.withheld_list
    scan = scan_row(engine)
    assert (scan.status, scan.stop_reason, scan.runs) == ("unfinished", "list_unavailable", 2)
    assert scan.pages_fetched == 2 and scan.cursor == "c2"
    assert cooldown_rows(engine) == [("rate_limit", False)]
    assert second.calls["fetch_followers_page"] == 1


def test_an_empty_first_page_of_a_restarted_run_after_saved_pages_is_a_withheld_list(engine):
    first = pages([1], [2], [3]).raise_on("fetch_followers_page", 3, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(engine, first).start("alice")

    with pytest.raises(BlockSignal):
        service(engine, pages([])).resume("alice", restart=True)

    assert cooldown_rows(engine) == [("rate_limit", False)]
    assert scan_row(engine).stop_reason == "list_unavailable"


def test_an_empty_page_before_any_page_was_saved_sets_no_cooldown(engine):
    result = service(engine, pages([])).start("alice")

    assert result.scan.stop_reason == "list_unavailable"
    assert count(engine, "cooldowns") == 0
    assert result.scan.signal_type is None


def test_an_empty_page_before_any_page_was_saved_does_not_block_later_commands(engine):
    service(engine, pages([])).start("alice")

    guard = SafetyGate(engine, timedelta(hours=24))
    for command in ("scan", "resume", "target add"):
        guard.guard_command(command)


def test_a_withheld_list_after_saved_pages_refuses_later_commands_until_the_cooldown_ends(engine):
    with pytest.raises(BlockSignal):
        service(engine, pages([1], [])).start("alice")

    guard = SafetyGate(engine, timedelta(hours=24))
    for command in ("scan", "resume", "target add"):
        with pytest.raises(CommandRefused):
            guard.guard_command(command)
    for command in ("session check", "session import"):
        guard.guard_command(command)


# --- 10.5 resume -------------------------------------------------------------


def test_resume_with_nothing_unfinished_makes_no_request(engine):
    baseline(engine, [1])
    fetcher = pages([1])

    with pytest.raises(NothingToResume):
        service(engine, fetcher).resume("alice")

    assert fetcher.total_calls == 0


def test_resume_continues_from_the_saved_cursor_in_the_same_scan(engine):
    first = pages([1], [2], [3], [4]).raise_on("fetch_followers_page", 3, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(engine, first).start("alice")
    scan_id = scan_row(engine).id
    second = pages([1], [2], [3], [4])

    result = service(engine, second).resume("alice")

    assert second.log[0] == ("fetch_followers_page", (TARGET_ID, "c2"))
    assert second.calls["fetch_followers_page"] == 2
    assert result.scan.id == scan_id
    assert result.scan.runs == 2
    assert result.scan.pages_fetched == 4
    assert result.complete
    assert count(engine, "scans") == 1


def test_the_early_stop_count_starts_again_on_resume(engine):
    groups = ([1, 2], [3, 4], [5, 6], [7, 8])
    baseline(engine, *groups)
    first = pages(*groups).raise_on("fetch_followers_page", 2, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(engine, first).start("alice")
    second = pages(*groups)

    result = service(engine, second).resume("alice")

    assert second.calls["fetch_followers_page"] == 2
    assert (result.scan.status, result.scan.stop_reason) == ("complete", "early_stop")


def test_resume_reuses_the_scans_mode(engine):
    baseline(engine, [1, 2], [3, 4], [5, 6], [7, 8])
    first = pages([1, 2], [3, 4], [5, 6], [7, 8]).raise_on("fetch_followers_page", 2, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(engine, first).start("alice", full=True)

    result = service(engine, pages([1, 2], [3, 4], [5, 6], [7, 8])).resume("alice")

    assert result.scan.mode == "full"
    assert result.scan.stop_reason == "end_of_list"
    assert result.scan.pages_fetched == 4


def test_a_rejected_cursor_stops_as_a_fetch_error_and_keeps_the_scan(engine):
    first = pages([1], [2], [3]).raise_on("fetch_followers_page", 3, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(engine, first).start("alice")
    rejected = pages([1], [2], [3]).raise_on("fetch_followers_page", 1, FetchError("cursor rejected"))

    with pytest.raises(FetchError):
        service(engine, rejected).resume("alice")

    scan = scan_row(engine)
    assert scan.status == "unfinished"
    assert scan.cursor == "c2"
    assert scan.error_message == "cursor rejected"
    assert scan.stop_reason == "error"
    assert count(engine, "cooldowns") == 0


def test_restart_fetches_page_one_and_keeps_followers_already_saved(engine):
    first = pages([1], [2], [3], [4]).raise_on("fetch_followers_page", 3, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(engine, first).start("alice")
    second = pages([1], [2], [3], [4])

    result = service(engine, second).resume("alice", restart=True)

    assert second.log[0] == ("fetch_followers_page", (TARGET_ID, None))
    assert second.calls["fetch_followers_page"] == 4
    assert result.complete
    assert count(engine, "follows") == 4
    assert result.scan.first_seen_followers == 4


def test_a_restarted_baseline_still_reads_the_whole_list(engine):
    first = pages([1, 2], [3, 4], [5, 6], [7, 8]).raise_on("fetch_followers_page", 3, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(engine, first).start("alice")
    second = pages([1, 2], [3, 4], [5, 6], [7, 8])

    result = service(engine, second, early_stop_pages=1).resume("alice", restart=True)

    assert result.scan.is_baseline
    assert result.scan.stop_reason == "end_of_list"
    assert second.calls["fetch_followers_page"] == 4


def test_a_restart_does_not_count_the_scans_own_earlier_pages_as_known(engine):
    baseline(engine, [90])
    first = pages([1, 2], [3, 4], [5, 6]).raise_on("fetch_followers_page", 3, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        service(engine, first).start("alice")
    second = pages([1, 2], [3, 4], [5, 6])

    result = service(engine, second, early_stop_pages=1).resume("alice", restart=True)

    assert result.scan.stop_reason == "end_of_list"
    assert second.calls["fetch_followers_page"] == 3
