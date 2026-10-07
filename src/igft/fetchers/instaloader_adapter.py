"""The instaloader backend (design D4): Firefox's Instagram cookies, sent as the Instagram web app sends them.

Only instaloader's cookie session, user agent and session-file format are used. The three requests are
plain GETs that follow no redirects, sleep never and retry never: every call makes exactly one HTTP
request, and whatever Instagram answers is either mapped to a domain result or classified by `classify()`.
`Instaloader.login()` is never called.
"""

from __future__ import annotations

import io
import platform
import re
from typing import Any
from urllib.parse import urlparse

import requests
from instaloader import Instaloader, RateController
from instaloader.instaloadercontext import InstaloaderContext

from igft import sessionfile
from igft.config import Settings
from igft.domain import (
    Backend,
    BlockSignal,
    FetchError,
    FollowerPage,
    FollowerRecord,
    ProfileInfo,
    ProfileNotFound,
    SessionMissing,
    SignalKind,
)
from igft.safety.waits import parse_stated_wait

BASE_URL = "https://www.instagram.com"
WEB_APP_ID = "936619743392459"  # x-ig-app-id of the Instagram web app
SESSION_CHECK_QUERY_HASH = "d6f4427fbe92d846298cf93df0b937d3"  # the query Instaloader.test_login() sends
PAGE_SIZE = 12  # what the web app asks for
FIREFOX_VERSION = "140.0"
_PROFILE_ID = re.compile(r'"profile_id":"(\d+)"')
_USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}")
_CHALLENGE_PATH = re.compile(r"/(challenge|checkpoint|auth_platform)")
_BODY_EXCERPT = 500


def default_user_agent() -> str:
    """A desktop Firefox user agent for this OS, e.g. `X11; Ubuntu; Linux x86_64` on Ubuntu."""
    system = platform.system()
    if system == "Darwin":
        os_part = "Macintosh; Intel Mac OS X 10.15"
    elif system == "Windows":
        os_part = "Windows NT 10.0; Win64; x64"
    else:
        try:
            distribution = platform.freedesktop_os_release().get("ID")
        except OSError:
            distribution = None
        os_part = f"X11; {'Ubuntu; ' if distribution == 'ubuntu' else ''}Linux {platform.machine()}"
    return f"Mozilla/5.0 ({os_part}; rv:{FIREFOX_VERSION}) Gecko/20100101 Firefox/{FIREFOX_VERSION}"


class NoWaitRateController(RateController):
    """Instaloader's rate controller with its waiting replaced by errors: igft never sleeps inside a request."""

    def handle_429(self, query_type: str) -> None:
        raise BlockSignal(SignalKind.RATE_LIMIT, f"HTTP 429 Too Many Requests ({query_type} query)")

    def sleep(self, secs: float) -> None:
        raise FetchError(f"instaloader asked to wait {secs:.0f} s, and igft does not wait inside a request")


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


def _session_check_headers() -> dict[str, str | None]:
    """What Instaloader's own GraphQL helper adds to the session's headers."""
    return {"Connection": None, "Content-Length": None, "authority": "www.instagram.com", "scheme": "https", "accept": "*/*"}


def describe(response: requests.Response) -> str:
    """The raw message kept for a response: status, any redirect target, and a JSON body's start."""
    text = f"HTTP {response.status_code} {response.reason}".rstrip()
    location = response.headers.get("location")
    if location:
        text += f" redirect to {location}"
    if response.headers.get("content-type", "").startswith("application/json") and response.text:
        text += f": {response.text[:_BODY_EXCERPT]}"
    return text


def _redirects_to_home_page(response: requests.Response) -> bool:
    """True for a redirect whose target is Instagram's home page, relative or on instagram.com."""
    location = response.headers.get("location", "")
    if not 300 <= response.status_code < 400 or not location:
        return False
    target = urlparse(location)
    host = (target.hostname or "").lower()
    on_instagram = host in ("", "instagram.com") or host.endswith(".instagram.com")
    return on_instagram and target.path in ("", "/")


def classify(response: requests.Response, *, follower_page: bool = False) -> BlockSignal | FetchError:
    """Map a response that is not a success to a block signal, or to a `FetchError` with its raw message.

    Challenge or checkpoint first, then `feedback_required` (an action block), then a rejected session, then, for
    a follower-page request only, a redirect to the home page (the follower list withheld from the account), then
    a rate limit (HTTP 429 or a "please wait" message). Anything else is a `FetchError`: it is never guessed to be
    a block, and it is never retried.
    """
    raw = describe(response)
    lowered = raw.lower()
    path = urlparse(response.headers.get("location", "")).path.lower()
    withheld_list = False
    if "challenge_required" in lowered or "checkpoint_required" in lowered or _CHALLENGE_PATH.match(path):
        kind = SignalKind.CHALLENGE
    elif "feedback_required" in lowered:
        kind = SignalKind.ACTION_BLOCK
    elif "login_required" in lowered or "require_login" in lowered or path.startswith("/accounts/login"):
        kind = SignalKind.SESSION_REJECTED
    elif follower_page and _redirects_to_home_page(response):
        kind = SignalKind.RATE_LIMIT
        withheld_list = True
    elif response.status_code == 429 or "please wait a few minutes" in lowered:
        kind = SignalKind.RATE_LIMIT
    else:
        return FetchError(raw)
    return BlockSignal(kind, raw, parse_stated_wait(raw), withheld_list=withheld_list)


