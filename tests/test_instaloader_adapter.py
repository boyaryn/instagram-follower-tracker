import dataclasses
import json
import platform
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import instaloader
import pytest
import requests
import responses

from igft import sessionfile
from igft.config import load_settings
from igft.domain import (
    BlockSignal,
    FetchError,
    FollowerPage,
    ProfileInfo,
    ProfileNotFound,
    SessionMissing,
    SignalKind,
)
from igft.fetchers.instaloader_adapter import (
    InstaloaderFetcher,
    NoWaitRateController,
    classify,
    default_user_agent,
)

FIXTURES = Path(__file__).parent / "fixtures" / "instaloader"
UA = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:1.0) Gecko/20100101 Firefox/1.0"
COOKIES = {"sessionid": "sid", "csrftoken": "tok", "ds_user_id": "1000000"}
TARGET_ID = 1000000
PROFILE_URL = re.compile(r"https://www\.instagram\.com/alice/")
FOLLOWERS_URL = re.compile(rf"https://www\.instagram\.com/api/v1/friendships/{TARGET_ID}/followers/.*")
SESSION_URL = re.compile(r"https://www\.instagram\.com/graphql/query.*")


def exchange(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())[0]["exchanges"][0]


def add(name, url, body=None):
    """Serve a fixture's status, content type and (unless replaced) body at `url`."""
    recorded = exchange(name)
    content = recorded["body"] if body is None else body
    responses.add(
        responses.GET,
        url,
        body=content if isinstance(content, str) else json.dumps(content),
        status=recorded["status"],
        content_type=recorded["content_type"],
    )
    return recorded


def profile_html(*ids):
    return "<html><script>" + "".join(f'{{"profile_id":"{i}"}}' for i in ids) + "</script></html>"


def query(call):
    return {k: v[0] for k, v in parse_qs(urlparse(call.request.url).query).items()}


@pytest.fixture
def fetcher():
    return InstaloaderFetcher.from_cookies(COOKIES, UA)


@pytest.fixture(autouse=True)
def mocked_http():
    with responses.mock:
        yield responses


# --- construction (12.3) -------------------------------------------------------


def set_platform(monkeypatch, system, machine="x86_64", release=None):
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)

    def freedesktop_os_release():
        if release is None:
            raise OSError("no os-release")
        return release

    monkeypatch.setattr(platform, "freedesktop_os_release", freedesktop_os_release)


def test_the_default_user_agent_on_ubuntu_is_a_desktop_firefox(monkeypatch):
    set_platform(monkeypatch, "Linux", release={"ID": "ubuntu"})

    agent = default_user_agent()

    assert "X11; Ubuntu; Linux x86_64" in agent
    assert re.fullmatch(r"Mozilla/5\.0 \(.*; rv:(\d+)\.0\) Gecko/20100101 Firefox/\1\.0", agent)


@pytest.mark.parametrize(
    ("system", "machine", "release", "expected"),
    [
        ("Linux", "aarch64", {"ID": "fedora"}, "(X11; Linux aarch64;"),
        ("Linux", "x86_64", None, "(X11; Linux x86_64;"),
        ("Darwin", "arm64", None, "(Macintosh; Intel Mac OS X 10.15;"),
        ("Windows", "AMD64", None, "(Windows NT 10.0; Win64; x64;"),
    ],
)
def test_the_default_user_agent_follows_the_platform(monkeypatch, system, machine, release, expected):
    set_platform(monkeypatch, system, machine, release)

    assert expected in default_user_agent()


def settings_for(tmp_path, **changes):
    config = tmp_path / "config.toml"
    config.write_text("")
    base = load_settings(config, env={})
    return dataclasses.replace(base, instaloader_session_path=tmp_path / "sessions" / "instaloader.session", **changes)


def test_the_configured_user_agent_is_sent_and_the_default_used_otherwise(tmp_path, monkeypatch):
    set_platform(monkeypatch, "Linux", release={"ID": "ubuntu"})
    seed = InstaloaderFetcher.from_cookies(COOKIES, UA)
    configured = settings_for(tmp_path, instaloader_user_agent="Configured/1.0")
    sessionfile.write_atomic(configured.instaloader_session_path, seed.session_bytes())

    assert InstaloaderFetcher.from_settings(configured)._context._session.headers["User-Agent"] == "Configured/1.0"
    default = dataclasses.replace(configured, instaloader_user_agent=None)
    assert "X11; Ubuntu; Linux x86_64" in InstaloaderFetcher.from_settings(default)._context._session.headers["User-Agent"]


