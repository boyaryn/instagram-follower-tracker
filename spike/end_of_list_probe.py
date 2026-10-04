"""Throwaway probe: what does the last page of a follower list look like?

Run on the Ubuntu host, where Firefox is logged in to Instagram with the research account:

    uv run python spike/end_of_list_probe.py --target <small_target_the_account_can_see> \
        --user-agent "<paste navigator.userAgent from that Firefox>"

Pick a target with roughly 15-60 followers (2-5 pages of 12) that the research account follows or that is public.
It makes 1 profile request plus one request per follower page, 15-45 s apart, with no retries and no redirects
followed. It stops at the first of: a page with no next cursor, a page with has_more false, an empty page, any
non-200 answer, or --max-pages pages. It prints the shape of every page (counts, flags, cursor shape) and saves
nothing to disk.
"""

from __future__ import annotations

import argparse

import _common as c
import requests
from instaloader_spike import api_headers, find_profile, page_headers, profile_id_from_html, read_instagram_cookies

FLAGS = ("has_more", "big_list", "page_size", "use_clickable_see_more", "should_limit_list_of_followers")


def describe(page_no: int, resp: requests.Response) -> tuple[int | None, str | None, object]:
    """Print one page's shape; return (follower count, next_max_id, has_more), count None if not usable."""
    print(f"   page {page_no}: HTTP {resp.status_code}, {len(resp.content)} bytes")
    try:
        data = resp.json()
    except ValueError:
        print("   not JSON")
        return None, None, None
    if not isinstance(data, dict):
        print(f"   JSON of type {type(data).__name__}")
        return None, None, None
    users = data.get("users")
    count = len(users) if isinstance(users, list) else None
    cursor = data.get("next_max_id")
    flags = ", ".join(f"{k}={data[k]!r}" if k in data else f"{k}=<absent>" for k in FLAGS)
    print(f"   status={data.get('status')!r}, followers={count}, next_max_id={c.cursor_shape(cursor) if cursor is not None else '<absent>'}")
    print(f"   {flags}")
    print(f"   top-level keys: {', '.join(sorted(data))}")
    return count, cursor, data.get("has_more")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", required=True, help="a small profile whose follower list the account can see")
    parser.add_argument("--user-agent", required=True, help="the exact user agent of the Firefox holding the session")
    parser.add_argument("--firefox-profile", help="profile directory, if discovery is ambiguous")
    parser.add_argument("--max-pages", type=int, default=8, help="hard cap on follower pages requested (default 8)")
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
        print(f"   HTTP {resp.status_code}: profile page was not a plain 200, stopping")
        return 1
    user_id, distinct = profile_id_from_html(resp.text)
    print(f"   HTTP 200, {distinct} distinct profile_id value(s)")
    if user_id is None:
        print("could not take the user id from the profile page: stopping")
        return 1

    cursor: str | None = None
    total = 0
    for page_no in range(1, args.max_pages + 1):
        pacer.wait(f"follower page {page_no}")
        params = {"count": 12, "search_surface": "follow_list_page"}
        if cursor is not None:
            params["max_id"] = cursor
        resp = session.get(
            f"https://www.instagram.com/api/v1/friendships/{user_id}/followers/",
            params=params,
            headers=api_headers(args.user_agent, cookies["csrftoken"], f"{profile_url}followers/"),
            allow_redirects=False,
        )
        count, cursor, has_more = describe(page_no, resp)
        if resp.status_code != 200 or count is None:
            print("unusable answer: stopping")
            return 1
        total += count
        if count == 0:
            print(f"EMPTY PAGE at page {page_no} (after {total} followers): stopping")
            return 0
        if cursor is None or has_more is False:
            print(f"END REACHED at page {page_no}: {total} followers in total, last page had {count}")
            return 0
    print(f"page cap reached after {total} followers without seeing the end; use a smaller target or a higher --max-pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