def _json(response: requests.Response, *, follower_page: bool = False) -> dict[str, Any]:
    """The JSON object of a successful response, or the error for anything else (including `status` not `ok`)."""
    if response.status_code != 200:
        raise classify(response, follower_page=follower_page)
    try:
        data = response.json()
    except ValueError:
        raise FetchError(f"{describe(response)}: the body is not JSON") from None
    if not isinstance(data, dict):
        raise FetchError(f"{describe(response)}: the body is not a JSON object")
    if data.get("status", "ok") != "ok":
        raise classify(response, follower_page=follower_page)
    return data


def _follower(entry: Any) -> FollowerRecord:
    try:
        pk, username = int(entry["pk"]), str(entry["username"])
    except (KeyError, TypeError, ValueError):
        raise FetchError("a follower entry has no usable pk and username") from None

    def flag(name: str) -> bool | None:
        value = entry.get(name)
        return value if isinstance(value, bool) else None

    full_name = entry.get("full_name")
    return FollowerRecord(
        pk=pk,
        username=username,
        full_name=full_name if isinstance(full_name, str) and full_name else None,
        is_private=flag("is_private"),
        is_verified=flag("is_verified"),
    )


class InstaloaderFetcher:
    backend = Backend.INSTALOADER

    def __init__(self, loader: Instaloader, user_agent: str) -> None:
        self._loader = loader
        self._user_agent = user_agent

    @staticmethod
    def _new_loader(user_agent: str) -> Instaloader:
        return Instaloader(
            sleep=False,
            quiet=True,
            user_agent=user_agent,
            max_connection_attempts=1,
            rate_controller=lambda context: NoWaitRateController(context),
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            save_metadata=False,
            compress_json=False,
            iphone_support=False,
        )

    @classmethod
    def from_cookies(cls, cookies: dict[str, str], user_agent: str | None = None) -> InstaloaderFetcher:
        agent = user_agent or default_user_agent()
        loader = cls._new_loader(agent)
        try:
            loader.context.load_session("", cookies)
        except KeyError:
            raise SessionMissing(Backend.INSTALOADER, "The session has no csrftoken cookie.") from None
        return cls(loader, agent)

    @classmethod
    def from_settings(cls, settings: Settings) -> InstaloaderFetcher:
        path = settings.instaloader_session_path
        agent = settings.instaloader_user_agent or default_user_agent()
        loader = cls._new_loader(agent)
        try:
            loader.context.load_session_from_file("", io.BytesIO(sessionfile.read(path)))
        except Exception:
            raise SessionMissing(Backend.INSTALOADER, f"The saved Instagram session at {path} cannot be read.") from None
        return cls(loader, agent)

    def session_bytes(self) -> bytes:
        """The session in instaloader's file format, for `sessionfile.write_atomic`."""
        buffer = io.BytesIO()
        self._loader.context.save_session_to_file(buffer)
        return buffer.getvalue()

    @property
    def _context(self) -> InstaloaderContext:
        return self._loader.context

    @property
    def _csrftoken(self) -> str:
        return self._context._session.cookies.get_dict()["csrftoken"]

    def _get(self, url: str, *, params: dict[str, str] | None = None, headers: dict[str, str | None]) -> requests.Response:
        try:
            return self._context._session.get(url, params=params, headers=headers, allow_redirects=False)
        except requests.exceptions.RequestException as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc

    def get_profile(self, username: str) -> ProfileInfo:
        if not _USERNAME.fullmatch(username):
            raise ProfileNotFound(username)
        name = username.lower()
        response = self._get(f"{BASE_URL}/{name}/", headers=page_headers(self._user_agent))
        if response.status_code == 404:
            raise ProfileNotFound(username)
        if response.status_code != 200:
            raise classify(response)
        ids = set(_PROFILE_ID.findall(response.text))
        if len(ids) != 1:
            found = f"{len(ids)} different profile_id values" if ids else "no profile_id"
            raise FetchError(f"The profile page of {name} has {found}, so the account ID cannot be read.")
        return ProfileInfo(pk=int(ids.pop()), username=name)

    def fetch_followers_page(self, user_id: int, cursor: str | None, *, username: str) -> FollowerPage:
        params = {"count": str(PAGE_SIZE)}
        if cursor is not None:
            params["max_id"] = cursor
        params["search_surface"] = "follow_list_page"
        referer = f"{BASE_URL}/{username}/followers/"
        response = self._get(
            f"{BASE_URL}/api/v1/friendships/{int(user_id)}/followers/",
            params=params,
            headers=api_headers(self._user_agent, self._csrftoken, referer),
        )
        data = _json(response, follower_page=True)
        users = data.get("users")
        if not isinstance(users, list):
            raise FetchError(f"{describe(response)}: the response has no list of users")
        next_cursor = data.get("next_max_id")
        has_more = data.get("has_more") is not False and next_cursor not in (None, "")
        return FollowerPage(
            followers=tuple(_follower(entry) for entry in users),
            next_cursor=str(next_cursor) if has_more else None,
        )

    def check_session(self) -> str:
        response = self._get(
            f"{BASE_URL}/graphql/query",
            params={"query_hash": SESSION_CHECK_QUERY_HASH, "variables": "{}"},
            headers=_session_check_headers(),
        )
        data = _json(response)
        user = (data.get("data") or {}).get("user")
        if not isinstance(user, dict) or not user.get("username"):
            raise BlockSignal(SignalKind.SESSION_REJECTED, f"{describe(response)}: the response names no logged-in account")
        return str(user["username"])