def test_the_library_is_set_up_to_never_wait_or_retry(fetcher):
    context = fetcher._context

    assert context.max_connection_attempts == 1
    assert context.sleep is False
    assert isinstance(context._rate_controller, NoWaitRateController)
    with pytest.raises(BlockSignal) as signal:
        context._rate_controller.handle_429("other")
    assert signal.value.kind is SignalKind.RATE_LIMIT
    with pytest.raises(FetchError):
        context._rate_controller.sleep(30)


def test_no_request_sleeps_or_logs_in(fetcher, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not be called")

    monkeypatch.setattr("time.sleep", forbidden)
    monkeypatch.setattr(instaloader.Instaloader, "login", forbidden)
    monkeypatch.setattr(instaloader.Instaloader, "interactive_login", forbidden)
    monkeypatch.setattr(instaloader.instaloadercontext.InstaloaderContext, "login", forbidden)
    add("profile_page", PROFILE_URL, profile_html(TARGET_ID))
    add("followers_page_1", FOLLOWERS_URL)
    add("session_check", SESSION_URL)

    fetcher.get_profile("alice")
    fetcher.fetch_followers_page(TARGET_ID, None, username="alice")
    fetcher.check_session()


def test_a_cookie_set_without_a_csrftoken_cannot_build_a_fetcher():
    with pytest.raises(SessionMissing):
        InstaloaderFetcher.from_cookies({"sessionid": "sid"}, UA)


def test_the_saved_session_round_trips_through_the_session_file(tmp_path, fetcher):
    settings = settings_for(tmp_path, instaloader_user_agent=UA)
    sessionfile.write_atomic(settings.instaloader_session_path, fetcher.session_bytes())

    restored = InstaloaderFetcher.from_settings(settings)

    assert restored._context._session.cookies.get_dict() == COOKIES
    assert restored._csrftoken == "tok"


def test_an_unreadable_session_file_is_session_missing(tmp_path):
    settings = settings_for(tmp_path)
    sessionfile.write_atomic(settings.instaloader_session_path, b"not a pickle")

    with pytest.raises(SessionMissing) as caught:
        InstaloaderFetcher.from_settings(settings)

    assert "igft session import" in caught.value.message


# --- get_profile (12.4) --------------------------------------------------------


def test_get_profile_reads_the_id_from_the_page_with_one_request(fetcher, mocked_http):
    add("profile_page", PROFILE_URL, profile_html(TARGET_ID, TARGET_ID))

    profile = fetcher.get_profile("alice")

    assert profile == ProfileInfo(pk=TARGET_ID, username="alice")
    assert len(mocked_http.calls) == 1
    request = mocked_http.calls[0].request
    assert request.url == "https://www.instagram.com/alice/"
    assert request.headers["User-Agent"] == UA
    assert request.headers["Sec-Fetch-Dest"] == "document"
    for absent in ("X-CSRFToken", "Referer", "Origin", "x-ig-app-id", "X-Requested-With", "X-Instagram-AJAX"):
        assert absent not in request.headers
    assert request.headers["Cookie"].count("sessionid=sid") == 1


def test_get_profile_lowercases_the_username_it_requests_and_returns(fetcher, mocked_http):
    add("profile_page", PROFILE_URL, profile_html(TARGET_ID))

    assert fetcher.get_profile("Alice").username == "alice"
    assert mocked_http.calls[0].request.url == "https://www.instagram.com/alice/"


@pytest.mark.parametrize("username", ["", "../etc", "a b", "alice/followers", "x" * 31, "a?b=1"])
def test_a_username_that_cannot_exist_is_not_found_without_a_request(fetcher, mocked_http, username):
    with pytest.raises(ProfileNotFound):
        fetcher.get_profile(username)

    assert len(mocked_http.calls) == 0


def test_a_404_is_profile_not_found(fetcher, mocked_http):
    responses.add(responses.GET, PROFILE_URL, status=404, body="<html>not available</html>", content_type="text/html")

    with pytest.raises(ProfileNotFound):
        fetcher.get_profile("alice")

    assert len(mocked_http.calls) == 1


@pytest.mark.parametrize(
    ("html", "expected"),
    [(profile_html(), "no profile_id"), (profile_html(1000001, 1000002), "2 different profile_id")],
)
def test_a_page_without_exactly_one_profile_id_is_a_fetch_error(fetcher, mocked_http, html, expected):
    add("profile_page", PROFILE_URL, html)

    with pytest.raises(FetchError) as caught:
        fetcher.get_profile("alice")

    assert expected in caught.value.raw_message
    assert len(mocked_http.calls) == 1


# --- fetch_followers_page (12.4) -----------------------------------------------


def test_the_first_page_is_requested_like_the_web_app_and_mapped(fetcher, mocked_http):
    recorded = add("followers_page_1", FOLLOWERS_URL)

    page = fetcher.fetch_followers_page(TARGET_ID, None, username="alice")

    assert len(mocked_http.calls) == 1
    call = mocked_http.calls[0]
    assert urlparse(call.request.url).path == f"/api/v1/friendships/{TARGET_ID}/followers/"
    assert query(call) == {"count": "12", "search_surface": "follow_list_page"}
    headers = call.request.headers
    assert headers["X-CSRFToken"] == "tok"
    assert headers["x-ig-app-id"] == "936619743392459"
    assert headers["Referer"] == "https://www.instagram.com/alice/followers/"
    assert headers["User-Agent"] == UA
    assert "Origin" not in headers
    assert len(page.followers) == 12
    assert page.next_cursor == recorded["body"]["next_max_id"]
    first = page.followers[0]
    assert (first.pk, first.is_private, first.is_verified) == (1000001, True, False)


def test_a_later_page_sends_the_cursor_and_returns_the_next_one_unchanged(fetcher, mocked_http):
    recorded = add("followers_page_2", FOLLOWERS_URL)

    page = fetcher.fetch_followers_page(TARGET_ID, "an opaque+cursor/1", username="alice")

    assert query(mocked_http.calls[0]) == {
        "count": "12",
        "max_id": "an opaque+cursor/1",
        "search_surface": "follow_list_page",
    }
    assert page.next_cursor == recorded["body"]["next_max_id"]
    assert len(mocked_http.calls) == 1


def test_a_garbled_cursor_is_sent_as_given_and_the_answer_mapped(fetcher, mocked_http):
    add("garbled_cursor", FOLLOWERS_URL)

    page = fetcher.fetch_followers_page(TARGET_ID, "!!garbled!!", username="alice")

    assert query(mocked_http.calls[0])["max_id"] == "!!garbled!!"
    assert len(page.followers) == 12
    assert len(mocked_http.calls) == 1


def test_no_picture_url_reaches_the_records(fetcher):
    add("followers_page_1", FOLLOWERS_URL)

    page = fetcher.fetch_followers_page(TARGET_ID, None, username="alice")

    text = repr(page)
    assert "profile_pic" not in text and "http" not in text
    assert {field.name for field in dataclasses.fields(page.followers[0])} == {
        "pk",
        "username",
        "full_name",
        "is_private",
        "is_verified",
    }


def test_an_empty_list_for_a_private_unfollowed_profile_is_an_empty_page_not_an_error(fetcher, mocked_http):
    add("followers_private_unfollowed", FOLLOWERS_URL)

    page = fetcher.fetch_followers_page(TARGET_ID, None, username="alice")

    assert page == FollowerPage((), None)
    assert len(mocked_http.calls) == 1


def test_the_last_page_has_its_followers_and_no_cursor(fetcher):
    recorded = add("followers_last_page", FOLLOWERS_URL)
    assert "next_max_id" not in recorded["body"]

    page = fetcher.fetch_followers_page(TARGET_ID, "c", username="alice")

    assert [f.pk for f in page.followers] == [int(u["pk"]) for u in recorded["body"]["users"]]
    assert len(page.followers) == 5
    assert page.next_cursor is None


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"has_more": False, "next_max_id": "99"}, None),
        ({"has_more": True}, None),
        ({"has_more": True, "next_max_id": "99"}, "99"),
        ({"next_max_id": "99"}, "99"),
        ({"has_more": True, "next_max_id": ""}, None),
    ],
)
def test_the_cursor_is_next_max_id_only_while_the_list_goes_on(fetcher, extra, expected):
    responses.add(responses.GET, FOLLOWERS_URL, json={"status": "ok", "users": [], **extra})

    assert fetcher.fetch_followers_page(TARGET_ID, None, username="alice").next_cursor == expected


