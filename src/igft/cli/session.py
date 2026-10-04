"""`igft session ...`: the research account's saved Instagram session."""

from __future__ import annotations

import typer

from igft import sessionfile
from igft.cli import fetcher as fetcher_factory
from igft.cli.runtime import InstagramRun, instagram_command
from igft.cli.state import get_state
from igft.db import safety_state
from igft.domain import BlockSignal, FetchError, restore_session_hint

session_app = typer.Typer(help="Import and check the research account's Instagram session.", no_args_is_help=True)


def _verified_username(run: InstagramRun) -> str:
    """One session-check request, recorded whether it works or not."""
    backend = run.fetcher.backend
    try:
        username = run.fetcher.check_session()
    except (BlockSignal, FetchError) as exc:
        with run.engine.begin() as conn:
            safety_state.add_session_check(conn, backend=backend, succeeded=False, message=exc.raw_message)
        if isinstance(exc, FetchError):
            typer.echo(f"Session check failed. {restore_session_hint(backend)}", err=True)
        raise
    with run.engine.begin() as conn:
        safety_state.add_session_check(conn, backend=backend, succeeded=True, account_username=username)
    return username


@session_app.command("import")
def import_session(ctx: typer.Context) -> None:
    """Import the research account's Instagram login from Firefox and save it as the session.

    Firefox must be logged in to Instagram with the research account. One request confirms the session works;
    nothing is saved unless it does. An existing saved session is replaced. Never asks for a password.
    """
    from igft.fetchers.firefox import find_profile, read_instagram_cookies  # offline commands never load fetchers

    settings = get_state(ctx).settings
    cookies = read_instagram_cookies(find_profile(settings.firefox_profile))
    imported = fetcher_factory.create_fetcher_from_cookies(settings, cookies)
    with instagram_command(ctx, "session import", imported) as run:
        username = _verified_username(run)
    sessionfile.write_atomic(settings.instaloader_session_path, imported.session_bytes())
    typer.echo(f"Imported the Instagram session of @{username} from Firefox and saved it to {settings.instaloader_session_path}.")


@session_app.command("check")
def check(ctx: typer.Context) -> None:
    """Make one request to confirm the saved session works and show which account it belongs to.

    Never refused during a cooldown or hold. A successful check is what lifts a challenge hold.
    """
    with instagram_command(ctx, "session check") as run:
        username = _verified_username(run)
    typer.echo(f"The saved session works. It belongs to the account @{username}.")
