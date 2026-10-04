"""Shared helpers for the throwaway backend spikes. Not part of the igft package.

Safety rules every spike follows:
- one HTTP request per step, and at least 15-45 s (random) between requests;
- no retries: the first error ends the run;
- raw responses go to spike/output/ (git-ignored); only sanitised copies go to tests/fixtures/.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SPIKE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SPIKE_DIR / "output"
FINDINGS = SPIKE_DIR / "findings.md"
FIXTURES_DIR = SPIKE_DIR.parent / "tests" / "fixtures"

MIN_DELAY_S = 15.0
MAX_DELAY_S = 45.0

# Every HTTP exchange made through requests in this process, in order.
EXCHANGES: list[dict] = []
_original_send = requests.Session.send


def _recording_send(self, request, **kwargs):
    started = time.monotonic()
    try:
        response = _original_send(self, request, **kwargs)
    except Exception as exc:  # network failure: record and re-raise, never retry
        EXCHANGES.append({"method": request.method, "url": request.url, "error": repr(exc)})
        raise
    try:
        body = response.json()
    except ValueError:
        body = response.text[:2000]
    EXCHANGES.append(
        {
            "method": request.method,
            "url": request.url,
            "status": response.status_code,
            "elapsed_s": round(time.monotonic() - started, 2),
            "content_type": response.headers.get("Content-Type"),
            "body": body,
        }
    )
    return response


def install_recorder() -> None:
    """Record every request made by either library (both use requests)."""
    requests.Session.send = _recording_send


class Pacer:
    """Waits a random 15-45 s between request starts; the first request does not wait."""

    def __init__(self) -> None:
        self._last_start: float | None = None

    def wait(self, label: str) -> None:
        if self._last_start is not None:
            target = self._last_start + random.uniform(MIN_DELAY_S, MAX_DELAY_S)
            remaining = target - time.monotonic()
            if remaining > 0:
                print(f"  waiting {remaining:.0f} s before: {label}", flush=True)
                time.sleep(remaining)
        print(f"-> {label}", flush=True)
        self._last_start = time.monotonic()


def requests_since(index: int) -> list[dict]:
    return EXCHANGES[index:]


# --- sanitising -------------------------------------------------------------

# String values under these keys are kept: they describe errors and status, not people.
_KEEP_STRING_KEYS = {
    "status",
    "message",
    "error_type",
    "error_title",
    "error_body",
    "feedback_title",
    "feedback_message",
    "feedback_action",
    "feedback_appeal_label",
    "feedback_ignore_label",
    "category",
    "__typename",
    "lock",
    "logout_reason",
    "content_type",
    "method",
    "type",
    "cause_type",
    "error",
}
_ID_KEYS = {"id", "pk", "pk_id", "strong_id__", "fbid", "fbid_v2", "interop_messaging_user_fbid", "user_id"}
_CURSOR_KEYS = {"end_cursor", "next_max_id", "max_id", "after", "big_list"}
# Numbers that describe a person's activity (unix times): a non-zero value becomes a fixed one.
_ACTIVITY_TIME_KEYS = {"latest_reel_media"}
_FIXED_TIME = 1_700_000_000
_SECRET_PATTERN = re.compile(r"sessionid|IGT:2:|Bearer ", re.IGNORECASE)


class Sanitiser:
    """Replaces personal data with stable placeholders while keeping the response shape."""

    def __init__(self) -> None:
        self._ids: dict[str, str] = {}

    def _fake_id(self, value: str) -> str:
        if value not in self._ids:
            self._ids[value] = str(1_000_000 + len(self._ids))
        return self._ids[value]

    def clean(self, value, key: str | None = None):
        if isinstance(value, dict):
            return {k: self.clean(v, k) for k, v in value.items() if k.lower() not in {"cookie", "set-cookie"}}
        if isinstance(value, list):
            return [self.clean(v, key) for v in value]
        if isinstance(value, bool) or value is None:
            return value
        if key in _ID_KEYS and isinstance(value, (int, str)) and str(value).isdigit():
            fake = self._fake_id(str(value))
            return int(fake) if isinstance(value, int) else fake
        if key in _ACTIVITY_TIME_KEYS and isinstance(value, int):
            return _FIXED_TIME if value else 0
        if isinstance(value, (int, float)):
            return value
        if key in _CURSOR_KEYS:
            return f"<cursor:{cursor_shape(str(value))}>"
        if key == "url":
            return re.sub(r"\?.*$", "?<query redacted>", re.sub(r"/\d{3,}/", "/<id>/", str(value)))
        if key in _KEEP_STRING_KEYS:
            return _scrub(str(value))
        return f"<{key or 'value'}>"


def _scrub(text: str) -> str:
    """Strip query strings and long numbers (user ids) from free text such as error messages."""
    text = re.sub(r"\?[^\s\"']*", "?<query>", text)
    return re.sub(r"\d{5,}", "<n>", text)


def cursor_shape(cursor: str | None) -> str:
    """Describe a cursor without revealing it: length, character classes and a 4-character prefix."""
    if cursor is None:
        return "none"
    classes = []
    if re.search(r"[0-9]", cursor):
        classes.append("digits")
    if re.search(r"[A-Za-z]", cursor):
        classes.append("letters")
    other = sorted(set(re.sub(r"[0-9A-Za-z]", "", cursor)))
    if other:
        classes.append("symbols " + "".join(other))
    return f"len={len(cursor)} {'+'.join(classes) or 'empty'} prefix={cursor[:4]!r}"


def write_fixture(backend: str, name: str, exchanges: list[dict], forbidden: list[str] = ()) -> Path:
    """Save sanitised exchanges as an adapter test fixture, refusing if a secret slipped through."""
    sanitiser = Sanitiser()
    data = sanitiser.clean(exchanges)
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if _SECRET_PATTERN.search(text):
        sys.exit(f"refusing to write fixture {name}: it still contains a session token")
    for secret in forbidden:
        if secret and secret in text:
            sys.exit(f"refusing to write fixture {name}: it contains a value the user typed")
    path = FIXTURES_DIR / backend / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    print(f"   fixture: {path.relative_to(SPIKE_DIR.parent)}")
    return path


def save_raw(backend: str) -> Path:
    """Keep the unsanitised log locally (git-ignored, owner-only) for your own reference."""
    OUTPUT_DIR.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUTPUT_DIR / f"{backend}-raw-{stamp}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(EXCHANGES, f, indent=2, ensure_ascii=False, default=str)
    return path


def append_findings(backend: str, lines: list[str]) -> None:
    """Append an automatically measured block to spike/findings.md. Contains no personal data."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    block = ["", f"### Measured: {backend} ({stamp})", ""] + [f"- {line}" for line in lines] + [""]
    with FINDINGS.open("a", encoding="utf-8") as f:
        f.write("\n".join(block))
    print(f"\nAppended measurements to {FINDINGS.relative_to(SPIKE_DIR.parent)}")


def endpoint_of(exchange: dict) -> str:
    url = re.sub(r"\?.*$", "", exchange.get("url", ""))
    return re.sub(r"/\d{3,}/", "/<user_id>/", url)
