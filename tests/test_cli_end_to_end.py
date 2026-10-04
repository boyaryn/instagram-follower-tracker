"""End-to-end CLI runs on the test database, with a FakeFetcher in place of Instagram (tasks 14.1 and 14.2)."""

import json

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from dbutil import empty_schema, migrated_schema
from fakes import FakeFetcher, followers
from igft.cli import app
from igft.db.targets import add_target
from igft.domain import BlockSignal, ProfileInfo, SignalKind

pytestmark = pytest.mark.db

runner = CliRunner()

NO_DELAY = {"IGFT_DELAY_MIN_SECONDS": "0", "IGFT_DELAY_MAX_SECONDS": "0"}
ALICE = 1001


class Session:
    """One user's terminal: the CLI environment and the database behind it."""

    def __init__(self, cli_env, schema):
        self.env = {**cli_env, **NO_DELAY, "IGFT_DATABASE_URL": schema.url}
        self.engine = schema.engine

    def igft(self, *args):
        return runner.invoke(app, list(args), env=self.env)

    def rows(self, sql):
        with self.engine.connect() as conn:
            return conn.execute(text(sql)).all()

    def execute(self, sql):
        with self.engine.begin() as conn:
            conn.execute(text(sql))

    def end_cooldowns(self):
        """The documented manual escape hatch: edit the cooldown rows so they have ended."""
        self.execute("UPDATE cooldowns SET ends_at = now() - interval '1 second'")

    def latest_scan(self):
        return self.rows(
            "SELECT status, stop_reason, signal_type, pages_fetched, cursor, runs FROM scans ORDER BY id DESC LIMIT 1"
        )[0]


def pages(*groups):
    return FakeFetcher([followers(*g) for g in groups])


def usernames(result):
    return sorted(item["username"] for item in json.loads(result.stdout))


@pytest.fixture
def fresh(cli_env):
    """A session on an empty schema: nothing migrated yet."""
    with empty_schema() as schema:
        yield Session(cli_env, schema)


@pytest.fixture
def alice(cli_env):
    """A session on a migrated schema that already tracks @alice."""
    with migrated_schema() as schema:
        with schema.engine.begin() as conn:
            add_target(conn, ALICE, "alice")
        yield Session(cli_env, schema)


def test_the_migration_plan_then_a_rate_limit_a_refused_scan_and_a_resume(fresh, use_fetcher):
    assert fresh.igft("db", "upgrade").exit_code == 0

    fetcher = use_fetcher(FakeFetcher(profiles=[ProfileInfo(ALICE, "alice")]))
    added = fresh.igft("target", "add", "alice")
    assert added.exit_code == 0, added.output
    assert f"Added @alice (id {ALICE})." in added.output
    assert fetcher.calls["get_profile"] == 1

    use_fetcher(pages([1, 2], [3, 4]))
    baseline = fresh.igft("scan", "alice")
    assert baseline.exit_code == 0, baseline.output
    assert "baseline" in baseline.output
    assert "Pages fetched: 2. Followers seen: 4." in baseline.output

    use_fetcher(pages([5, 1, 2]))
    second = fresh.igft("scan", "alice")
    assert second.exit_code == 0, second.output
    assert "Pages fetched: 1. Followers seen: 3. Seen for the first time: 1." in second.output

    first_seen = fresh.igft("first-seen", "alice", "--format", "json")
    assert first_seen.exit_code == 0, first_seen.output
    assert usernames(first_seen) == ["user5"]
    everyone = fresh.igft("list", "alice", "--format", "json")
    assert usernames(everyone) == ["user1", "user2", "user3", "user4", "user5"]

    use_fetcher(
        pages([6, 5], [7, 1], [2, 3]).raise_on(
            "fetch_followers_page", 2, BlockSignal(SignalKind.RATE_LIMIT, "Please wait a few minutes")
        )
    )
    limited = fresh.igft("scan", "alice")
    assert limited.exit_code == 4
    assert "igft resume alice" in limited.output
    assert fresh.latest_scan() == ("unfinished", "block_signal", "rate_limit", 1, "c1", 1)
    assert fresh.rows("SELECT kind, requires_session_check FROM cooldowns") == [("rate_limit", False)]

    refused_fetcher = use_fetcher(pages([9]))
    refused = fresh.igft("scan", "alice")
    assert refused.exit_code == 3
    assert "cooldown" in refused.output
    assert refused_fetcher.total_calls == 0

    fresh.end_cooldowns()
    resumed_fetcher = use_fetcher(pages([6, 5], [7, 1], [2, 3]))
    resumed = fresh.igft("resume", "alice")
    assert resumed.exit_code == 0, resumed.output
    assert "complete (end of list)" in resumed.output
    assert "Pages fetched: 3 (2 in this run)" in resumed.output
    assert resumed_fetcher.log[0] == ("fetch_followers_page", (ALICE, "c1"))
    assert fresh.latest_scan() == ("complete", "end_of_list", "rate_limit", 3, None, 2)
    assert fresh.rows("SELECT count(*) FROM scans") == [(3,)]
    assert fresh.rows("SELECT count(*) FROM cooldowns") == [(1,)]

    final = fresh.igft("first-seen", "alice", "--format", "json")
    assert usernames(final) == ["user6", "user7"]


