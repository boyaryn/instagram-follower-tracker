from datetime import timedelta

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from dbutil import migrated_schema
from fakes import FakeFetcher
from igft.cli import app
from igft.domain import BlockSignal, ProfileInfo, SignalKind
from test_gate import hours, put_cooldown

runner = CliRunner()

ALICE = ProfileInfo(pk=1001, username="alice")


def targets(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT id, username FROM targets ORDER BY id")).all()


def command_paths(group, prefix=()):
    """Every command path in a Typer/Click command tree, e.g. ("target", "add")."""
    import typer.main

    def walk(command, path):
        subcommands = getattr(command, "commands", None)
        if not subcommands:
            yield path
            return
        for name, sub in subcommands.items():
            yield from walk(sub, (*path, name))

    return list(walk(typer.main.get_command(group), prefix))


@pytest.mark.db
def test_added_reports_the_target_and_stores_it(cli_env, use_fetcher):
    fetcher = use_fetcher(FakeFetcher(profiles=[ALICE]))
    with migrated_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = runner.invoke(app, ["target", "add", "alice"], env=env)

        assert result.exit_code == 0, result.output
        assert "Added @alice (id 1001)" in result.output
        assert targets(schema.engine) == [(1001, "alice")]
        assert fetcher.total_calls == 1


@pytest.mark.db
def test_a_private_profile_is_added_like_any_other(cli_env, use_fetcher):
    fetcher = use_fetcher(FakeFetcher(profiles=[ProfileInfo(pk=7, username="private_pat")]))
    with migrated_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = runner.invoke(app, ["target", "add", "private_pat"], env=env)

        assert result.exit_code == 0, result.output
        assert targets(schema.engine) == [(7, "private_pat")]
        assert fetcher.total_calls == 1
        assert "follows it" in result.output


@pytest.mark.db
def test_unknown_profile_exits_1_and_stores_nothing(cli_env, use_fetcher):
    use_fetcher(FakeFetcher())
    with migrated_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = runner.invoke(app, ["target", "add", "ghost"], env=env)

        assert result.exit_code == 1
        assert "ghost" in result.output
        assert targets(schema.engine) == []


@pytest.mark.db
def test_adding_twice_says_it_already_exists(cli_env, use_fetcher):
    use_fetcher(FakeFetcher(profiles=[ALICE]))
    with migrated_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}
        runner.invoke(app, ["target", "add", "alice"], env=env)

        result = runner.invoke(app, ["target", "add", "alice"], env=env)

        assert result.exit_code == 0
        assert "already a target" in result.output
        assert targets(schema.engine) == [(1001, "alice")]


@pytest.mark.db
def test_a_renamed_profile_is_reported_and_updated(cli_env, use_fetcher):
    fetcher = use_fetcher(FakeFetcher(profiles=[ALICE]))
    with migrated_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}
        runner.invoke(app, ["target", "add", "alice"], env=env)
        fetcher.profiles = {"alice_new": ProfileInfo(pk=1001, username="alice_new")}

        result = runner.invoke(app, ["target", "add", "alice_new"], env=env)

        assert result.exit_code == 0
        assert "@alice" in result.output and "@alice_new" in result.output
        assert targets(schema.engine) == [(1001, "alice_new")]


@pytest.mark.db
def test_refused_during_a_cooldown_without_any_request(cli_env, use_fetcher):
    fetcher = use_fetcher(FakeFetcher(profiles=[ALICE]))
    with migrated_schema() as schema:
        put_cooldown(schema.engine, started_ago=timedelta(hours=1), ends_in=hours(23))
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = runner.invoke(app, ["target", "add", "alice"], env=env)

        assert result.exit_code == 3
        assert "cooldown" in result.output
        assert fetcher.total_calls == 0
        assert targets(schema.engine) == []


@pytest.mark.db
def test_a_rate_limit_exits_4_and_stores_no_target(cli_env, use_fetcher):
    use_fetcher(FakeFetcher(profiles=[ALICE]).raise_on("get_profile", 1, BlockSignal(SignalKind.RATE_LIMIT, "slow down")))
    with migrated_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = runner.invoke(app, ["target", "add", "alice"], env=env)

        assert result.exit_code == 4
        assert targets(schema.engine) == []


def forbid_fetcher(monkeypatch):
    def fail(settings):
        raise AssertionError("target list must not build a fetcher")

    monkeypatch.setattr("igft.cli.fetcher.create_fetcher", fail)


@pytest.mark.db
def test_list_shows_two_targets_with_their_latest_scan_and_builds_no_fetcher(cli_env, monkeypatch):
    forbid_fetcher(monkeypatch)
    with migrated_schema() as schema:
        with schema.engine.begin() as conn:
            conn.execute(text("INSERT INTO targets (id, username) VALUES (1001, 'alice'), (2002, 'bob')"))
            conn.execute(
                text(
                    "INSERT INTO scans (target_id, backend, mode, is_baseline, status, stop_reason) "
                    "VALUES (1001, 'instaloader', 'default', true, 'complete', 'end_of_list')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO scans (target_id, backend, mode, is_baseline, status) "
                    "VALUES (2002, 'instaloader', 'default', true, 'unfinished')"
                )
            )
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url, "COLUMNS": "200"}

        result = runner.invoke(app, ["target", "list"], env=env)

        assert result.exit_code == 0, result.output
        lines = {name: line for name in ("@alice", "@bob") for line in result.output.splitlines() if name in line}
        assert "1001" in lines["@alice"] and "complete (end of list)" in lines["@alice"]
        assert "2002" in lines["@bob"] and "unfinished (interrupted)" in lines["@bob"]


@pytest.mark.db
def test_list_marks_a_target_that_was_never_scanned(cli_env, monkeypatch):
    forbid_fetcher(monkeypatch)
    with migrated_schema() as schema:
        with schema.engine.begin() as conn:
            conn.execute(text("INSERT INTO targets (id, username) VALUES (1001, 'alice')"))
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url, "COLUMNS": "200"}

        result = runner.invoke(app, ["target", "list"], env=env)

        assert result.exit_code == 0
        assert "never scanned" in result.output


@pytest.mark.db
def test_list_with_no_targets_says_so(cli_env, monkeypatch):
    forbid_fetcher(monkeypatch)
    with migrated_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = runner.invoke(app, ["target", "list"], env=env)

        assert result.exit_code == 0
        assert "No targets" in result.output


def test_no_command_removes_a_target_or_its_followers():
    verbs = {"remove", "rm", "delete", "del", "drop", "purge", "forget", "clear", "reset", "prune"}
    paths = command_paths(app)

    assert ("target", "add") in paths and ("target", "list") in paths
    offenders = [p for p in paths if verbs & {part.lower() for part in p}]
    assert offenders == []
