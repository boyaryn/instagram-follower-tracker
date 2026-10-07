import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from dbutil import migrated_schema
from fakes import FakeFetcher, followers
from igft.cli import app
from igft.db.targets import add_target
from igft.domain import BlockSignal, FetchError, SignalKind
from test_gate import hours, put_cooldown

runner = CliRunner()

NO_DELAY = {"IGFT_DELAY_MIN_SECONDS": "0", "IGFT_DELAY_MAX_SECONDS": "0"}


def pages(*groups):
    return FakeFetcher([followers(*g) for g in groups])


@pytest.fixture
def env(cli_env):
    """CLI environment on a fresh migrated schema with the target @alice, and no delay between pages."""
    with migrated_schema() as schema:
        with schema.engine.begin() as conn:
            add_target(conn, 1001, "alice")
        yield {**cli_env, **NO_DELAY, "IGFT_DATABASE_URL": schema.url, "engine": schema.engine}


def invoke(env, *args):
    environ = {k: v for k, v in env.items() if k != "engine"}
    return runner.invoke(app, list(args), env=environ)


def rows(env, sql):
    with env["engine"].connect() as conn:
        return conn.execute(text(sql)).all()


pytestmark = pytest.mark.db


def test_a_baseline_scan_is_shown_as_a_baseline(env, use_fetcher):
    fetcher = use_fetcher(pages([1, 2], [3, 4]))

    result = invoke(env, "scan", "alice")

    assert result.exit_code == 0, result.output
    assert "complete (end of list)" in result.output
    assert "baseline" in result.output
    assert "Pages fetched: 2. Followers seen: 4." in result.output
    assert "Seen for the first time" not in result.output
    assert set(fetcher.calls) == {"fetch_followers_page"}


def test_a_later_scan_shows_pages_followers_and_those_seen_for_the_first_time(env, use_fetcher):
    use_fetcher(pages([1, 2], [3, 4]))
    assert invoke(env, "scan", "alice").exit_code == 0
    use_fetcher(pages([5, 1, 2]))

    result = invoke(env, "scan", "alice")

    assert result.exit_code == 0, result.output
    assert "Pages fetched: 1. Followers seen: 3. Seen for the first time: 1." in result.output
    assert "baseline" not in result.output


def test_a_scan_during_a_cooldown_is_refused_with_exit_3_and_no_request(env, use_fetcher):
    fetcher = use_fetcher(pages([1]))
    put_cooldown(env["engine"], started_ago=hours(3), ends_in=hours(21))

    result = invoke(env, "scan", "alice")

    assert result.exit_code == 3
    assert "cooldown" in result.output
    assert fetcher.total_calls == 0
    assert rows(env, "SELECT count(*) FROM scans") == [(0,)]


def test_a_block_signal_exits_4_records_it_and_keeps_the_scan(env, use_fetcher):
    use_fetcher(pages([1, 2], [3, 4], [5, 6]).raise_on(
        "fetch_followers_page", 3, BlockSignal(SignalKind.RATE_LIMIT, "Please wait a few minutes")
    ))

    result = invoke(env, "scan", "alice")

    assert result.exit_code == 4
    assert "rate limit" in result.output
    assert "igft resume alice" in result.output
    assert "2 pages" in result.output
    assert rows(env, "SELECT status, stop_reason, signal_type, pages_fetched FROM scans") == [
        ("unfinished", "block_signal", "rate_limit", 2)
    ]
    assert rows(env, "SELECT count(*) FROM cooldowns") == [(1,)]


def test_a_withheld_list_exits_4_says_so_and_keeps_the_scan_and_its_cursor(env, use_fetcher):
    withheld = BlockSignal(
        SignalKind.RATE_LIMIT, "HTTP 302 Found redirect to https://www.instagram.com/", withheld_list=True
    )
    use_fetcher(pages([1, 2], [3, 4]).raise_on("fetch_followers_page", 2, withheld))

    result = invoke(env, "scan", "alice")

    assert result.exit_code == 4
    assert "withholding" in result.output
    assert "Firefox" in result.output
    assert "igft resume alice" in result.output
    assert "--restart" not in result.output
    assert rows(env, "SELECT status, stop_reason, signal_type, pages_fetched, cursor FROM scans") == [
        ("unfinished", "block_signal", "rate_limit", 1, "c1")
    ]
    assert rows(env, "SELECT kind, requires_session_check FROM cooldowns") == [("rate_limit", False)]


