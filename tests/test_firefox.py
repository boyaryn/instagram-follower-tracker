import sqlite3
import sys
from pathlib import Path

import pytest
import responses

from igft.domain import SessionMissing
from igft.fetchers.firefox import FirefoxProfileError, find_profile, firefox_base_dirs, read_instagram_cookies

SNAP = Path("snap/firefox/common/.mozilla/firefox")
FLATPAK = Path(".var/app/org.mozilla.firefox/.mozilla/firefox")
CLASSIC = Path(".mozilla/firefox")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    return tmp_path


def make_profiles(base: Path, ini: str, *profiles: str) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / "profiles.ini").write_text(ini, encoding="utf-8")
    for name in profiles:
        (base / name).mkdir(parents=True, exist_ok=True)


INSTALL_INI = """\
[Profile1]
Name=other
IsRelative=1
Path=other.profile

[Profile0]
Name=default-release
IsRelative=1
Path=abcd.default-release
Default=1

[Install4F96D1932A9F858E]
Default=wxyz.install-default
Locked=1
"""

LEGACY_INI = """\
[Profile0]
Name=default
IsRelative=1
Path=old.default

[Profile1]
Name=default-release
IsRelative=1
Path=abcd.default-release
Default=1
"""


def test_base_dirs_on_linux_start_with_the_snap_location(home):
    assert firefox_base_dirs() == [home / SNAP, home / FLATPAK, home / CLASSIC]


