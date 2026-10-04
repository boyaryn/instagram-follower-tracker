"""Typer commands: parse arguments, call services, render output, map errors to exit codes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from igft.cli.db import db_app
from igft.cli.errors import IgftGroup
from igft.cli.report import first_seen, list_followers
from igft.cli.scan import resume, scan
from igft.cli.session import session_app
from igft.cli.state import AppState
from igft.cli.target import target_app
from igft.config import load_settings

app = typer.Typer(
    name="igft",
    cls=IgftGroup,
    help="Track who follows a small set of Instagram profiles.",
    no_args_is_help=True,
)
app.add_typer(session_app, name="session")
app.add_typer(target_app, name="target")
app.add_typer(db_app, name="db")
app.command("scan")(scan)
app.command("resume")(resume)
app.command("list")(list_followers)
app.command("first-seen")(first_seen)


@app.callback()
def _root(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="Config file to use instead of $IGFT_CONFIG or the per-user config file.",
            dir_okay=False,
        ),
    ] = None,
) -> None:
    """Track who follows a small set of Instagram profiles."""
    ctx.obj = AppState(settings=load_settings(config))


def main() -> None:
    app()