def test_fields_a_follower_entry_lacks_are_none(fetcher):
    entry = {"pk": "7", "username": "bob"}
    responses.add(responses.GET, FOLLOWERS_URL, json={"status": "ok", "users": [entry], "has_more": False})

    (follower,) = fetcher.fetch_followers_page(TARGET_ID, None, username="alice").followers

    assert (follower.pk, follower.username) == (7, "bob")
    assert (follower.full_name, follower.is_private, follower.is_verified) == (None, None, None)


@pytest.mark.parametrize("full_name", ["", None, 5])
def test_an_empty_or_unusable_full_name_is_none(fetcher, full_name):
    entry = {"pk": "7", "username": "bob", "full_name": full_name}
    responses.add(responses.GET, FOLLOWERS_URL, json={"status": "ok", "users": [entry], "has_more": False})

    (follower,) = fetcher.fetch_followers_page(TARGET_ID, None, username="alice").followers

    assert follower.full_name is None


@pytest.mark.parametrize("entry", [{"username": "bob"}, {"pk": "x", "username": "bob"}, {"pk": "7"}, "bob"])
def test_a_follower_entry_without_a_usable_pk_and_username_is_a_fetch_error(fetcher, entry):
    responses.add(responses.GET, FOLLOWERS_URL, json={"status": "ok", "users": [entry]})

    with pytest.raises(FetchError):
        fetcher.fetch_followers_page(TARGET_ID, None, username="alice")