def test_a_challenge_holds_scanning_until_the_cooldown_has_ended_and_a_session_check_has_passed(alice, use_fetcher):
    use_fetcher(
        pages([1], [2], [3]).raise_on(
            "fetch_followers_page", 2, BlockSignal(SignalKind.CHALLENGE, "challenge_required")
        )
    )
    challenged = alice.igft("scan", "alice")
    assert challenged.exit_code == 4
    assert "igft session check" in challenged.output
    assert alice.latest_scan() == ("unfinished", "block_signal", "challenge", 1, "c1", 1)
    assert alice.rows("SELECT kind, requires_session_check FROM cooldowns") == [("challenge", True)]

    fetcher = use_fetcher(pages([1]))
    before_check = alice.igft("scan", "alice")
    assert before_check.exit_code == 3
    assert "challenge cooldown is active" in before_check.output
    assert "on hold until a session check succeeds" in before_check.output
    assert fetcher.total_calls == 0

    checker = use_fetcher(FakeFetcher(account="research_account"))
    checked = alice.igft("session", "check")
    assert checked.exit_code == 0, checked.output
    assert "@research_account" in checked.output
    assert checker.total_calls == 1
    assert alice.rows("SELECT succeeded FROM session_checks") == [(True,)]

    fetcher = use_fetcher(pages([1]))
    after_check = alice.igft("resume", "alice")
    assert after_check.exit_code == 3
    assert "challenge cooldown is active" in after_check.output
    assert "on hold until a session check succeeds" not in after_check.output
    assert fetcher.total_calls == 0

    alice.end_cooldowns()
    resumed_fetcher = use_fetcher(pages([1], [2], [3]))
    resumed = alice.igft("resume", "alice")
    assert resumed.exit_code == 0, resumed.output
    assert resumed_fetcher.log[0] == ("fetch_followers_page", (ALICE, "c1"))
    assert alice.latest_scan() == ("complete", "end_of_list", "challenge", 3, None, 2)


def test_a_rejected_session_sets_no_cooldown_so_resume_works_at_once(alice, use_fetcher):
    use_fetcher(
        pages([1], [2], [3]).raise_on(
            "fetch_followers_page", 2, BlockSignal(SignalKind.SESSION_REJECTED, "login_required")
        )
    )
    rejected = alice.igft("scan", "alice")
    assert rejected.exit_code == 4
    assert "igft session import" in rejected.output
    assert alice.latest_scan() == ("unfinished", "block_signal", "session_rejected", 1, "c1", 1)
    assert alice.rows("SELECT count(*) FROM cooldowns") == [(0,)]

    fetcher = use_fetcher(pages([1], [2], [3]))
    resumed = alice.igft("resume", "alice")

    assert resumed.exit_code == 0, resumed.output
    assert "complete (end of list)" in resumed.output
    assert fetcher.log[0] == ("fetch_followers_page", (ALICE, "c1"))
    assert alice.latest_scan() == ("complete", "end_of_list", "session_rejected", 3, None, 2)