def test_a_fetch_error_exits_5_and_names_the_resume_commands(env, use_fetcher):
    use_fetcher(pages([1], [2]).raise_on("fetch_followers_page", 2, FetchError("HTTP 500")))

    result = invoke(env, "scan", "alice")

    assert result.exit_code == 5
    assert "HTTP 500" in result.output
    assert "igft resume alice" in result.output
    assert "--restart" not in result.output
    assert rows(env, "SELECT count(*) FROM cooldowns") == [(0,)]


def test_ctrl_c_exits_5_and_names_the_resume_commands(env, use_fetcher):
    use_fetcher(pages([1], [2]).raise_on("fetch_followers_page", 2, KeyboardInterrupt()))

    result = invoke(env, "scan", "alice")

    assert result.exit_code == 5
    assert "Interrupted" in result.output
    assert "igft resume alice" in result.output
    assert rows(env, "SELECT status, stop_reason FROM scans") == [("unfinished", "interrupted")]


def test_the_page_cap_stops_with_exit_5_and_a_resume_hint(env, use_fetcher):
    fetcher = use_fetcher(pages(*[[i] for i in range(1, 6)]))

    result = invoke(env | {"IGFT_PAGE_CAP": "2"}, "scan", "alice")

    assert result.exit_code == 5
    assert "page cap" in result.output
    assert "igft resume alice" in result.output
    assert fetcher.calls["fetch_followers_page"] == 2
    assert rows(env, "SELECT status, stop_reason FROM scans") == [("unfinished", "page_cap")]


def test_an_empty_follower_list_exits_5_with_guidance_and_no_cooldown(env, use_fetcher):
    use_fetcher(pages([]))

    result = invoke(env, "scan", "alice")

    assert result.exit_code == 5
    assert "came back empty" in result.output
    assert "follows it" in result.output
    assert rows(env, "SELECT status, stop_reason FROM scans") == [("unfinished", "list_unavailable")]
    assert rows(env, "SELECT count(*) FROM cooldowns") == [(0,)]


def test_an_empty_page_after_saved_pages_exits_4_with_withheld_list_guidance(env, use_fetcher):
    use_fetcher(pages([1], [2], [3], []))

    result = invoke(env, "scan", "alice")

    assert result.exit_code == 4
    assert "withholding" in result.output
    assert "igft resume alice" in result.output
    assert "--restart" not in result.output
    assert rows(env, "SELECT status, stop_reason, signal_type, pages_fetched, cursor FROM scans") == [
        ("unfinished", "list_unavailable", "rate_limit", 3, "c3")
    ]
    assert rows(env, "SELECT kind, requires_session_check FROM cooldowns") == [("rate_limit", False)]


def test_an_unknown_target_exits_1_pointing_to_target_add_and_makes_no_request(env, use_fetcher):
    fetcher = use_fetcher(pages([1]))

    result = invoke(env, "scan", "nobody")

    assert result.exit_code == 1
    assert "igft target add nobody" in result.output
    assert fetcher.total_calls == 0


def test_a_scan_while_one_is_unfinished_points_to_resume(env, use_fetcher):
    use_fetcher(pages([1], [2]).raise_on("fetch_followers_page", 2, KeyboardInterrupt()))
    invoke(env, "scan", "alice")
    fetcher = use_fetcher(pages([1]))

    result = invoke(env, "scan", "alice")

    assert result.exit_code == 1
    assert "igft resume alice" in result.output
    assert fetcher.total_calls == 0


