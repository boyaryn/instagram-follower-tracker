"""Finding the Firefox profile that holds the research account's Instagram login, and reading its cookies."""

from __future__ import annotations

import configparser
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from igft.domain import Backend, IgftError, SessionMissing

_COOKIES_FILE = "cookies.sqlite"


class FirefoxProfileError(IgftError):
    """No single Firefox profile could be chosen, or the chosen one has no cookie database."""


def firefox_base_dirs() -> list[Path]:
    """Where Firefox keeps `profiles.ini` on this platform, most specific install first."""
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Firefox"]
    if sys.platform in ("win32", "cygwin"):
        return [Path(os.environ.get("APPDATA") or home) / "Mozilla" / "Firefox"]
    return [
        home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
        home / ".mozilla" / "firefox",
    ]


def _default_profile(base: Path) -> Path | None:
    """The default profile `profiles.ini` in `base` names: the `Install*` default, else the one marked `Default=1`."""
    ini = base / "profiles.ini"
    if not ini.is_file():
        return None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(ini, encoding="utf-8")
    except configparser.Error:
        return None
    path: str | None = None
    for section in parser.sections():
        if section.startswith("Install") and parser.get(section, "Default", fallback=""):
            path = parser.get(section, "Default")
            break
    if path is None:
        for section in parser.sections():
            if parser.get(section, "Default", fallback="0") == "1" and parser.get(section, "Path", fallback=""):
                path = parser.get(section, "Path")
                relative = parser.get(section, "IsRelative", fallback="1") == "1"
                return base / path if relative else Path(path)
    if path is None:
        return None
    profile = Path(path)
    return profile if profile.is_absolute() else base / profile


def find_profile(configured: Path | str | None = None) -> Path:
    """The Firefox profile directory to read cookies from.

    `configured` (the `firefox_profile` setting) wins without any discovery. Otherwise exactly one default profile
    must be found across the known locations; none or several stops with the candidates listed.
    """
    if configured:
        profile = Path(configured).expanduser()
        if not profile.is_dir():
            raise FirefoxProfileError(f"The configured firefox_profile {str(profile)!r} is not a directory.")
        return profile
    found = [(base, _default_profile(base)) for base in firefox_base_dirs()]
    candidates = [profile for _, profile in found if profile is not None]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        listing = "\n".join(f"  {profile}" for profile in candidates)
        problem = f"Found more than one default Firefox profile:\n{listing}"
    else:
        searched = "\n".join(f"  {base}" for base, _ in found)
        problem = f"Found no default Firefox profile. Looked for profiles.ini in:\n{searched}"
    raise FirefoxProfileError(f"{problem}\nSet firefox_profile in the config file to the profile directory to use.")


def read_instagram_cookies(profile: Path) -> dict[str, str]:
    """Instagram's cookies from a copy of the profile's cookie database (Firefox keeps the original locked).

    Raises `SessionMissing` unless both `sessionid` and `csrftoken` are present, before any request is made.
    """
    source = profile / _COOKIES_FILE
    if not source.is_file():
        raise FirefoxProfileError(f"There is no {_COOKIES_FILE} in the Firefox profile {str(profile)!r}.")
    with tempfile.TemporaryDirectory(prefix="igft-cookies-") as tmp:
        copy = Path(tmp) / _COOKIES_FILE
        for suffix in ("", "-wal", "-shm"):  # the write-ahead log holds the newest cookies
            part = profile / f"{_COOKIES_FILE}{suffix}"
            if part.exists():
                shutil.copy2(part, Path(tmp) / part.name)
        connection = sqlite3.connect(copy)
        try:
            rows = connection.execute(
                "SELECT name, value FROM moz_cookies"
                " WHERE host = 'instagram.com' OR host LIKE '%.instagram.com'"
                " ORDER BY lastAccessed"
            ).fetchall()
        except sqlite3.Error as exc:
            raise FirefoxProfileError(f"Could not read the cookies in {str(profile)!r}: {exc}") from exc
        finally:
            connection.close()
    cookies = {name: value for name, value in rows if value}
    if "sessionid" not in cookies:
        raise SessionMissing(Backend.INSTALOADER, "Firefox has no logged-in Instagram session.")
    if "csrftoken" not in cookies:
        raise SessionMissing(
            Backend.INSTALOADER,
            "Firefox has an Instagram session but no csrftoken cookie; open instagram.com in Firefox once.",
        )
    return cookies
