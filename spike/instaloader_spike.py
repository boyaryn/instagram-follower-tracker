"""Throwaway spike: confirm how a follower list can be read today with Firefox's session in instaloader.

Run on the Ubuntu host, where Firefox is logged in to Instagram with the research account:

    uv run python spike/instaloader_spike.py --target <small_public_profile> \
        --user-agent "<paste navigator.userAgent from that Firefox>"

It makes at most 5 requests, 15-45 s apart, and stops at the first error without retrying:
session check, profile page, follower page 1, follower page 2, and page 2 again with a garbled
cursor. Pass --old-cursor-file spike/output/instaloader-cursor.txt on a later run (hours or days
afterwards) to also try a genuinely old cursor.

The profile and follower requests are the ones the Instagram web app itself sends (seen in
Firefox's Network panel on 2026-10-02), not instaloader's helpers: Profile.from_username() calls
api/v1/users/web_profile_info/, which the web app no longer uses and which got a 429 at once, and
get_followers() uses a GraphQL query_hash the web app does not send either. They go out through
instaloader's cookie session but bypass get_json(), which adds its own sleeps and follows
redirects with extra requests.
"""

from __future__ import annotations

import argparse
import configparser
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import _common as c

TEST_LOGIN_QUERY_HASH = "d6f4427fbe92d846298cf93df0b937d3"  # what Instaloader.test_login() sends
WEB_APP_ID = "936619743392459"  # x-ig-app-id of the Instagram web app
PROFILE_ID_PATTERN = re.compile(r'"profile_id":"(\d+)"')  # how the profile page HTML names the user id
BACKEND = "instaloader"


class SpikeError(Exception):
    """A response the spike does not accept (redirect, non-200 status, unexpected content)."""


def page_headers(user_agent: str) -> dict[str, str | None]:
    """A top-level page load. None removes instaloader's AJAX defaults, which a page load lacks."""
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Origin": None,
        "Referer": None,
        "X-Instagram-AJAX": None,
        "X-Requested-With": None,
        "x-ig-app-id": None,
        "X-CSRFToken": None,
        "Content-Length": None,
    }


def api_headers(user_agent: str, csrftoken: str, referer: str) -> dict[str, str | None]:
    """A same-origin fetch from the web app, as for the followers popup."""
    return {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Referer": referer,
        "X-CSRFToken": csrftoken,
        "x-ig-app-id": WEB_APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Origin": None,
        "X-Instagram-AJAX": None,
        "Content-Length": None,
    }


def checked(resp):
    """Accept only a plain 200: a redirect is reported, never followed."""
    if resp.is_redirect:
        raise SpikeError(f"HTTP {resp.status_code} redirect to {resp.headers.get('location')}")
    if resp.status_code != 200:
        raise SpikeError(f"HTTP {resp.status_code} {resp.reason}")
    return resp


def profile_id_from_html(html: str) -> tuple[str | None, int]:
    """The single distinct "profile_id" in the page, or None; also how many distinct values were found."""
    values = set(PROFILE_ID_PATTERN.findall(html))
    return (next(iter(values)) if len(values) == 1 else None), len(values)


def firefox_base_dirs() -> list[Path]:
    home = Path.home()
    if sys.platform.startswith("linux"):
        return [
            home / "snap/firefox/common/.mozilla/firefox",
            home / ".var/app/org.mozilla.firefox/.mozilla/firefox",
            home / ".mozilla/firefox",
        ]
    if sys.platform == "darwin":
        return [home / "Library/Application Support/Firefox"]
    return [Path(os.environ.get("APPDATA", home)) / "Mozilla/Firefox"]