def test_resume_continues_the_same_scan_from_its_cursor(env, use_fetcher):
    use_fetcher(pages([1, 2], [3, 4], [5, 6]).raise_on("fetch_followers_page", 2, KeyboardInterrupt()))
    invoke(env, "scan", "alice")
    fetcher = use_fetcher(pages([1, 2], [3, 4], [5, 6]))

    result = invoke(env, "resume", "alice")

    assert result.exit_code == 0, result.output
    assert "complete" in result.output
    assert "Pages fetched: 3 (2 in this run)" in result.output
    assert fetcher.log[0] == ("fetch_followers_page", (1001, "c1"))
    assert rows(env, "SELECT count(*), max(runs) FROM scans") == [(1, 2)]


def test_resume_restart_fetches_page_one_again(env, use_fetcher):
    use_fetcher(pages([1], [2], [3]).raise_on("fetch_followers_page", 2, KeyboardInterrupt()))
    invoke(env, "scan", "alice")
    fetcher = use_fetcher(pages([1], [2], [3]))

    result = invoke(env, "resume", "alice", "--restart")

    assert result.exit_code == 0, result.output
    assert fetcher.log[0] == ("fetch_followers_page", (1001, None))
    assert rows(env, "SELECT count(*) FROM persons") == [(3,)]


def test_a_rejected_cursor_on_resume_does_not_suggest_restart(env, use_fetcher):
    use_fetcher(pages([1], [2], [3]).raise_on("fetch_followers_page", 2, KeyboardInterrupt()))
    invoke(env, "scan", "alice")
    use_fetcher(pages([1]).raise_on("fetch_followers_page", 1, FetchError("cursor rejected")))

    result = invoke(env, "resume", "alice")

    assert result.exit_code == 5
    assert "cursor rejected" in result.output
    assert "igft resume alice" in result.output
    assert "--restart" not in result.output


def test_resume_with_nothing_unfinished_exits_1_and_makes_no_request(env, use_fetcher):
    fetcher = use_fetcher(pages([1]))

    result = invoke(env, "resume", "alice")

    assert result.exit_code == 1
    assert "nothing" in result.output.lower() or "no unfinished scan" in result.output
    assert fetcher.total_calls == 0


def test_resume_is_refused_during_a_cooldown(env, use_fetcher):
    use_fetcher(pages([1], [2]).raise_on("fetch_followers_page", 2, KeyboardInterrupt()))
    invoke(env, "scan", "alice")
    fetcher = use_fetcher(pages([1]))
    put_cooldown(env["engine"], started_ago=hours(1), ends_in=hours(23))

    result = invoke(env, "resume", "alice")

    assert result.exit_code == 3
    assert fetcher.total_calls == 0


def test_a_second_command_holding_the_lock_is_refused_with_exit_3(env, use_fetcher):
    from igft.safety.lock import instagram_lock

    fetcher = use_fetcher(pages([1]))
    with instagram_lock(env["engine"]):
        result = invoke(env, "scan", "alice")

    assert result.exit_code == 3
    assert "Another igft command" in result.output
    assert fetcher.total_calls == 0


def test_scan_and_resume_options_are_documented_in_their_help(cli_env):
    scan_help = runner.invoke(app, ["scan", "--help"], env=cli_env).output
    resume_help = runner.invoke(app, ["resume", "--help"], env=cli_env).output

    assert "--full" in scan_help
    assert "--restart" in resume_help


def test_a_scan_prints_one_progress_line_per_page_to_stderr(env, use_fetcher):
    use_fetcher(pages([1, 2], [3, 4], [5]))

    result = invoke(env, "scan", "alice")

    assert result.exit_code == 0, result.output
    lines = [line for line in result.stderr.splitlines() if line.startswith("Page ")]
    assert lines == [
        "Page 1 of at most 300 this run, 2 followers saved, next request in 0 s.",
        "Page 2 of at most 300 this run, 4 followers saved, next request in 0 s.",
    ]
    assert "Page " not in result.stdout


def test_a_later_scan_progress_line_shows_the_new_followers(env, use_fetcher):
    use_fetcher(pages([1, 2]))
    assert invoke(env, "scan", "alice").exit_code == 0
    use_fetcher(pages([5, 1], [6], [1]))

    result = invoke(env, "scan", "alice")

    assert "Page 1 of at most 300 this run, 2 followers saved (1 new), next request in 0 s." in result.output