def test_a_response_without_a_users_list_is_a_fetch_error(fetcher):
    responses.add(responses.GET, FOLLOWERS_URL, json={"status": "ok"})

    with pytest.raises(FetchError):
        fetcher.fetch_followers_page(TARGET_ID, None, username="alice")


# --- check_session (12.4) ------------------------------------------------------


def test_the_session_check_makes_one_request_and_names_the_account(fetcher, mocked_http):
    recorded = add("session_check", SESSION_URL)

    username = fetcher.check_session()

    assert username == recorded["body"]["data"]["user"]["username"]
    assert len(mocked_http.calls) == 1
    call = mocked_http.calls[0]
    assert urlparse(call.request.url).path == "/graphql/query"
    assert query(call) == {"query_hash": "d6f4427fbe92d846298cf93df0b937d3", "variables": "{}"}
    assert call.request.headers["X-CSRFToken"] == "tok"
    assert call.request.headers["User-Agent"] == UA


def test_a_session_check_that_returns_no_user_is_a_rejected_session(fetcher):
    responses.add(responses.GET, SESSION_URL, json={"data": {"user": None}, "status": "ok"})

    with pytest.raises(BlockSignal) as caught:
        fetcher.check_session()

    assert caught.value.kind is SignalKind.SESSION_REJECTED


# --- classify() (12.5) ---------------------------------------------------------

CALLS = {
    "get_profile": (PROFILE_URL, lambda f: f.get_profile("alice")),
    "fetch_followers_page": (FOLLOWERS_URL, lambda f: f.fetch_followers_page(TARGET_ID, None, username="alice")),
    "check_session": (SESSION_URL, lambda f: f.check_session()),
}