def test_base_dirs_on_macos(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    assert firefox_base_dirs() == [home / "Library/Application Support/Firefox"]


def test_base_dirs_on_windows_use_appdata(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(home / "Roaming"))

    assert firefox_base_dirs() == [home / "Roaming" / "Mozilla" / "Firefox"]


def test_snap_profile_is_found_from_its_install_default(home):
    make_profiles(home / SNAP, INSTALL_INI, "wxyz.install-default", "abcd.default-release")

    assert find_profile() == home / SNAP / "wxyz.install-default"


def test_profile_marked_default_is_the_fallback_without_an_install_section(home):
    make_profiles(home / SNAP, LEGACY_INI, "abcd.default-release")

    assert find_profile() == home / SNAP / "abcd.default-release"


def test_absolute_profile_path_is_used_as_is(home, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "profile"
    make_profiles(home / CLASSIC, f"[Profile0]\nIsRelative=0\nPath={elsewhere}\nDefault=1\n")

    assert find_profile() == elsewhere


def test_classic_location_is_used_when_it_is_the_only_one(home):
    make_profiles(home / CLASSIC, LEGACY_INI)

    assert find_profile() == home / CLASSIC / "abcd.default-release"


def test_two_locations_with_a_default_each_is_ambiguous_and_lists_both(home):
    make_profiles(home / SNAP, LEGACY_INI)
    make_profiles(home / CLASSIC, LEGACY_INI)

    with pytest.raises(FirefoxProfileError) as caught:
        find_profile()

    message = caught.value.message
    assert str(home / SNAP / "abcd.default-release") in message
    assert str(home / CLASSIC / "abcd.default-release") in message
    assert "firefox_profile" in message


def test_no_profile_anywhere_lists_where_it_looked(home):
    with pytest.raises(FirefoxProfileError) as caught:
        find_profile()

    message = caught.value.message
    assert all(str(home / where) in message for where in (SNAP, FLATPAK, CLASSIC))
    assert "firefox_profile" in message


def test_profiles_ini_without_a_default_counts_as_no_profile(home):
    make_profiles(home / SNAP, "[Profile0]\nName=x\nIsRelative=1\nPath=x.profile\n")

    with pytest.raises(FirefoxProfileError):
        find_profile()


def test_configured_profile_overrides_discovery(home, tmp_path):
    make_profiles(home / SNAP, LEGACY_INI)
    make_profiles(home / CLASSIC, LEGACY_INI)
    chosen = tmp_path / "chosen"
    chosen.mkdir()

    assert find_profile(str(chosen)) == chosen


def test_configured_profile_expands_the_home_directory(home, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    (home / "chosen").mkdir()

    assert find_profile("~/chosen") == home / "chosen"


def test_configured_profile_that_is_not_a_directory_is_an_error(home):
    with pytest.raises(FirefoxProfileError) as caught:
        find_profile(str(home / "missing"))

    assert "firefox_profile" in caught.value.message


def make_cookies(profile: Path, rows: list[tuple[str, str, str, int]], *, wal: bool = False) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(profile / "cookies.sqlite")
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT, lastAccessed INTEGER)")
    connection.executemany("INSERT INTO moz_cookies VALUES (?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()


def test_instagram_cookies_are_read_and_other_sites_ignored(tmp_path):
    make_cookies(
        tmp_path,
        [
            ("sessionid", "sid", ".instagram.com", 1),
            ("csrftoken", "tok", ".instagram.com", 2),
            ("ds_user_id", "1000001", ".instagram.com", 3),
            ("sessionid", "not-ours", ".example.com", 4),
            ("csrftoken", "lookalike", ".notinstagram.com", 5),
        ],
    )

    assert read_instagram_cookies(tmp_path) == {"sessionid": "sid", "csrftoken": "tok", "ds_user_id": "1000001"}


def test_the_most_recently_used_cookie_wins_when_a_name_appears_on_two_hosts(tmp_path):
    make_cookies(
        tmp_path,
        [
            ("sessionid", "new", ".instagram.com", 9),
            ("sessionid", "old", "www.instagram.com", 1),
            ("csrftoken", "tok", ".instagram.com", 2),
        ],
    )

    assert read_instagram_cookies(tmp_path)["sessionid"] == "new"


def test_cookies_are_read_from_a_copy_so_the_original_is_untouched(tmp_path):
    make_cookies(tmp_path, [("sessionid", "sid", ".instagram.com", 1), ("csrftoken", "tok", ".instagram.com", 2)])
    before = (tmp_path / "cookies.sqlite").read_bytes()

    read_instagram_cookies(tmp_path)

    assert (tmp_path / "cookies.sqlite").read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cookies.sqlite"]


def test_cookies_still_in_the_write_ahead_log_are_read(tmp_path):
    profile = tmp_path / "profile"
    make_cookies(profile, [("csrftoken", "tok", ".instagram.com", 1)], wal=True)
    # Firefox is still running: a new cookie sits in the -wal file and the main file lacks it
    live = sqlite3.connect(profile / "cookies.sqlite")
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("INSERT INTO moz_cookies VALUES ('sessionid', 'sid', '.instagram.com', 2)")
    live.commit()
    try:
        assert (profile / "cookies.sqlite-wal").stat().st_size > 0

        assert read_instagram_cookies(profile)["sessionid"] == "sid"
    finally:
        live.close()


def test_no_sessionid_is_session_missing_with_the_restore_hint(tmp_path):
    make_cookies(tmp_path, [("csrftoken", "tok", ".instagram.com", 1)])

    with pytest.raises(SessionMissing) as caught:
        read_instagram_cookies(tmp_path)

    assert "no logged-in Instagram session" in caught.value.message
    assert "igft session import" in caught.value.message


def test_empty_sessionid_counts_as_missing(tmp_path):
    make_cookies(tmp_path, [("sessionid", "", ".instagram.com", 1), ("csrftoken", "tok", ".instagram.com", 2)])

    with pytest.raises(SessionMissing):
        read_instagram_cookies(tmp_path)


def test_no_csrftoken_is_session_missing_and_says_so(tmp_path):
    make_cookies(tmp_path, [("sessionid", "sid", ".instagram.com", 1)])

    with pytest.raises(SessionMissing) as caught:
        read_instagram_cookies(tmp_path)

    assert "csrftoken" in caught.value.message


@pytest.mark.parametrize("name", ["sessionid", "csrftoken"])
def test_a_missing_cookie_is_reported_before_any_http_request(tmp_path, name):
    present = {"sessionid", "csrftoken"} - {name}
    make_cookies(tmp_path, [(cookie, "x", ".instagram.com", 1) for cookie in present])

    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        with pytest.raises(SessionMissing):
            read_instagram_cookies(tmp_path)

    assert len(mock.calls) == 0


def test_profile_without_a_cookie_database_is_an_error(tmp_path):
    with pytest.raises(FirefoxProfileError) as caught:
        read_instagram_cookies(tmp_path)

    assert "cookies.sqlite" in caught.value.message


def test_an_unreadable_cookie_database_is_an_error(tmp_path):
    (tmp_path / "cookies.sqlite").write_bytes(b"this is not a database" * 100)

    with pytest.raises(FirefoxProfileError):
        read_instagram_cookies(tmp_path)
