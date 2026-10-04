"""Throwaway spike: confirm how instagrapi reads a follower list today, with a manual mobile login.

Run on the Ubuntu host:

    uv run python spike/instagrapi_spike.py --target <small_public_profile>

The first run asks for the research account's username, password (not echoed) and any 2FA or
verification code, and saves instagrapi's session and device fingerprint to
spike/output/instagrapi.session (owner-only, git-ignored). Later runs reuse that file and do not
log in again. To keep the same device later, copy that file to igft's instagrapi session path.

After login it makes at most 5 requests, 15-45 s apart, and stops at the first error without
retrying: session check, profile lookup, follower page 1, follower page 2, and page 2 again with a
garbled cursor. Pass --old-cursor-file spike/output/instagrapi-cursor.txt on a later run to also
try a genuinely old cursor.

Each follower page is the single request user_followers_v1_chunk() sends, made directly, because
user_followers_v1_chunk() itself keeps requesting more pages, with no delay, whenever a page comes
back shorter than asked for.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

import _common as c

BACKEND = "instagrapi"
SETTINGS_PATH = c.OUTPUT_DIR / "instagrapi.session"

# Device presets for --device. The app version, version code and bloks hash stay instagrapi's own,
# because they must match the request formats it sends.
DEVICES = {
    "pixel-6a": {
        "manufacturer": "Google/google",
        "model": "Pixel 6a",
        "device": "bluejay",
        "cpu": "bluejay",
        "resolution": "1080x2400",
        "dpi": "420dpi",
    },
}
ANDROID_API_LEVELS = {"13": 33, "14": 34, "15": 35, "16": 36, "17": 37}


def _raise(client, exc):
    raise exc


def write_private(path: Path, text: str) -> None:
    c.OUTPUT_DIR.mkdir(mode=0o700, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", required=True, help="a small public profile (a few hundred followers)")
    parser.add_argument("--count", type=int, default=50, help="page size to request (instagrapi's maximum is 200)")
    parser.add_argument("--old-cursor-file", help="cursor saved by an earlier run, to test expiry")
    # instagrapi's device otherwise claims a Pixel 8 Pro on Android 14, en_US, US, +1 and New York
    # time. Used only for a new login; a saved session keeps the values it was created with.
    parser.add_argument("--device", choices=sorted(DEVICES), help="phone model to claim")
    parser.add_argument("--android-release", choices=sorted(ANDROID_API_LEVELS, key=int),
                        help="Android version to claim, as shown in Settings > About phone")
    parser.add_argument("--locale", help="device locale, e.g. uk_UA (also sets --country from its suffix)")
    parser.add_argument("--country", help="ISO country code, e.g. UA")
    parser.add_argument("--country-code", type=int, help="phone calling code, e.g. 380")
    parser.add_argument("--timezone-offset", type=int, help="seconds east of UTC, e.g. 10800 for UTC+3")
    args = parser.parse_args()

    import json

    import instagrapi
    from instagrapi import Client
    from instagrapi.exceptions import TwoFactorRequired

    old_cursor: str | None = None
    old_cursor_age_h = 0.0
    if args.old_cursor_file:  # read before this run overwrites the saved cursor
        old_path = Path(args.old_cursor_file)
        old_cursor = old_path.read_text().strip()
        old_cursor_age_h = (time.time() - old_path.stat().st_mtime) / 3600

    c.install_recorder()
    findings: list[str] = [f"instagrapi {getattr(instagrapi, '__version__', '?')}, page size requested: {args.count}"]
    typed_secrets: list[str] = []
    code_prompts: list[str] = []

    def prompt_code(username, choice=None):
        code_prompts.append(str(choice))
        code = input(f"Instagram sent a verification code ({choice}). Enter it: ").strip()
        typed_secrets.append(code)
        return code

    # The spike's own pacer owns delays; session_retry_total=0 stops urllib3 silently retrying 429s
    # on the public session (the private one goes through curl, which does not retry).
    client = Client(delay_range=None, session_retry_total=0)
    client.challenge_code_handler = prompt_code
    device_overrides = [name for name in ("device", "android_release", "locale", "country", "country_code",
                                          "timezone_offset")
                        if getattr(args, name) is not None]

    def finish(code: int) -> int:
        c.append_findings(BACKEND, findings)
        print(f"Raw log (local only): {c.save_raw(BACKEND)}")
        return code

    if SETTINGS_PATH.exists():
        client.load_settings(SETTINGS_PATH)
        findings.append("reused the saved session from an earlier spike run (no login)")
        if device_overrides:
            findings.append(f"ignored for the saved session: {', '.join(device_overrides)}")
    else:
        device = dict(DEVICES.get(args.device, {}))
        if args.android_release is not None:
            device["android_release"] = args.android_release
            device["android_version"] = ANDROID_API_LEVELS[args.android_release]
        if device:
            client.set_device(device)  # before set_locale, which edits the user agent this rebuilds
        if args.locale is not None:
            client.set_locale(args.locale)
        if args.country is not None:
            client.set_country(args.country)
        if args.country_code is not None:
            client.set_country_code(args.country_code)
        if args.timezone_offset is not None:
            client.set_timezone_offset(args.timezone_offset)
        # Only which values were overridden, not the values themselves.
        findings.append(f"device locale settings overridden: {', '.join(device_overrides) or 'none'}")
        username = input("Research account username: ").strip()
        password = getpass.getpass("Password (not shown): ")
        typed_secrets.append(password)
        start = len(c.EXCHANGES)
        try:
            try:
                client.login(username, password)
            except TwoFactorRequired:
                code = input("Two-factor code: ").strip()
                typed_secrets.append(code)
                client.login(username, password, verification_code=code)
        except Exception as exc:
            findings.append(f"login FAILED with {type(exc).__name__} after {len(c.requests_since(start))} request(s)")
            findings.append(f"verification code prompted: {', '.join(code_prompts) or 'no'}")
            c.write_fixture(BACKEND, "login_failure",
                            [{"exception": {"type": type(exc).__name__, "message": str(exc)}}],
                            forbidden=typed_secrets)
            return finish(1)
        finally:
            password = None  # noqa: F841 - drop the only reference as soon as possible
        client.password = None  # never kept on the client after login
        findings.append(f"login made {len(c.requests_since(start))} request(s); "
                        f"verification code prompted: {', '.join(code_prompts) or 'no'}")
        settings_text = json.dumps(client.get_settings(), indent=2)
        if any(s and s in settings_text for s in typed_secrets):
            sys.exit("refusing to save settings: they contain something you typed")
        write_private(SETTINGS_PATH, settings_text)
        findings.append(f"settings keys saved: {', '.join(sorted(json.loads(settings_text)))}")

    # From here on, nothing may retry or resolve a challenge on its own.
    client.handle_exception = _raise
    findings.append(f"private transport: {client.private_transport}")
    pacer = c.Pacer()

    def request(endpoint: str, params: dict | None = None):
        # _send_private_request skips private_request()'s timeout retries and challenge handling.
        client._send_private_request(endpoint, params=params)
        return client.last_json

    def step(name: str, label: str, call):
        start = len(c.EXCHANGES)
        pacer.wait(label)
        try:
            result, error = call(), None
        except Exception as exc:  # stop at the first error, but keep what it looked like
            result, error = None, exc
        exchanges = c.requests_since(start)
        record = {"exchanges": exchanges}
        if error is not None:
            record["exception"] = {"type": type(error).__name__, "message": str(error)}
            record["last_json"] = client.last_json
        session_values = [client.authorization, *client.private.cookies.get_dict().values()]
        c.write_fixture(BACKEND, name, [record], forbidden=typed_secrets + session_values)
        endpoints = ", ".join(f"{e.get('method')} {c.endpoint_of(e)} -> {e.get('status', e.get('error'))}" for e in exchanges)
        findings.append(f"{label}: {len(exchanges)} HTTP request(s): {endpoints or 'none'}")
        if error is not None:
            findings.append(f"{label} FAILED with {type(error).__name__}: stopping (no retry)")
        return result, error

    data, err = step("session_check", "session check (account_info endpoint)",
                     lambda: request("accounts/current_user/?edit=true"))
    if err:
        return finish(1)

    data, err = step("profile", "profile lookup (usernameinfo)",
                     lambda: request(f"users/{args.target.lower()}/usernameinfo/"))
    if err:
        return finish(1)
    profile = data["user"]
    user_id = profile["pk"]
    findings.append(
        f"profile: is_private={profile.get('is_private')}, friendship_status present={'friendship_status' in profile}, "
        f"fields: {', '.join(sorted(profile))[:400]}"
    )

    def followers(max_id: str | None):
        params = {
            "count": args.count,
            "rank_token": client.rank_token,
            "search_surface": "follow_list_page",
            "query": "",
            "enable_groups": "true",
        }
        if max_id:
            params["max_id"] = max_id
        return request(f"friendships/{user_id}/followers/", params)

    def describe_page(label: str, data) -> str | None:
        users = data.get("users", [])
        fields = sorted(users[0].keys()) if users else []
        findings.append(
            f"{label}: {len(users)} followers, big_list={data.get('big_list')}, "
            f"next_max_id {c.cursor_shape(data.get('next_max_id'))}; user fields: {', '.join(fields)}"
        )
        return data.get("next_max_id")

    data, err = step("followers_page_1", "follower page 1", lambda: followers(None))
    if err:
        return finish(1)
    cursor_page2 = describe_page("page 1", data)
    if not cursor_page2:
        findings.append("the list ended after page 1; pick a target with more followers to test paging")
        return finish(0)

    data, err = step("followers_page_2", "follower page 2", lambda: followers(cursor_page2))
    if err:
        return finish(1)
    describe_page("page 2", data)
    write_private(c.OUTPUT_DIR / "instagrapi-cursor.txt", cursor_page2)

    garbled = cursor_page2[:-4] + "ZZZZ" if len(cursor_page2) > 4 else "garbled"
    data, err = step("garbled_cursor", "page 2 with a garbled cursor", lambda: followers(garbled))
    if data is not None:
        describe_page("garbled cursor response", data)
    if err:
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
