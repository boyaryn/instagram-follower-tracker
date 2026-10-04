"""`igft target ...`: the profiles whose followers are tracked."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from igft.cli.runtime import instagram_command
from igft.cli.state import get_state
from igft.db.engine import engine_from_settings
from igft.targets import AddOutcome, TargetService, scan_status_label, targets_with_latest_scan
from igft.timeutil import format_local

target_app = typer.Typer(help="Add and list the profiles whose followers are tracked.", no_args_is_help=True)


@target_app.command("add")
def add(ctx: typer.Context, username: str = typer.Argument(..., help="Instagram username of the profile to track.")) -> None:
    """Look the profile up with one request and start tracking it.

    If the profile is private, follow it with the research account first: igft does not check.
    """
    with instagram_command(ctx, "target add") as run:
        result = TargetService(run.engine, run.fetcher).add(username)
    target = result.target
    if result.outcome is AddOutcome.ADDED:
        typer.echo(f"Added @{target.username} (id {target.id}).")
        typer.echo("If this profile is private, make sure the research account follows it before you scan it.")
    elif result.outcome is AddOutcome.RENAMED:
        typer.echo(
            f"@{target.username} (id {target.id}) is already a target under the old username "
            f"@{result.old_username}. The stored username is now @{target.username}."
        )
    else:
        typer.echo(f"@{target.username} (id {target.id}) is already a target.")


@target_app.command("list")
def list_(ctx: typer.Context) -> None:
    """Show every target and its latest scan. Makes no Instagram request."""
    engine = engine_from_settings(get_state(ctx).settings)
    try:
        rows = targets_with_latest_scan(engine)
    finally:
        engine.dispose()
    if not rows:
        typer.echo("No targets yet. Add one with `igft target add <username>`.")
        return
    table = Table()
    table.add_column("Username")
    table.add_column("ID", justify="right", no_wrap=True)
    table.add_column("Added", no_wrap=True)
    table.add_column("Latest scan", no_wrap=True)
    table.add_column("Status")
    for target, scan in rows:
        table.add_row(
            f"@{target.username}",
            str(target.id),
            format_local(target.added_at),
            format_local(scan.started_at) if scan else "-",
            scan_status_label(scan) if scan else "never scanned",
        )
    Console().print(table)