JSON = "application/json; charset=utf-8"
RESPONSES = {
    "429 without a body (the profile fixture)": (429, {}, "<body>", "text/plain", SignalKind.RATE_LIMIT),
    "429 with a stated wait": (
        429,
        {},
        json.dumps({"message": "Please wait 2 hours before trying again.", "status": "fail"}),
        JSON,
        SignalKind.RATE_LIMIT,
    ),
    "please wait a few minutes": (
        400,
        {},
        json.dumps({"message": "Please wait a few minutes before you try again.", "status": "fail"}),
        JSON,
        SignalKind.RATE_LIMIT,
    ),
    "challenge_required": (
        400,
        {},
        json.dumps({"message": "challenge_required", "challenge": {"url": "https://www.instagram.com/challenge/"}}),
        JSON,
        SignalKind.CHALLENGE,
    ),
    "checkpoint_required": (
        400,
        {},
        json.dumps({"message": "checkpoint_required", "status": "fail"}),
        JSON,
        SignalKind.CHALLENGE,
    ),
    "redirect to a challenge page": (
        302,
        {"Location": "https://www.instagram.com/challenge/action/abc/"},
        "",
        "text/html",
        SignalKind.CHALLENGE,
    ),
    "redirect to a checkpoint page": (
        302,
        {"Location": "https://www.instagram.com/checkpoint/1/"},
        "",
        "text/html",
        SignalKind.CHALLENGE,
    ),
    "redirect to the verification page": (
        302,
        {"Location": "https://www.instagram.com/auth_platform/?apc=abc"},
        "",
        "text/html",
        SignalKind.CHALLENGE,
    ),
    "feedback_required": (
        400,
        {},
        json.dumps({"message": "feedback_required", "spam": True, "status": "fail"}),
        JSON,
        SignalKind.ACTION_BLOCK,
    ),
    "login_required": (
        400,
        {},
        json.dumps({"message": "login_required", "require_login": True, "status": "fail"}),
        JSON,
        SignalKind.SESSION_REJECTED,
    ),
    "redirect to the login page": (
        302,
        {"Location": "https://www.instagram.com/accounts/login/?next=/alice/"},
        "",
        "text/html",
        SignalKind.SESSION_REJECTED,
    ),
}
NOT_BLOCKS = {
    "a redirect elsewhere": (302, {"Location": "https://www.instagram.com/somewhere/"}, "", "text/html", "redirect to"),
    "a server error": (500, {}, "<html>oops</html>", "text/html", "HTTP 500"),
    "a forbidden page": (403, {}, "<html>forbidden</html>", "text/html", "HTTP 403"),
    "an unknown JSON failure": (
        400,
        {},
        json.dumps({"message": "something new", "status": "fail"}),
        JSON,
        "something new",
    ),
}
API_CALLS = ["fetch_followers_page", "check_session"]


def serve(url, status, headers, body, content_type):
    responses.add(responses.GET, url, status=status, headers=headers, body=body, content_type=content_type)


@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("case", RESPONSES)
def test_a_block_response_is_classified_on_every_request_and_never_retried(fetcher, mocked_http, call, case):
    url, run = CALLS[call]
    status, headers, body, content_type, kind = RESPONSES[case]
    serve(url, status, headers, body, content_type)

    with pytest.raises(BlockSignal) as caught:
        run(fetcher)

    assert caught.value.kind is kind
    assert len(mocked_http.calls) == 1
    assert str(status) in caught.value.raw_message


@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("case", NOT_BLOCKS)
def test_anything_else_is_a_fetch_error_with_the_raw_message_and_is_never_retried(fetcher, mocked_http, call, case):
    url, run = CALLS[call]
    status, headers, body, content_type, expected = NOT_BLOCKS[case]
    serve(url, status, headers, body, content_type)

    with pytest.raises(FetchError) as caught:
        run(fetcher)

    assert not isinstance(caught.value, BlockSignal)
    assert expected in caught.value.raw_message
    assert len(mocked_http.calls) == 1


@pytest.mark.parametrize("call", API_CALLS)
def test_a_200_with_status_fail_is_classified_too(fetcher, mocked_http, call):
    url, run = CALLS[call]
    body = json.dumps({"message": "feedback_required", "status": "fail"})
    serve(url, 200, {}, body, JSON)

    with pytest.raises(BlockSignal) as caught:
        run(fetcher)

    assert caught.value.kind is SignalKind.ACTION_BLOCK
    assert len(mocked_http.calls) == 1


@pytest.mark.parametrize(
    ("body", "content_type", "expected"),
    [("<html>hello</html>", "text/html", "not JSON"), ("[1, 2]", JSON, "not a JSON object")],
)
@pytest.mark.parametrize("call", API_CALLS)
def test_a_200_that_is_not_a_json_object_is_a_fetch_error(fetcher, mocked_http, call, body, content_type, expected):
    url, run = CALLS[call]
    serve(url, 200, {}, body, content_type)

    with pytest.raises(FetchError) as caught:
        run(fetcher)

    assert expected in caught.value.raw_message
    assert len(mocked_http.calls) == 1