def find_profile(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    candidates: list[Path] = []
    for base in firefox_base_dirs():
        ini = base / "profiles.ini"
        if not ini.is_file():
            continue
        parser = configparser.ConfigParser()
        parser.read(ini)
        default = None
        for section in parser.sections():
            if section.startswith("Install") and parser.has_option(section, "Default"):
                default = parser.get(section, "Default")
                break
        if default is None:
            for section in parser.sections():
                if parser.get(section, "Default", fallback="0") == "1" and parser.has_option(section, "Path"):
                    default = parser.get(section, "Path")
                    break
        if default:
            path = Path(default)
            candidates.append(path if path.is_absolute() else base / path)
    if len(candidates) != 1:
        found = "\n  ".join(str(p) for p in candidates) or "(none)"
        sys.exit(f"Could not pick one Firefox profile. Found:\n  {found}\nPass --firefox-profile <dir>.")
    print(f"Using Firefox profile: {candidates[0]}")
    return candidates[0]


def read_instagram_cookies(profile: Path) -> dict[str, str]:
    source = profile / "cookies.sqlite"
    if not source.is_file():
        sys.exit(f"No cookies.sqlite in {profile}")
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "cookies.sqlite"
        shutil.copy2(source, copy)  # Firefox keeps the original locked
        for suffix in ("-wal", "-shm"):
            if (profile / f"cookies.sqlite{suffix}").exists():
                shutil.copy2(profile / f"cookies.sqlite{suffix}", Path(tmp) / f"cookies.sqlite{suffix}")
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT name, value FROM moz_cookies WHERE host LIKE '%instagram.com'"
            ).fetchall()
        finally:
            con.close()
    cookies = dict(rows)
    if "sessionid" not in cookies:
        sys.exit("Firefox has no Instagram sessionid cookie: log in to Instagram in Firefox with the research account.")
    if "csrftoken" not in cookies:
        sys.exit("Firefox has a sessionid but no csrftoken cookie; open instagram.com in Firefox once and retry.")
    return cookies


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", required=True, help="a small public profile (a few hundred followers)")
    parser.add_argument("--user-agent", required=True, help="the exact user agent of the Firefox holding the session")
    parser.add_argument("--firefox-profile", help="profile directory, if discovery is ambiguous")
    parser.add_argument("--count", type=int, default=12, help="page size to request (the web app asks for 12)")
    parser.add_argument("--old-cursor-file", help="cursor saved by an earlier run, to test expiry")
    args = parser.parse_args()

    import instaloader  # imported late so --help works without it

    old_cursor: str | None = None
    old_cursor_age_h = 0.0
    if args.old_cursor_file:  # read before this run overwrites the saved cursor
        old_path = Path(args.old_cursor_file)
        old_cursor = old_path.read_text().strip()
        old_cursor_age_h = (time.time() - old_path.stat().st_mtime) / 3600

    c.install_recorder()
    cookies = read_instagram_cookies(find_profile(args.firefox_profile))
    loader = instaloader.Instaloader(
        max_connection_attempts=1,  # no retries
        user_agent=args.user_agent,
        quiet=True,
        download_pictures=False,
        download_videos=False,
        save_metadata=False,
    )
    context = loader.context
    context.load_session("", cookies)
    pacer = c.Pacer()
    findings: list[str] = [
        f"instaloader {instaloader.__version__}, page size requested: {args.count}, "
        "profile and follower requests as the web app sends them"
    ]
    cursor_page2: str | None = None
    target_pattern = re.compile(re.escape(args.target), re.IGNORECASE)
    # Short cookie values such as "1" are not secrets and would match almost any response.
    secrets = [v for v in cookies.values() if len(v) >= 8] + [args.target.lower()]

    def redact(text: str) -> str:
        """The target's username appears in the profile page URL and in redirect messages."""
        return target_pattern.sub("<target>", text)

    def step(name: str, label: str, call):
        start = len(c.EXCHANGES)
        pacer.wait(label)
        try:
            result = call()
            error = None
        except Exception as exc:  # stop at the first error, but keep what it looked like
            result, error = None, exc
        exchanges = [{**e, "url": redact(e["url"])} if "url" in e else e for e in c.requests_since(start)]
        record = {"exchanges": exchanges}
        if error is not None:
            record["exception"] = {"type": type(error).__name__, "message": redact(str(error))}
            if error.__cause__ is not None:
                record["exception"]["cause_type"] = type(error.__cause__).__name__
        c.write_fixture(BACKEND, name, [record], forbidden=secrets)
        endpoints = ", ".join(f"{e.get('method')} {c.endpoint_of(e)} -> {e.get('status', e.get('error'))}" for e in exchanges)
        findings.append(f"{label}: {len(exchanges)} HTTP request(s): {endpoints or 'none'}")
        if error is not None:
            detail = f" ({c._scrub(redact(str(error)))})" if isinstance(error, SpikeError) else ""
            findings.append(f"{label} FAILED with {type(error).__name__}{detail} (no retry)")
        return result, error

    def finish(code: int) -> int:
        c.append_findings(BACKEND, findings)
        print(f"Raw log (local only): {c.save_raw(BACKEND)}")
        return code

    data, err = step("session_check", "session check (test_login query)",
                     lambda: context.graphql_query(TEST_LOGIN_QUERY_HASH, {}))
    if err:
        return finish(1)
    user = (data.get("data") or {}).get("user")
    if not user:
        findings.append("session check returned no user: the Firefox session is not accepted")
        return finish(1)
    context.username = user["username"]
    print(f"   logged in as {context.username}")

    session = context._session  # instaloader's session holding the Firefox cookies
    target = args.target.lower()
    profile_url = f"https://www.instagram.com/{target}/"

    def profile_page():
        resp = checked(session.get(profile_url, headers=page_headers(args.user_agent), allow_redirects=False))
        if not resp.headers.get("Content-Type", "").startswith("text/html"):
            raise SpikeError(f"profile page is {resp.headers.get('Content-Type')}, not HTML")
        return resp.text

    html, err = step("profile_page", "profile page (GET /<target>/)", profile_page)
    if err:
        return finish(1)
    user_id, distinct = profile_id_from_html(html)
    findings.append(f"profile page: {len(html)} characters of HTML, {distinct} distinct \"profile_id\" value(s)")
    if user_id is None:
        findings.append("could not take the user id from the profile page: stopping")
        return finish(1)
    print(f"   user id of {target}: {user_id}")

    followers_url = f"https://www.instagram.com/api/v1/friendships/{user_id}/followers/"
    followers_headers = api_headers(args.user_agent, cookies["csrftoken"], f"{profile_url}followers/")

    def followers(max_id: str | None):
        params = {"count": args.count}
        if max_id is not None:
            params["max_id"] = max_id
        params["search_surface"] = "follow_list_page"
        data = checked(session.get(followers_url, params=params, headers=followers_headers,
                                   allow_redirects=False)).json()
        if data.get("status") != "ok":
            raise SpikeError(f"status {data.get('status')!r}")
        return data

    def describe_page(label: str, data) -> str | None:
        users = data.get("users", [])
        fields = sorted(users[0].keys()) if users else []
        flags = ", ".join(f"{k}={v}" for k, v in data.items() if isinstance(v, (bool, int)))
        findings.append(
            f"{label}: {len(users)} followers, next_max_id {c.cursor_shape(data.get('next_max_id'))}, {flags}; "
            f"top-level keys: {', '.join(sorted(data))}; user fields: {', '.join(fields)}"
        )
        return data.get("next_max_id")

    data, err = step("followers_page_1", "follower page 1", lambda: followers(None))
    if err:
        return finish(1)
    cursor_page2 = describe_page("page 1", data)
    if cursor_page2 is None:
        findings.append("the list ended after page 1; pick a target with more followers to test paging")
        return finish(0)

    data, err = step("followers_page_2", "follower page 2", lambda: followers(cursor_page2))
    if err:
        return finish(1)
    describe_page("page 2", data)

    c.OUTPUT_DIR.mkdir(mode=0o700, exist_ok=True)
    saved = c.OUTPUT_DIR / "instaloader-cursor.txt"
    fd = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(cursor_page2)

    if cursor_page2.isdigit():  # an offset such as "12": something Instagram never issues
        garbled = cursor_page2 + "x"
    else:
        garbled = cursor_page2[:-6] + "AAAAAA" if len(cursor_page2) > 6 else "garbled"
    data, err = step("garbled_cursor", "page 2 with a garbled cursor", lambda: followers(garbled))
    if data is not None:
        describe_page("garbled cursor response", data)
    # Rejecting the cursor (400, or 200 with a non-ok status) is the expected outcome; anything else stops.
    if err is not None and not (isinstance(err, SpikeError) and c.EXCHANGES[-1].get("status") in (200, 400)):
        return finish(1)

    if old_cursor is not None:
        findings.append(f"old cursor saved about {old_cursor_age_h:.1f} h ago")
        data, err = step("old_cursor", "page with an old saved cursor", lambda: followers(old_cursor))
        if data is not None:
            describe_page("old cursor response", data)
        if err:
            return finish(1)

    return finish(0)


if __name__ == "__main__":
    raise SystemExit(main())
