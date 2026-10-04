from datetime import timedelta

import pytest

from igft.safety.waits import parse_stated_wait


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Please wait 48 hours before trying again.", timedelta(hours=48)),
        ("You must wait 30 minutes to continue.", timedelta(minutes=30)),
        ("Please wait for at least 2 days.", timedelta(days=2)),
        ("Try again in 15 minutes", timedelta(minutes=15)),
        ("Please wait an hour", timedelta(hours=1)),
        ("WAIT 1 HOUR", timedelta(hours=1)),
        ("wait 1.5 hours", timedelta(hours=1.5)),
        ("Please wait 90 seconds", timedelta(seconds=90)),
    ],
)
def test_a_stated_wait_is_parsed(message, expected):
    assert parse_stated_wait(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "",
        "Please wait a few minutes before you try again.",
        "feedback_required",
        "Too many requests",
        "48 hours",
    ],
)
def test_an_unrecognised_message_returns_none(message):
    assert parse_stated_wait(message) is None
