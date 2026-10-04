"""`igft db ...`: database setup."""

from __future__ import annotations

import typer

from igft.cli.state import get_state
from igft.db import migrate
from igft.db.engine import engine_from_settings

db_app = typer.Typer(help="Set up and migrate the PostgreSQL database.", no_args_is_help=True)


@db_app.command("upgrade")
def upgrade(ctx: typer.Context) -> None:
    """Create the schema, or bring it to the latest version. Safe to run again."""
    engine = engine_from_settings(get_state(ctx).settings)
    try:
        before = migrate.current_revision(engine)
        migrate.upgrade(engine)
        after = migrate.current_revision(engine)
    finally:
        engine.dispose()
    if before == after:
        typer.echo(f"Database schema is already up to date (revision {after}).")
    else:
        typer.echo(f"Database schema upgraded from {before or 'an empty database'} to revision {after}.")
