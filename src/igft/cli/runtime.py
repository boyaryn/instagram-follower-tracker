"""What every command that talks to Instagram sets up: fetcher, database, lock and safety gate."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import typer
from sqlalchemy import Engine

from igft.cli import fetcher as fetcher_factory
from igft.cli.state import get_state
from igft.config import Settings
from igft.db.engine import engine_from_settings
from igft.domain import Fetcher
from igft.safety.gate import GuardedFetcher, SafetyGate
from igft.safety.lock import instagram_lock


@dataclass(frozen=True)
class InstagramRun:
    settings: Settings
    engine: Engine
    gate: SafetyGate
    fetcher: GuardedFetcher


@contextmanager
def instagram_command(ctx: typer.Context, command: str, fetcher: Fetcher | None = None) -> Iterator[InstagramRun]:
    """Yield a guarded fetcher while holding the Instagram advisory lock.

    The session is checked first, so a missing session is reported without needing the database.
    Then the database is opened, the lock taken and the command checked against cooldowns and holds.
    A `fetcher` that is already built (an unsaved session being imported) replaces the saved session.
    """
    settings = get_state(ctx).settings
    raw_fetcher = fetcher if fetcher is not None else fetcher_factory.create_fetcher(settings)
    engine = engine_from_settings(settings)
    try:
        with instagram_lock(engine):
            gate = SafetyGate(engine, settings.cooldown)
            gate.guard_command(command)
            yield InstagramRun(settings, engine, gate, GuardedFetcher(raw_fetcher, gate, command))
    finally:
        engine.dispose()