@pytest.mark.parametrize("call", CALLS)
def test_a_network_error_is_a_fetch_error_and_is_never_retried(fetcher, mocked_http, call):
    url, run = CALLS[call]
    responses.add(responses.GET, url, body=requests.exceptions.ConnectTimeout("timed out"))

    with pytest.raises(FetchError) as caught:
        run(fetcher)

    assert "ConnectTimeout" in caught.value.raw_message
    assert len(mocked_http.calls) == 1


def response_for(status, headers=None, body="", content_type="application/json"):
    response = requests.Response()
    response.status_code = status
    response.reason = {302: "Found", 429: "Too Many Requests"}.get(status, "")
    response.headers.update({"Content-Type": content_type, **(headers or {})})
    response._content = body.encode()
    return response


def test_a_stated_wait_is_kept_on_the_signal():
    signal = classify(response_for(429, body=json.dumps({"message": "Please wait 2 hours.", "status": "fail"})))

    assert isinstance(signal, BlockSignal)
    assert signal.stated_wait == timedelta(hours=2)


def test_a_signal_without_a_stated_wait_has_none():
    signal = classify(response_for(429, body="", content_type="text/plain"))

    assert isinstance(signal, BlockSignal) and signal.stated_wait is None
    assert signal.raw_message == "HTTP 429 Too Many Requests"


def test_a_login_redirect_with_a_challenge_in_its_query_is_still_a_rejected_session():
    response = response_for(302, {"Location": "https://www.instagram.com/accounts/login/?next=/challenge/"}, content_type="text/html")

    assert classify(response).kind is SignalKind.SESSION_REJECTED


def test_a_login_redirect_with_the_verification_page_in_its_query_is_still_a_rejected_session():
    response = response_for(
        302, {"Location": "https://www.instagram.com/accounts/login/?next=/auth_platform/"}, content_type="text/html"
    )

    assert classify(response).kind is SignalKind.SESSION_REJECTED


HOME_REDIRECTS = {
    "the absolute home page": "https://www.instagram.com/",
    "a relative home page": "/",
    "the home page with a query": "https://www.instagram.com/?x=1",
}


@pytest.mark.parametrize("location", HOME_REDIRECTS.values(), ids=HOME_REDIRECTS.keys())
def test_a_redirect_to_the_home_page_on_a_follower_request_is_a_withheld_list(fetcher, mocked_http, location):
    serve(FOLLOWERS_URL, 302, {"Location": location}, "", "text/html")

    with pytest.raises(BlockSignal) as caught:
        fetcher.fetch_followers_page(TARGET_ID, None, username="alice")

    assert caught.value.kind is SignalKind.RATE_LIMIT
    assert caught.value.withheld_list is True
    assert "302" in caught.value.raw_message and location in caught.value.raw_message
    assert len(mocked_http.calls) == 1


@pytest.mark.parametrize("call", ["get_profile", "check_session"])
def test_a_redirect_to_the_home_page_on_another_request_is_a_fetch_error(fetcher, mocked_http, call):
    url, run = CALLS[call]
    serve(url, 302, {"Location": "https://www.instagram.com/"}, "", "text/html")

    with pytest.raises(FetchError) as caught:
        run(fetcher)

    assert not isinstance(caught.value, BlockSignal)
    assert len(mocked_http.calls) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"Location": "http://192.168.0.1/"},
        {"Location": "https://example.com/"},
        {"Location": "https://www.instagram.com/explore/"},
        {},
    ],
    ids=["a captive portal", "another site", "another Instagram page", "no Location"],
)
def test_a_follower_request_redirect_that_is_not_the_home_page_is_a_fetch_error(fetcher, mocked_http, headers):
    serve(FOLLOWERS_URL, 302, headers, "", "text/html")

    with pytest.raises(FetchError) as caught:
        fetcher.fetch_followers_page(TARGET_ID, None, username="alice")

    assert not isinstance(caught.value, BlockSignal)
    assert len(mocked_http.calls) == 1


def test_only_a_withheld_list_signal_carries_the_flag(fetcher, mocked_http):
    serve(FOLLOWERS_URL, 429, {}, "", "text/plain")

    with pytest.raises(BlockSignal) as caught:
        fetcher.fetch_followers_page(TARGET_ID, None, username="alice")

    assert caught.value.withheld_list is False
