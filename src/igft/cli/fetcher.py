"""Builds the fetcher for commands that talk to Instagram.

Commands call `create_fetcher` through this module, so tests replace it with a fake. The instaloader
adapter is imported only here, after the session file is known to exist, so offline commands never
load it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from igft import sessionfile
from igft.config import Settings
from igft.domain import Backend, Fetcher, SessionMissing

if TYPE_CHECKING:
    from igft.fetchers.instaloader_adapter import InstaloaderFetcher


def create_fetcher(settings: Settings) -> Fetcher:
    path = settings.instaloader_session_path
    if not sessionfile.exists(path):
        raise SessionMissing(Backend.INSTALOADER, f"No saved Instagram session at {path}.")
    warning = sessionfile.permission_warning(path)
    if warning:
        typer.echo(warning, err=True)

    from igft.fetchers.instaloader_adapter import InstaloaderFetcher

    return InstaloaderFetcher.from_settings(settings)


def create_fetcher_from_cookies(settings: Settings, cookies: dict[str, str]) -> InstaloaderFetcher:
    """A fetcher for a session that has not been saved yet (`session import`)."""
    from igft.fetchers.instaloader_adapter import InstaloaderFetcher

    return InstaloaderFetcher.from_cookies(cookies, settings.instaloader_user_agent)
