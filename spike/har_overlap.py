"""Throwaway spike: check whether the followers list keeps its order while Firefox scrolls through it.

Makes no requests. Reads a HAR file saved from Firefox's Network panel (right-click a request,
"Save All As HAR") after opening a target's followers popup and scrolling through a few pages:

    uv run python spike/har_overlap.py spike/output/<file>.har

For each followers request, in the order sent, it prints the cursor sent, the time since the
first request, the number of users returned and how many of them already appeared on earlier
pages. It prints no usernames, user ids, cookies or tokens. Delete the HAR afterwards: it holds
the session cookies.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FOLLOWERS_PATH = re.compile(r"/api/v1/friendships/(\d+)/followers/$")


def body_text(content: dict) -> str | None:
    text = content.get("text")
    if text is None:
        return None
    if content.get("encoding") == "base64":
        return base64.b64decode(text).decode("utf-8", errors="replace")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("har", type=Path, help="HAR file saved from Firefox's Network panel")
    args = parser.parse_args()

    entries = json.loads(args.har.read_text(encoding="utf-8"))["log"]["entries"]
    pages = []
    for entry in entries:
        url = urlsplit(entry["request"]["url"])
        match = FOLLOWERS_PATH.search(url.path)
        if entry["request"]["method"] != "GET" or not match:
            continue
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        text = body_text(entry["response"].get("content", {}))
        data = None
        if text:
            try:
                data = json.loads(text)
            except ValueError:
                pass
        pages.append({
            "target": match.group(1),
            "started": datetime.fromisoformat(entry["startedDateTime"].replace("Z", "+00:00")),
            "status": entry["response"]["status"],
            "params": params,
            "data": data,
        })
    if not pages:
        sys.exit("No GET .../api/v1/friendships/<id>/followers/ requests in this HAR.")
    pages.sort(key=lambda p: p["started"])

    targets = sorted({p["target"] for p in pages})
    if len(targets) > 1:
        print(f"Note: requests for {len(targets)} different targets; each is counted separately.\n")

    no_body = 0
    for n, target in enumerate(targets, 1):
        if len(targets) > 1:
            print(f"Target {n}:")
        print(f"{'#':>3} {'t (s)':>6} {'max_id':>7} {'count':>5} {'status':>6} {'users':>5} "
              f"{'new':>4} {'seen before':>11} {'in previous':>11} {'next_max_id':>11}  other params")
        target_pages = [p for p in pages if p["target"] == target]
        first = target_pages[0]["started"]
        seen: set[str] = set()
        previous: set[str] = set()
        total = 0
        for i, page in enumerate(target_pages):
            params = dict(page["params"])
            max_id = params.pop("max_id", "-")
            count = params.pop("count", "-")
            other = ", ".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "search_surface")
            if max_id == "-" and seen:
                print("    (popup opened again: list starts over; overlap counts continue)")
            t = (page["started"] - first).total_seconds()
            data = page["data"]
            if not isinstance(data, dict) or "users" not in data:
                no_body += 1
                previous = set()  # unknown, so the next page's "in previous" is not compared with an older page
                print(f"{i + 1:>3} {t:>6.1f} {max_id:>7} {count:>5} {page['status']:>6}  (no JSON body in the HAR)")
                continue
            ids = [str(u.get("pk") or u.get("id")) for u in data["users"]]
            current = set(ids)
            before = len(current & seen)
            in_prev = len(current & previous)
            repeats_within = len(ids) - len(current)
            print(f"{i + 1:>3} {t:>6.1f} {max_id:>7} {count:>5} {page['status']:>6} {len(ids):>5} "
                  f"{len(current - seen):>4} {before:>11} {in_prev:>11} {str(data.get('next_max_id', '-')):>11}  "
                  f"{other}{f' (repeats within page: {repeats_within})' if repeats_within else ''}")
            total += len(ids)
            seen |= current
            previous = current
        print(f"\n    users returned: {total}, distinct: {len(seen)}, duplicates: {total - len(seen)}\n")

    if no_body:
        print(f"{no_body} request(s) had no response body. In Firefox, check that "
              "devtools.netmonitor.har.includeResponseBodies is true in about:config, then save the HAR again.")
    print(f"Delete {args.har} when done: it contains your session cookies.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
