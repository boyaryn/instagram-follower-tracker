"""Throwaway probe: what does the follower endpoint return for a private profile the research account does not follow?

Run on the Ubuntu host, where Firefox is logged in to Instagram with the research account:

    uv run python spike/private_probe.py --target <private_profile_not_followed> \
        --user-agent "<paste navigator.userAgent from that Firefox>"

It makes exactly 2 requests, 15-45 s apart, with no retries and no redirects followed: the profile
page (for the numeric id) and follower page 1. It prints a summary only (status, content type,
JSON key names, flag values, a short body preview) and saves nothing to disk.
"""

from __future__ import annotations

import argparse
import re

import _common as c
import requests
from instaloader_spike import api_headers, find_profile, page_headers, profile_id_from_html, read_instagram_cookies


def summarise(resp: requests.Response, target: str) -> None:
    hide = re.compile(re.escape(target), re.IGNORECASE)
    print(f"   HTTP {resp.status_code} {resp.reason}, Content-Type: {resp.headers.get('Content-Type')}, {len(resp.content)} bytes")
    if resp.is_redirect:
        print(f"   redirect to: {hide.sub('<target>', resp.headers.get('location', ''))}")
    try:
        data = resp.json()
    except ValueError:
        print(f"   not JSON; body preview: {hide.sub('<target>', resp.text[:300])!r}")
        return
    if not isinstance(data, dict):
        print(f"   JSON of type {type(data).__name__}")
        return
    print(f"   top-level keys: {', '.join(sorted(data))}")
    for key, value in data.items():
        if isinstance(value, (bool, int)) or key in ("status", "message", "error_type", "feedback_title", "feedback_message"):
            print(f"   {key} = {hide.sub('<target>', str(value))[:200]!r}")
        elif isinstance(value, list):
            print(f"   {key}: list of {len(value)} items")
        elif isinstance(value, dict):
            print(f"   {key}: object with keys {', '.join(sorted(value))}")
        elif isinstance(value, str):
            print(f"   {key}: string of {len(value)} characters")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", required=True, help="a private profile the research account does NOT follow")
    parser.add_argument("--user-agent", required=True, help="the exact user agent of the Firefox holding the session")
    parser.add_argument("--firefox-profile", help="profile directory, if discovery is ambiguous")
    args = parser.parse_args()

    cookies = read_instagram_cookies(find_profile(args.firefox_profile))
    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".instagram.com")
    pacer = c.Pacer()
    target = args.target.lower()
    profile_url = f"https://www.instagram.com/{target}/"

    pacer.wait("profile page (GET /<target>/)")
    resp = session.get(profile_url, headers=page_headers(args.user_agent), allow_redirects=False)
    if resp.status_code != 200:
        summarise(resp, target)
        print("profile page was not a plain 200: stopping")
        return 1
    user_id, distinct = profile_id_from_html(resp.text)
    print(f"   HTTP 200, {len(resp.text)} characters of HTML, {distinct} distinct profile_id value(s)")
    if user_id is None:
        print("could not take the user id from the profile page: stopping")
        return 1

    pacer.wait("follower page 1")
    resp = session.get(
        f"https://www.instagram.com/api/v1/friendships/{user_id}/followers/",
        params={"count": 12, "search_surface": "follow_list_page"},
        headers=api_headers(args.user_agent, cookies["csrftoken"], f"{profile_url}followers/"),
        allow_redirects=False,
    )
    summarise(resp, target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
