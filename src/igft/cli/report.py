"""`igft list` and `igft first-seen`: reports read from the database only, never from Instagram."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import typer

from igft.cli.state import get_state
from igft.db.engine import engine_from_settings
from igft.reporting import queries
from igft.reporting.render import OutputFormat, render

UsernameArg = Annotated[str, typer.Argument(help="A target added with `igft target add`.")]
FormatOpt = Annotated[
    OutputFormat, typer.Option("--format", help="How to print the result.", case_sensitive=False)
]


def _print(ctx: typer.Context, build, fmt: OutputFormat) -> None:
    engine = engine_from_settings(get_state(ctx).settings)
    try:
        report = build(engine)
    finally:
        engine.dispose()
    typer.echo(render(report.rows, fmt), nl=False)
    # Notes go to stderr so that CSV and JSON on stdout stay valid.
    for note in report.notes:
        typer.echo(note, err=True)


def list_followers(ctx: typer.Context, username: UsernameArg, fmt: FormatOpt = OutputFormat.TABLE) -> None:
    """Show every follower igft has recorded for a target. Makes no Instagram request."""
    _print(ctx, lambda engine: queries.list_followers(engine, username), fmt)


def first_seen(
    ctx: typer.Context,
    username: UsernameArg,
    since: Annotated[
        datetime | None,
        typer.Option(
            "--since",
            formats=["%Y-%m-%d"],
            help="Followers first seen on or after this local date (YYYY-MM-DD), baseline excluded.",
        ),
    ] = None,
    since_scan: Annotated[
        int | None,
        typer.Option("--since-scan", min=1, help="Followers first seen in any scan of this target after this scan ID."),
    ] = None,
    fmt: FormatOpt = OutputFormat.TABLE,
) -> None:
    """Show the followers first seen in the latest scan, or since a date or an earlier scan.

    "First seen" is the scan in which igft first recorded the follower, not when they started
    following. Makes no Instagram request.
    """
    if since is not None and since_scan is not None:
        raise typer.BadParameter("use either --since or --since-scan, not both.")
    if since is not None:
        local_midnight = since.astimezone()
        _print(ctx, lambda engine: queries.first_seen_since(engine, username, local_midnight), fmt)
    elif since_scan is not None:
        _print(ctx, lambda engine: queries.first_seen_since_scan(engine, username, since_scan), fmt)
    else:
        _print(ctx, lambda engine: queries.first_seen_latest(engine, username), fmt)
