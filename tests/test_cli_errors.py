from datetime import timedelta

import pytest
import typer
from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from igft.cli.errors import (
    EXIT_BLOCKED,
    EXIT_REFUSED,
    EXIT_STOPPED,
    EXIT_USER_ERROR,
    IgftGroup,
    exit_code_for,
    message_for,
)
from igft.config import ConfigError
from igft.domain import (
    Backend,
    BlockSignal,
    CommandRefused,
    FetchError,
    IgftError,
    ProfileNotFound,
    SessionMissing,
    SignalKind,
)

CASES = [
    (ConfigError("bad config"), EXIT_USER_ERROR, "bad config"),
    (IgftError("plain error"), EXIT_USER_ERROR, "plain error"),
    (ProfileNotFound("nobody"), EXIT_USER_ERROR, "No Instagram profile named 'nobody' exists."),
    (SessionMissing(Backend.INSTALOADER), EXIT_USER_ERROR, "session import"),
    (CommandRefused("A cooldown is active until 2026-10-04 10:00."), EXIT_REFUSED, "cooldown is active"),
    (
        BlockSignal(SignalKind.RATE_LIMIT, "Please wait a few minutes", timedelta(minutes=5)),
        EXIT_BLOCKED,
        "rate limit signal: Please wait a few minutes",
    ),
    (BlockSignal(SignalKind.CHALLENGE, "checkpoint_required"), EXIT_BLOCKED, "challenge signal"),
    (FetchError("HTTP 500"), EXIT_STOPPED, "Instagram request failed: HTTP 500"),
]


@pytest.mark.parametrize(("exc", "code", "fragment"), CASES, ids=lambda v: type(v).__name__ if not isinstance(v, (int, str)) else "")
def test_each_exception_maps_to_its_code_and_message(exc, code, fragment):
    assert exit_code_for(exc) == code
    assert fragment in message_for(exc)


def test_a_fetch_error_prints_only_its_message():
    assert message_for(FetchError("HTTP 400")) == "Instagram request failed: HTTP 400"


def test_database_connection_failures_are_user_errors():
    exc = OperationalError("SELECT 1", {}, Exception("connection refused"))
    assert exit_code_for(exc) == EXIT_USER_ERROR
    assert "connection refused" in message_for(exc)


def test_unmapped_exceptions_have_no_message_mapping():
    with pytest.raises(TypeError):
        message_for(KeyError("x"))


@pytest.mark.parametrize(("exc", "code", "fragment"), CASES, ids=lambda v: type(v).__name__ if not isinstance(v, (int, str)) else "")
def test_the_root_group_prints_the_message_to_stderr_and_exits_with_the_code(exc, code, fragment):
    app = typer.Typer(cls=IgftGroup)

    @app.command()
    def boom() -> None:
        raise exc

    @app.command()
    def other() -> None: ...

    result = CliRunner().invoke(app, ["boom"])
    assert result.exit_code == code
    assert fragment in result.stderr
    assert result.stdout == ""


def test_exit_codes_match_the_design():
    assert (EXIT_USER_ERROR, EXIT_REFUSED, EXIT_BLOCKED, EXIT_STOPPED) == (1, 3, 4, 5)
