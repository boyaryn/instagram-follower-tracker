"""The single choke point for Instagram requests (design D10)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import TypeVar

from sqlalchemy import Engine

from igft.db import safety_state
from igft.db import scans as scans_repo
from igft.domain import (
    BlockSignal,
    CommandRefused,
    Fetcher,
    FollowerPage,
    ProfileInfo,
    SignalKind,
    restore_session_hint,
)
from igft.domain import Backend
from igft.timeutil import format_local

# Restoring and verifying a session is how a hold is lifted, so these are never refused.
SESSION_COMMANDS = frozenset({"session check", "session import"})

T = TypeVar("T")


def _kind_label(kind: str) -> str:
    return kind.replace("_", " ")


class SafetyGate:
    """Checks cooldowns and holds against the database's `now()` and records block signals."""

    def __init__(self, engine: Engine, cooldown: timedelta) -> None:
        self._engine = engine
        self._cooldown = cooldown

    def guard_command(self, command: str) -> None:
        """Raise `CommandRefused` if `command` may not talk to Instagram right now."""
        if command in SESSION_COMMANDS:
            return
        with self._engine.connect() as conn:
            active = safety_state.active_cooldowns(conn)
            unchecked = safety_state.holds_missing_check(conn)
        if not active and not unchecked:
            return

        parts: list[str] = []
        if active:
            latest = active[0]
            parts.append(
                f"Instagram requests are paused: a {_kind_label(latest.kind)} cooldown is active until "
                f"{format_local(latest.ends_at)}."
            )
        if unchecked:
            hold = unchecked[0]
            if active:
                parts.append(f"After a {_kind_label(hold.kind)} the account is also on hold until a session check succeeds.")
            else:
                parts.append(
                    f"The account is on hold after a {_kind_label(hold.kind)} on {format_local(hold.started_at)}. "
                    "The cooldown has ended, but no successful session check has run since."
                )
            parts.append("Verify the account by hand (for example in Firefox), then run `igft session check`.")
        raise CommandRefused(" ".join(parts))

    def record_signal(self, signal: BlockSignal, *, command: str, scan_id: int | None) -> str:
        """Record `signal` in its own transaction and return what the user should do next.

        It is a separate transaction so the signal is saved even if the page transaction rolls back.
        """
        with self._engine.begin() as conn:
            if scan_id is not None:
                scans_repo.record_signal(conn, scan_id, signal.kind, signal.raw_message)
            cooldown = None
            if signal.kind is not SignalKind.SESSION_REJECTED:
                duration = max(self._cooldown, signal.stated_wait or timedelta(0))
                cooldown = safety_state.add_cooldown(
                    conn,
                    kind=signal.kind,
                    raw_message=signal.raw_message,
                    source_scan_id=scan_id,
                    command=command,
                    duration=duration,
                    requires_session_check=signal.kind is SignalKind.CHALLENGE,
                )
        return guidance_for(signal.kind, cooldown.ends_at if cooldown else None)


def guidance_for(kind: SignalKind, ends_at) -> str:
    if kind is SignalKind.SESSION_REJECTED:
        return (
            f"The saved session was rejected. {restore_session_hint(Backend.INSTALOADER)} "
            "No cooldown was set: once the session is restored you can run `igft resume` straight away."
        )
    until = format_local(ends_at)
    if kind is SignalKind.CHALLENGE:
        return (
            "Instagram asked the account to verify itself. Verify the account by hand (for example in "
            "Firefox), restore the session if needed, and then run `igft session check`. Scanning stays "
            f"on hold until {until} and until a session check succeeds."
        )
    return f"Scanning is paused until {until}. You can continue the scan with `igft resume` after that."


class GuardedFetcher:
    """Wraps a fetcher so every call goes through the gate and every block signal is recorded once.

    Nothing is retried: a `BlockSignal` is recorded and raised again, with its `guidance` set.
    Set `scan_id` once a scan exists so signals are written on it.
    """

    def __init__(self, fetcher: Fetcher, gate: SafetyGate, command: str) -> None:
        self._fetcher = fetcher
        self._gate = gate
        self._command = command
        self.scan_id: int | None = None

    @property
    def backend(self) -> Backend:
        return self._fetcher.backend

    def _call(self, request: Callable[[], T]) -> T:
        self._gate.guard_command(self._command)
        try:
            return request()
        except BlockSignal as signal:
            signal.guidance = self._gate.record_signal(signal, command=self._command, scan_id=self.scan_id)
            raise

    def get_profile(self, username: str) -> ProfileInfo:
        return self._call(lambda: self._fetcher.get_profile(username))

    def fetch_followers_page(self, user_id: int, cursor: str | None, *, username: str) -> FollowerPage:
        return self._call(lambda: self._fetcher.fetch_followers_page(user_id, cursor, username=username))

    def check_session(self) -> str:
        return self._call(self._fetcher.check_session)
