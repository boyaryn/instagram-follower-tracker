from datetime import timedelta

import pytest
from sqlalchemy import text

from igft.cli.errors import exit_code_for
from igft.db import safety_state
from igft.db.scans import create_scan, get_scan
from igft.db.targets import add_target
from igft.domain import Backend, BlockSignal, CommandRefused, SignalKind
from igft.safety.gate import GuardedFetcher, SafetyGate
from igft.safety.lock import instagram_lock
from igft.timeutil import format_local
from fakes import FakeFetcher, followers

pytestmark = pytest.mark.db

DEFAULT_COOLDOWN = timedelta(hours=24)
GUARDED = ["scan", "resume", "target add"]


@pytest.fixture
def engine(db_engine):
    return db_engine


@pytest.fixture
def gate(engine):
    return SafetyGate(engine, DEFAULT_COOLDOWN)


def put_cooldown(engine, *, kind="rate_limit", started_ago, ends_in, requires_check=False):
    """Insert a cooldown with explicit times relative to the database's now()."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO cooldowns (kind, command, started_at, ends_at, requires_session_check) "
                "VALUES (:kind, 'scan', now() - make_interval(secs => :ago), "
                "now() + make_interval(secs => :ends), :req)"
            ),
            {
                "kind": kind,
                "ago": started_ago.total_seconds(),
                "ends": ends_in.total_seconds(),
                "req": requires_check,
            },
        )


def put_check(engine, *, ago, succeeded=True):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO session_checks (backend, checked_at, succeeded) "
                "VALUES ('instaloader', now() - make_interval(secs => :ago), :ok)"
            ),
            {"ago": ago.total_seconds(), "ok": succeeded},
        )


def hours(n):
    return timedelta(hours=n)


# --- guard_command -----------------------------------------------------------


@pytest.mark.parametrize("command", GUARDED)
def test_everything_is_allowed_without_cooldowns(gate, command):
    gate.guard_command(command)


@pytest.mark.parametrize("command", GUARDED)
def test_an_active_cooldown_refuses_requests_and_reports_when_it_ends(engine, gate, command):
    put_cooldown(engine, started_ago=hours(3), ends_in=hours(21))
    with pytest.raises(CommandRefused) as refused:
        gate.guard_command(command)
    with engine.connect() as conn:
        ends_at = safety_state.active_cooldowns(conn)[0].ends_at
    from igft.timeutil import format_local

    assert format_local(ends_at) in str(refused.value)


@pytest.mark.parametrize("command", ["session check", "session import"])
def test_session_commands_are_allowed_during_a_cooldown_and_a_hold(engine, gate, command):
    put_cooldown(engine, kind="challenge", started_ago=hours(3), ends_in=hours(21), requires_check=True)
    gate.guard_command(command)


@pytest.mark.parametrize("command", GUARDED)
def test_an_ended_cooldown_allows_requests_again(engine, gate, command):
    put_cooldown(engine, started_ago=hours(25), ends_in=-hours(1))
    gate.guard_command(command)


def test_a_challenge_hold_with_an_ended_cooldown_names_the_missing_session_check(engine, gate):
    put_cooldown(engine, kind="challenge", started_ago=hours(25), ends_in=-hours(1), requires_check=True)
    with pytest.raises(CommandRefused) as refused:
        gate.guard_command("scan")
    assert "session check" in str(refused.value)
    assert "cooldown has ended" in str(refused.value)


def test_a_check_during_the_cooldown_does_not_lift_the_hold_until_the_cooldown_ends(engine, gate):
    put_cooldown(engine, kind="challenge", started_ago=hours(3), ends_in=hours(21), requires_check=True)
    put_check(engine, ago=hours(1))
    with pytest.raises(CommandRefused):
        gate.guard_command("scan")


def test_the_hold_lifts_when_the_cooldown_has_ended_and_a_check_succeeded_after_it_started(engine, gate):
    put_cooldown(engine, kind="challenge", started_ago=hours(30), ends_in=-hours(6), requires_check=True)
    put_check(engine, ago=hours(2))
    gate.guard_command("scan")


def test_a_check_from_before_the_challenge_does_not_lift_the_hold(engine, gate):
    put_check(engine, ago=hours(40))
    put_cooldown(engine, kind="challenge", started_ago=hours(30), ends_in=-hours(6), requires_check=True)
    with pytest.raises(CommandRefused):
        gate.guard_command("scan")


def test_a_failed_check_does_not_lift_the_hold(engine, gate):
    put_cooldown(engine, kind="challenge", started_ago=hours(30), ends_in=-hours(6), requires_check=True)
    put_check(engine, ago=hours(2), succeeded=False)
    with pytest.raises(CommandRefused):
        gate.guard_command("scan")


def test_a_rate_limit_cooldown_needs_no_session_check(engine, gate):
    put_cooldown(engine, kind="rate_limit", started_ago=hours(25), ends_in=-hours(1))
    gate.guard_command("scan")


# --- record_signal -------------------------------------------------------------


def hours_until_end(engine):
    with engine.connect() as conn:
        seconds = conn.execute(
            text("SELECT extract(epoch FROM (max(ends_at) - now())) FROM cooldowns")
        ).scalar_one()
    return seconds / 3600


def new_scan(engine):
    with engine.begin() as conn:
        add_target(conn, 100, "one")
        return create_scan(conn, 100, Backend.INSTALOADER, "default").id


def test_a_stated_wait_longer_than_the_default_sets_the_cooldown(engine, gate):
    signal = BlockSignal(SignalKind.RATE_LIMIT, "wait 48 hours", stated_wait=hours(48))
    gate.record_signal(signal, command="scan", scan_id=new_scan(engine))
    assert hours_until_end(engine) == pytest.approx(48, abs=0.05)


def test_a_stated_wait_shorter_than_the_default_does_not_shorten_the_cooldown(engine, gate):
    signal = BlockSignal(SignalKind.RATE_LIMIT, "wait 1 hour", stated_wait=hours(1))
    gate.record_signal(signal, command="scan", scan_id=new_scan(engine))
    assert hours_until_end(engine) == pytest.approx(24, abs=0.05)


def test_a_signal_without_a_stated_wait_uses_the_default_cooldown(engine, gate):
    gate.record_signal(BlockSignal(SignalKind.ACTION_BLOCK, "blocked"), command="scan", scan_id=new_scan(engine))
    assert hours_until_end(engine) == pytest.approx(24, abs=0.05)


def test_the_signal_is_saved_on_the_scan_with_its_raw_message(engine, gate):
    scan_id = new_scan(engine)
    gate.record_signal(BlockSignal(SignalKind.RATE_LIMIT, "Please wait"), command="scan", scan_id=scan_id)
    with engine.connect() as conn:
        scan = get_scan(conn, scan_id)
        cooldown = safety_state.active_cooldowns(conn)[0]
    assert scan is not None
    assert (cooldown.kind, cooldown.raw_message, cooldown.command, cooldown.source_scan_id) == (
        "rate_limit",
        "Please wait",
        "scan",
        scan_id,
    )


def test_a_challenge_creates_a_hold_that_requires_a_session_check(engine, gate):
    gate.record_signal(BlockSignal(SignalKind.CHALLENGE, "checkpoint"), command="scan", scan_id=new_scan(engine))
    with engine.connect() as conn:
        assert [c.requires_session_check for c in safety_state.active_cooldowns(conn)] == [True]


def test_a_rejected_session_creates_no_cooldown(engine, gate):
    guidance = gate.record_signal(
        BlockSignal(SignalKind.SESSION_REJECTED, "login_required"), command="scan", scan_id=new_scan(engine)
    )
    with engine.connect() as conn:
        assert safety_state.active_cooldowns(conn) == []
        assert safety_state.holds_missing_check(conn) == []
    assert "session import" in guidance


def test_a_withheld_list_sets_a_rate_limit_cooldown_without_a_hold_and_says_what_happened(engine, gate):
    signal = BlockSignal(SignalKind.RATE_LIMIT, "HTTP 302 Found redirect to https://www.instagram.com/", withheld_list=True)

    guidance = gate.record_signal(signal, command="resume", scan_id=new_scan(engine))

    with engine.connect() as conn:
        cooldowns = safety_state.active_cooldowns(conn)
        assert [(c.kind, c.requires_session_check) for c in cooldowns] == [("rate_limit", False)]
        assert safety_state.holds_missing_check(conn) == []
        ends = format_local(cooldowns[0].ends_at)
    assert "withholding" in guidance
    assert ends in guidance
    assert "Firefox" in guidance
    assert "igft resume" in guidance
    assert "--restart" not in guidance
    assert "page 1" in guidance


def test_a_plain_rate_limit_keeps_its_own_guidance(engine, gate):
    guidance = gate.record_signal(BlockSignal(SignalKind.RATE_LIMIT, "429"), command="scan", scan_id=new_scan(engine))

    assert "withholding" not in guidance


def test_the_signal_is_saved_even_when_the_page_transaction_rolls_back(engine, gate):
    scan_id = new_scan(engine)
    with pytest.raises(BlockSignal):
        with engine.begin():
            signal = BlockSignal(SignalKind.RATE_LIMIT, "slow down")
            gate.record_signal(signal, command="scan", scan_id=scan_id)
            raise signal  # the page transaction fails after the signal was recorded separately
    with engine.connect() as conn:
        assert len(safety_state.active_cooldowns(conn)) == 1


# --- GuardedFetcher ------------------------------------------------------------


def test_a_block_signal_is_recorded_once_and_carries_guidance(engine, gate):
    fetcher = FakeFetcher([followers(1), followers(2)])
    fetcher.raise_on("fetch_followers_page", 1, BlockSignal(SignalKind.RATE_LIMIT, "wait 2 hours"))
    guarded = GuardedFetcher(fetcher, gate, "scan")
    guarded.scan_id = new_scan(engine)
    with pytest.raises(BlockSignal) as raised:
        guarded.fetch_followers_page(1, None, username="one")
    assert raised.value.guidance and "igft resume" in raised.value.guidance
    with engine.connect() as conn:
        assert len(safety_state.active_cooldowns(conn)) == 1
    assert fetcher.total_calls == 1


def test_the_guarded_fetcher_passes_the_username_on(engine, gate):
    fetcher = FakeFetcher([followers(1)])

    GuardedFetcher(fetcher, gate, "scan").fetch_followers_page(1, None, username="one")

    assert fetcher.follower_page_usernames == ["one"]


def test_no_request_is_made_while_a_cooldown_is_active(engine, gate):
    put_cooldown(engine, started_ago=hours(1), ends_in=hours(23))
    fetcher = FakeFetcher([followers(1)])
    guarded = GuardedFetcher(fetcher, gate, "scan")
    with pytest.raises(CommandRefused):
        guarded.fetch_followers_page(1, None, username="one")
    with pytest.raises(CommandRefused):
        guarded.get_profile("one")
    assert fetcher.total_calls == 0


def test_a_session_check_runs_through_the_guard_during_a_hold(engine, gate):
    put_cooldown(engine, kind="challenge", started_ago=hours(3), ends_in=hours(21), requires_check=True)
    fetcher = FakeFetcher()
    guarded = GuardedFetcher(fetcher, gate, "session check")
    assert guarded.check_session() == "research_account"
    assert fetcher.calls["check_session"] == 1


# --- advisory lock -------------------------------------------------------------


def test_a_second_command_is_refused_while_the_lock_is_held(engine):
    with instagram_lock(engine):
        with pytest.raises(CommandRefused) as refused:
            with instagram_lock(engine):
                pass
    assert "another igft command is using instagram" in str(refused.value).lower()
    assert exit_code_for(refused.value) == 3


def test_the_lock_is_released_afterwards_and_after_errors(engine):
    with instagram_lock(engine):
        pass
    with pytest.raises(RuntimeError):
        with instagram_lock(engine):
            raise RuntimeError("boom")
    with instagram_lock(engine):
        pass
