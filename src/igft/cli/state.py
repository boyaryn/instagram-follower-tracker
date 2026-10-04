"""Per-invocation state shared by all commands."""

from __future__ import annotations

from dataclasses import dataclass

import typer

from igft.config import Settings


@dataclass(frozen=True)
class AppState:
    settings: Settings


def get_state(ctx: typer.Context) -> AppState:
    state = ctx.find_root().obj
    assert isinstance(state, AppState), "settings are loaded by the root callback"
    return state
