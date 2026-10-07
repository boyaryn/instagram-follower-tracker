"""The single place where exceptions become messages and exit codes (design D14)."""

from __future__ import annotations

import typer
from sqlalchemy.exc import OperationalError
from typer.core import TyperGroup

from igft.domain import BlockSignal, CommandRefused, FetchError, IgftError

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_USAGE = 2  # Click/Typer's own code for a bad command line
EXIT_REFUSED = 3
EXIT_BLOCKED = 4
EXIT_STOPPED = 5


def exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, CommandRefused):
        return EXIT_REFUSED
    if isinstance(exc, BlockSignal):
        return EXIT_BLOCKED
    if isinstance(exc, FetchError):
        return EXIT_STOPPED
    return EXIT_USER_ERROR


def message_for(exc: BaseException) -> str:
    if isinstance(exc, BlockSignal) and exc.guidance:
        return f"{exc.message}\n{exc.guidance}"
    if isinstance(exc, IgftError):
        return exc.message
    if isinstance(exc, OperationalError):
        detail = str(exc.orig).strip() if exc.orig is not None else str(exc)
        return f"Could not use the database: {detail}"
    raise TypeError(f"No message mapping for {type(exc).__name__}")


MAPPED_EXCEPTIONS = (IgftError, OperationalError)


class IgftGroup(TyperGroup):
    """Root command group: turns mapped exceptions from any command into a message and an exit code."""

    def invoke(self, ctx: typer.Context):
        try:
            return super().invoke(ctx)
        except MAPPED_EXCEPTIONS as exc:
            typer.echo(message_for(exc), err=True)
            raise typer.Exit(exit_code_for(exc)) from exc
