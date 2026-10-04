import sqlite3
import stat

import pytest
import responses
from sqlalchemy import text
from typer.testing import CliRunner

from dbutil import migrated_schema
from igft.cli import app
from igft.config import load_settings
from igft.fetchers.instaloader_adapter import InstaloaderFetcher
from test_gate import hours, put_cooldown

runner = CliRunner()
SESSION_CHECK = "https://www.instagram.com/graphql/query"
UA = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"


def firefox_profile(tmp_path, cookies, name="firefox-profile"):
    profile = tmp_path / name
    profile.mkdir()
    connection = sqlite3.connect(profile / "cookies.sqlite")
    connection.execute("CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT, lastAccessed INTEGER)")
    connection.executemany(
        "INSERT INTO moz_cookies VALUES (?, ?, ?, ?)",
        [(name, value, host, i) for i, (name, value, host) in enumerate(cookies)],
    )
    connection.commit()
    connection.close()
    return profile


def logged_in(sessionid="sid-1"):
    return [
        ("sessionid", sessionid, ".instagram.com"),
        ("csrftoken", "tok", ".instagram.com"),
        ("ds_user_id", "1000000", ".instagram.com"),
    ]


@pytest.fixture
def env(cli_env, tmp_path):
    return {
        **cli_env,
        "IGFT_INSTALOADER_SESSION_PATH": str(tmp_path / "state" / "instaloader.session"),
        "IGFT_INSTALOADER_USER_AGENT": UA,
    }


def saved_cookies(env):
    settings = load_settings(env["IGFT_CONFIG"], env=env)
    return InstaloaderFetcher.from_settings(settings)._context._session.cookies.get_dict()


def accept_session(mock, username="lab_account"):
    mock.add(responses.GET, SESSION_CHECK, json={"data": {"user": {"username": username}}, "status": "ok"})


def test_no_instagram_session_in_firefox_exits_1_and_saves_nothing(env, tmp_path):
    profile = firefox_profile(tmp_path, [("sessionid", "other", ".example.com")])
    env = {**env, "IGFT_FIREFOX_PROFILE": str(profile)}

    with responses.RequestsMock() as mock:
        result = runner.invoke(app, ["session", "import"], env=env)
        assert len(mock.calls) == 0

    assert result.exit_code == 1
    assert "Log in to Instagram in Firefox" in result.output
    assert not (tmp_path / "state").exists()


def test_a_firefox_profile_that_does_not_exist_exits_1(env, tmp_path):
    env = {**env, "IGFT_FIREFOX_PROFILE": str(tmp_path / "nowhere")}

    result = runner.invoke(app, ["session", "import"], env=env)

    assert result.exit_code == 1
    assert "firefox_profile" in result.output


@pytest.mark.db
def test_import_checks_the_session_once_saves_it_privately_and_names_the_account(env, tmp_path):
    env = {**env, "IGFT_FIREFOX_PROFILE": str(firefox_profile(tmp_path, logged_in()))}
    with migrated_schema() as schema, responses.RequestsMock() as mock:
        env["IGFT_DATABASE_URL"] = schema.url
        accept_session(mock)

        result = runner.invoke(app, ["session", "import"], env=env)

        assert result.exit_code == 0, result.output
        assert "@lab_account" in result.output
        assert len(mock.calls) == 1
        assert mock.calls[0].request.headers["User-Agent"] == UA
        assert mock.calls[0].request.headers["X-CSRFToken"] == "tok"
        with schema.engine.connect() as conn:
            checks = conn.execute(text("SELECT backend, succeeded, account_username FROM session_checks")).all()
        assert checks == [("instaloader", True, "lab_account")]
    path = tmp_path / "state" / "instaloader.session"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert saved_cookies(env)["sessionid"] == "sid-1"


@pytest.mark.db
def test_a_second_import_replaces_the_saved_session(env, tmp_path):
    first = firefox_profile(tmp_path, logged_in("sid-old"), name="first")
    second = firefox_profile(tmp_path, logged_in("sid-new"), name="second")
    with migrated_schema() as schema, responses.RequestsMock() as mock:
        env["IGFT_DATABASE_URL"] = schema.url
        accept_session(mock)
        accept_session(mock)

        runner.invoke(app, ["session", "import"], env={**env, "IGFT_FIREFOX_PROFILE": str(first)})
        assert saved_cookies(env)["sessionid"] == "sid-old"
        result = runner.invoke(app, ["session", "import"], env={**env, "IGFT_FIREFOX_PROFILE": str(second)})

        assert result.exit_code == 0, result.output
    assert saved_cookies(env)["sessionid"] == "sid-new"


@pytest.mark.db
def test_a_session_instagram_rejects_is_not_saved_and_the_old_one_stays(env, tmp_path):
    path = tmp_path / "state" / "instaloader.session"
    old = InstaloaderFetcher.from_cookies({"sessionid": "sid-old", "csrftoken": "tok"}, UA).session_bytes()
    path.parent.mkdir()
    path.write_bytes(old)
    env = {**env, "IGFT_FIREFOX_PROFILE": str(firefox_profile(tmp_path, logged_in("sid-new")))}
    with migrated_schema() as schema, responses.RequestsMock() as mock:
        env["IGFT_DATABASE_URL"] = schema.url
        mock.add(responses.GET, SESSION_CHECK, status=400, json={"message": "login_required", "status": "fail"})

        result = runner.invoke(app, ["session", "import"], env=env)

        assert result.exit_code == 4
        assert "session import" in result.output
        assert len(mock.calls) == 1
        with schema.engine.connect() as conn:
            assert conn.execute(text("SELECT succeeded FROM session_checks")).all() == [(False,)]
            assert conn.execute(text("SELECT count(*) FROM cooldowns")).scalar_one() == 0
    assert path.read_bytes() == old


@pytest.mark.db
def test_a_challenge_on_import_is_recorded_as_a_hold_and_nothing_is_saved(env, tmp_path):
    env = {**env, "IGFT_FIREFOX_PROFILE": str(firefox_profile(tmp_path, logged_in()))}
    with migrated_schema() as schema, responses.RequestsMock() as mock:
        env["IGFT_DATABASE_URL"] = schema.url
        mock.add(responses.GET, SESSION_CHECK, status=400, json={"message": "challenge_required"})

        result = runner.invoke(app, ["session", "import"], env=env)

        assert result.exit_code == 4
        with schema.engine.connect() as conn:
            assert conn.execute(text("SELECT kind, requires_session_check FROM cooldowns")).all() == [("challenge", True)]
    assert not (tmp_path / "state" / "instaloader.session").exists()


@pytest.mark.db
def test_import_is_allowed_during_a_cooldown(env, tmp_path):
    env = {**env, "IGFT_FIREFOX_PROFILE": str(firefox_profile(tmp_path, logged_in()))}
    with migrated_schema() as schema, responses.RequestsMock() as mock:
        env["IGFT_DATABASE_URL"] = schema.url
        put_cooldown(schema.engine, kind="challenge", started_ago=hours(3), ends_in=hours(21), requires_check=True)
        accept_session(mock)

        result = runner.invoke(app, ["session", "import"], env=env)

        assert result.exit_code == 0, result.output
        assert len(mock.calls) == 1
