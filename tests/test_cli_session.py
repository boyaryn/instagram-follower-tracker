from datetime import timedelta

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from dbutil import migrated_schema
from fakes import FakeFetcher
from igft.cli import app
from igft.domain import BlockSignal, FetchError, SignalKind
from test_gate import hours, put_cooldown

runner = CliRunner()


def rows(engine, sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).all()


def test_session_check_without_a_saved_session_exits_1_and_needs_no_database(cli_env, tmp_path):
    env = {**cli_env, "IGFT_INSTALOADER_SESSION_PATH": str(tmp_path / "missing.session")}

    result = runner.invoke(app, ["session", "check"], env=env)

    assert result.exit_code == 1
    assert "session import" in result.output


@pytest.mark.parametrize("args", [("scan", "alice"), ("resume", "alice")])
def test_scan_and_resume_without_an_imported_session_exit_1_and_make_no_request(cli_env, tmp_path, args):
    env = {**cli_env, "IGFT_INSTALOADER_SESSION_PATH": str(tmp_path / "missing.session")}

    result = runner.invoke(app, list(args), env=env)

    assert result.exit_code == 1
    assert "No saved Instagram session" in result.output
    assert "igft session import" in result.output


@pytest.mark.db
def test_check_makes_one_request_records_it_and_names_the_account(cli_env, use_fetcher):
    fetcher = use_fetcher(FakeFetcher(account="lab_account"))
    with migrated_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = runner.invoke(app, ["session", "check"], env=env)

        assert result.exit_code == 0, result.output
        assert "@lab_account" in result.output
        assert fetcher.calls["check_session"] == 1
        assert fetcher.total_calls == 1
        checks = rows(schema.engine, "SELECT backend, succeeded, account_username FROM session_checks")
        assert checks == [("instaloader", True, "lab_account")]


@pytest.mark.db
def test_failed_check_exits_non_zero_with_restore_guidance_and_is_recorded(cli_env, use_fetcher):
    fetcher = use_fetcher(FakeFetcher().raise_on("check_session", 1, FetchError("HTTP 500 from Instagram")))
    with migrated_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = runner.invoke(app, ["session", "check"], env=env)

        assert result.exit_code == 5
        assert "session import" in result.output
        assert fetcher.total_calls == 1
        checks = rows(schema.engine, "SELECT succeeded, account_username, message FROM session_checks")
        assert checks == [(False, None, "HTTP 500 from Instagram")]


@pytest.mark.db
def test_rejected_session_is_a_failed_check_with_restore_guidance_and_no_cooldown(cli_env, use_fetcher):
    use_fetcher(FakeFetcher().raise_on("check_session", 1, BlockSignal(SignalKind.SESSION_REJECTED, "login_required")))
    with migrated_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = runner.invoke(app, ["session", "check"], env=env)

        assert result.exit_code == 4
        assert "session import" in result.output
        assert rows(schema.engine, "SELECT succeeded, message FROM session_checks") == [(False, "login_required")]
        assert rows(schema.engine, "SELECT id FROM cooldowns") == []


@pytest.mark.db
def test_block_signal_during_the_check_is_recorded_as_a_cooldown_and_a_failed_check(cli_env, use_fetcher):
    use_fetcher(FakeFetcher().raise_on("check_session", 1, BlockSignal(SignalKind.RATE_LIMIT, "Please wait")))
    with migrated_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = runner.invoke(app, ["session", "check"], env=env)

        assert result.exit_code == 4
        assert rows(schema.engine, "SELECT kind, command, raw_message FROM cooldowns") == [
            ("rate_limit", "session check", "Please wait")
        ]
        assert rows(schema.engine, "SELECT succeeded, message FROM session_checks") == [(False, "Please wait")]


@pytest.mark.db
def test_check_still_runs_during_a_cooldown_and_a_challenge_hold(cli_env, use_fetcher):
    fetcher = use_fetcher(FakeFetcher())
    with migrated_schema() as schema:
        put_cooldown(
            schema.engine, kind="challenge", started_ago=timedelta(hours=1), ends_in=hours(23), requires_check=True
        )
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = runner.invoke(app, ["session", "check"], env=env)

        assert result.exit_code == 0, result.output
        assert fetcher.calls["check_session"] == 1
