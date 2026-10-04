import csv
import io
import json
import time

import pytest
from typer.testing import CliRunner

from dbutil import migrated_schema
from igft.cli import app
from igft.db.follows import upsert_follows
from igft.db.persons import upsert_persons
from igft.db.scans import create_scan, finish_scan
from igft.db.targets import add_target
from igft.domain import Backend, FollowerRecord

pytestmark = pytest.mark.db

runner = CliRunner()


@pytest.fixture(autouse=True)
def berlin_time(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def schema():
    with migrated_schema() as schema:
        with schema.engine.begin() as conn:
            add_target(conn, 100, "alice")
            add_target(conn, 200, "bob")
        yield schema


@pytest.fixture
def env(cli_env, schema):
    return {**cli_env, "IGFT_DATABASE_URL": schema.url, "COLUMNS": "200"}


def scan(schema, target_id, *pks, complete=True):
    with schema.engine.begin() as conn:
        scan_id = create_scan(conn, target_id, Backend.INSTALOADER, "default").id
        upsert_persons(conn, [FollowerRecord(pk, f"user{pk}", f"User {pk}") for pk in pks])
        upsert_follows(conn, target_id, list(pks), scan_id)
        if complete:
            finish_scan(conn, scan_id, status="complete", stop_reason="end_of_list")
    return scan_id


def invoke(env, *args):
    return runner.invoke(app, list(args), env=env)


def test_list_prints_a_table_of_the_recorded_followers(schema, env):
    scan(schema, 100, 1, 2)

    result = invoke(env, "list", "alice")

    assert result.exit_code == 0, result.output
    assert "user1" in result.stdout and "user2" in result.stdout
    assert "Username" in result.stdout


def test_list_as_json_is_an_array_of_objects(schema, env):
    scan(schema, 100, 1, 2)

    result = invoke(env, "list", "alice", "--format", "json")

    assert result.exit_code == 0, result.output
    assert sorted(item["username"] for item in json.loads(result.stdout)) == ["user1", "user2"]


def test_list_as_csv_has_a_header_and_one_line_per_follower(schema, env):
    scan(schema, 100, 1, 2)

    result = invoke(env, "list", "alice", "--format", "csv")

    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows[0][:2] == ["id", "username"]
    assert len(rows) == 3


def test_the_format_name_is_not_case_sensitive(schema, env):
    scan(schema, 100, 1)

    result = invoke(env, "list", "alice", "--format", "JSON")

    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)) == 1


def test_list_of_a_target_without_followers_says_so(schema, env):
    result = invoke(env, "list", "alice")

    assert result.exit_code == 0
    assert "No followers to show." in result.stdout


def test_an_unknown_format_is_a_usage_error_naming_the_valid_ones(schema, env):
    result = invoke(env, "list", "alice", "--format", "xml")

    assert result.exit_code == 2
    for name in ("table", "csv", "json"):
        assert name in result.stderr


@pytest.mark.parametrize("command", [["list"], ["first-seen"]])
def test_an_unknown_target_exits_1_and_says_how_to_add_it(schema, env, command):
    result = invoke(env, *command, "nobody")

    assert result.exit_code == 1
    assert "igft target add nobody" in result.stderr
    assert result.stdout == ""


def test_first_seen_shows_the_followers_of_the_latest_scan(schema, env):
    scan(schema, 100, 1, 2)
    scan(schema, 100, 1, 2, 3)

    result = invoke(env, "first-seen", "alice", "--format", "json")

    assert result.exit_code == 0, result.output
    assert [item["username"] for item in json.loads(result.stdout)] == ["user3"]


def test_first_seen_after_only_a_baseline_is_empty_and_explains_why_on_stderr(schema, env):
    scan(schema, 100, 1, 2)

    result = invoke(env, "first-seen", "alice", "--format", "json")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
    assert "baseline" in result.stderr


def test_first_seen_notes_do_not_break_csv_on_stdout(schema, env):
    scan(schema, 100, 1, 2)

    result = invoke(env, "first-seen", "alice", "--format", "csv")

    assert result.stdout.strip() == "id,username,full_name,first_seen_at,tags,is_marked"
    assert "baseline" in result.stderr


def test_first_seen_warns_when_the_latest_scan_is_not_complete(schema, env):
    scan(schema, 100, 1, 2)
    scan(schema, 100, 1, 2, 3, complete=False)

    result = invoke(env, "first-seen", "alice", "--format", "json")

    assert result.exit_code == 0
    assert [item["username"] for item in json.loads(result.stdout)] == ["user3"]
    assert "not complete" in result.stderr


def test_first_seen_for_a_never_scanned_target_says_so(schema, env):
    result = invoke(env, "first-seen", "alice", "--format", "json")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
    assert "not been scanned" in result.stderr


def test_first_seen_since_scan_shows_followers_first_seen_after_that_scan(schema, env):
    first = scan(schema, 100, 1)
    scan(schema, 100, 1, 2)
    scan(schema, 100, 1, 2, 3)

    result = invoke(env, "first-seen", "alice", "--since-scan", str(first), "--format", "json")

    assert result.exit_code == 0, result.output
    assert sorted(item["username"] for item in json.loads(result.stdout)) == ["user2", "user3"]


def test_first_seen_since_scan_of_another_target_is_an_error(schema, env):
    other = scan(schema, 200, 9)

    result = invoke(env, "first-seen", "alice", "--since-scan", str(other))

    assert result.exit_code == 1
    assert f"Scan {other} is not a scan of @alice" in result.stderr


def test_first_seen_since_an_unknown_scan_is_an_error(schema, env):
    scan(schema, 100, 1)

    result = invoke(env, "first-seen", "alice", "--since-scan", "9999")

    assert result.exit_code == 1
    assert "9999" in result.stderr


def test_since_and_since_scan_together_are_a_usage_error(schema, env):
    scan(schema, 100, 1)

    result = invoke(env, "first-seen", "alice", "--since", "2026-01-01", "--since-scan", "1")

    assert result.exit_code == 2
    assert "--since" in result.stderr and "--since-scan" in result.stderr


def test_a_malformed_date_is_a_usage_error(schema, env):
    result = invoke(env, "first-seen", "alice", "--since", "yesterday")

    assert result.exit_code == 2


def test_since_counts_from_local_midnight_and_leaves_out_the_baseline(schema, env):
    scan(schema, 100, 1)
    later = scan(schema, 100, 1, 2)
    with schema.engine.begin() as conn:
        # 23:30 UTC on 14 Sep is 01:30 on 15 Sep in Berlin (CEST): on or after local 15 Sep.
        conn.exec_driver_sql(
            "UPDATE follows SET first_seen_at = '2026-09-14 23:30:00+00' WHERE person_id = 2 AND first_seen_scan_id = %s",
            (later,),
        )

    on_the_15th = invoke(env, "first-seen", "alice", "--since", "2026-09-15", "--format", "json")
    on_the_16th = invoke(env, "first-seen", "alice", "--since", "2026-09-16", "--format", "json")

    assert [item["username"] for item in json.loads(on_the_15th.stdout)] == ["user2"]
    assert json.loads(on_the_16th.stdout) == []
