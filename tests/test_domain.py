import dataclasses
from datetime import timedelta

import pytest

from igft.domain import (
    Backend,
    BlockSignal,
    FetchError,
    FollowerPage,
    FollowerRecord,
    IgftError,
    ProfileInfo,
    ProfileNotFound,
    SessionMissing,
    SignalKind,
)


def test_backend_values():
    assert [b.value for b in Backend] == ["instaloader"]


def test_signal_kinds():
    assert {k.value for k in SignalKind} == {"rate_limit", "challenge", "action_block", "session_rejected"}


def test_follower_record_defaults_mean_not_provided():
    record = FollowerRecord(pk=1, username="alice")
    assert record.full_name is None
    assert record.is_private is None
    assert record.is_verified is None


def test_follower_record_has_no_profile_picture_field():
    names = {f.name for f in dataclasses.fields(FollowerRecord)}
    assert names == {"pk", "username", "full_name", "is_private", "is_verified"}
    assert not any("pic" in name for name in names)


def test_follower_page_end_of_list():
    page = FollowerPage(followers=(FollowerRecord(pk=1, username="a"),), next_cursor=None)
    assert page.next_cursor is None
    assert len(page.followers) == 1


def test_profile_info():
    info = ProfileInfo(pk=42, username="target")
    assert (info.pk, info.username) == (42, "target")
    assert {f.name for f in dataclasses.fields(ProfileInfo)} == {"pk", "username"}


def test_block_signal_carries_kind_message_and_wait():
    signal = BlockSignal(SignalKind.RATE_LIMIT, "Please wait a few minutes", timedelta(hours=48))
    assert signal.kind is SignalKind.RATE_LIMIT
    assert signal.raw_message == "Please wait a few minutes"
    assert signal.stated_wait == timedelta(hours=48)
    assert "rate limit" in signal.message


@pytest.mark.parametrize(
    "error",
    [
        FetchError("connection reset"),
        ProfileNotFound("nobody"),
        SessionMissing(Backend.INSTALOADER),
        BlockSignal(SignalKind.CHALLENGE, "challenge_required"),
    ],
)
def test_all_errors_are_igft_errors(error):
    assert isinstance(error, IgftError)
    assert error.message


def test_session_missing_tells_how_to_restore():
    message = SessionMissing(Backend.INSTALOADER).message
    assert "Log in to Instagram in Firefox" in message
    assert "igft session import" in message
    assert "--backend" not in message
