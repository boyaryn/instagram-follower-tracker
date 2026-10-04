import pytest
from sqlalchemy import inspect
from typer.testing import CliRunner

import igft.cli
from dbutil import empty_schema
from igft.cli import app
from igft.db import tables

runner = CliRunner()


def test_help_lists_the_command_groups():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for group in ("session", "target", "db"):
        assert group in result.output
    assert "--config" in result.output


def test_groups_without_a_subcommand_show_their_help(cli_env):
    for group in ("session", "target", "db"):
        result = runner.invoke(app, [group], env=cli_env)
        assert "Usage:" in result.output


def test_invalid_config_exits_before_any_command_runs(cli_env, tmp_path, monkeypatch):
    bad = tmp_path / "bad.toml"
    bad.write_text("page_cap = 0\n")

    def must_not_run(*args, **kwargs):
        raise AssertionError("the command ran despite an invalid config")

    monkeypatch.setattr("igft.cli.db.migrate.upgrade", must_not_run)
    result = runner.invoke(app, ["--config", str(bad), "db", "upgrade"], env=cli_env)
    assert result.exit_code == 1
    assert "page_cap" in result.output


def test_missing_config_file_named_on_the_command_line_is_an_error(cli_env, tmp_path):
    result = runner.invoke(app, ["--config", str(tmp_path / "nope.toml"), "db", "upgrade"], env=cli_env)
    assert result.exit_code == 1
    assert "nope.toml" in result.output


def test_settings_are_loaded_once_per_invocation(cli_env, tmp_path, monkeypatch):
    config = tmp_path / "custom.toml"
    config.write_text('database_url = "postgresql://u@h/d"\npage_cap = 7\n')
    loaded = []
    real = igft.cli.load_settings

    def counting(path):
        loaded.append(path)
        return real(path)

    seen = []

    def stop_after_recording(settings):
        seen.append(settings)
        raise SystemExit(9)

    monkeypatch.setattr(igft.cli, "load_settings", counting)
    monkeypatch.setattr("igft.cli.db.engine_from_settings", stop_after_recording)
    runner.invoke(app, ["--config", str(config), "db", "upgrade"], env=cli_env)
    assert loaded == [config]
    assert seen[0].page_cap == 7


# --- 5.3: igft db upgrade ----------------------------------------------------------------------------------


@pytest.mark.db
def test_db_upgrade_creates_the_schema_and_a_second_run_changes_nothing(cli_env):
    with empty_schema() as schema:
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}
        first = runner.invoke(app, ["db", "upgrade"], env=env)
        assert first.exit_code == 0, first.output
        assert "revision 0001" in first.output
        assert set(tables.metadata.tables) <= set(inspect(schema.engine).get_table_names())

        second = runner.invoke(app, ["db", "upgrade"], env=env)
        assert second.exit_code == 0, second.output
        assert "already up to date" in second.output


def test_db_upgrade_without_a_database_url_says_how_to_set_it(cli_env):
    result = runner.invoke(app, ["db", "upgrade"], env=cli_env)
    assert result.exit_code == 1
    assert "IGFT_DATABASE_URL" in result.output


def test_db_upgrade_reports_an_unreachable_database_without_a_traceback(cli_env):
    env = {**cli_env, "IGFT_DATABASE_URL": "postgresql://igft:igft@127.0.0.1:1/none"}
    result = runner.invoke(app, ["db", "upgrade"], env=env)
    assert result.exit_code == 1
    assert "Could not use the database" in result.output
    assert "Traceback" not in result.output
