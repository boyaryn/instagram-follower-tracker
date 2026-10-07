"""Dataclasses and errors shared by all layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable


class Backend(StrEnum):
    INSTALOADER = "instaloader"


class SignalKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    CHALLENGE = "challenge"
    ACTION_BLOCK = "action_block"
    SESSION_REJECTED = "session_rejected"


@dataclass(frozen=True)
class FollowerRecord:
    """One follower as the follower list returns it. `None` means the backend did not provide the field."""

    pk: int
    username: str
    full_name: str | None = None
    is_private: bool | None = None
    is_verified: bool | None = None


@dataclass(frozen=True)
class FollowerPage:
    """One page of a follower list. `next_cursor is None` means the list has ended."""

    followers: tuple[FollowerRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class ProfileInfo:
    pk: int
    username: str


@runtime_checkable
class Fetcher(Protocol):
    """Every method makes exactly one Instagram request."""

    backend: Backend

    def get_profile(self, username: str) -> ProfileInfo: ...

    def fetch_followers_page(self, user_id: int, cursor: str | None, *, username: str) -> FollowerPage:
        """`username` is the target's current username; the web app's followers request names it in its `Referer`."""
        ...

    def check_session(self) -> str:
        """Return the username of the account the session belongs to."""
        ...


def restore_session_hint(backend: Backend) -> str:
    return "Log in to Instagram in Firefox with the research account, then run `igft session import`."


class IgftError(Exception):
    """Base class for errors the CLI reports to the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CommandRefused(IgftError):
    """A command was refused before any request: a cooldown or hold is active, or another command holds the lock."""


class FetchError(IgftError):
    """An Instagram request failed for a reason that is not a block signal."""

    def __init__(self, raw_message: str) -> None:
        super().__init__(f"Instagram request failed: {raw_message}")
        self.raw_message = raw_message


class BlockSignal(IgftError):
    """Instagram asked us to back off, verify the account, or log in again."""

    def __init__(
        self, kind: SignalKind, raw_message: str, stated_wait: timedelta | None = None, *, withheld_list: bool = False
    ) -> None:
        super().__init__(f"Instagram returned a {kind.value.replace('_', ' ')} signal: {raw_message}")
        self.kind = kind
        self.raw_message = raw_message
        self.stated_wait = stated_wait
        self.withheld_list = withheld_list  # selects the guidance text only; not stored
        self.guidance: str | None = None  # what to do next; set once the signal has been recorded


class SessionMissing(IgftError):
    """No usable saved session exists for a backend. Raised before any request is made."""

    def __init__(self, backend: Backend, detail: str | None = None) -> None:
        prefix = detail or f"No saved {backend.value} session."
        super().__init__(f"{prefix} {restore_session_hint(backend)}")
        self.backend = backend


class ProfileNotFound(IgftError):
    def __init__(self, username: str) -> None:
        super().__init__(f"No Instagram profile named {username!r} exists.")
        self.username = username


class UnknownTarget(IgftError):
    def __init__(self, username: str) -> None:
        super().__init__(f"{username!r} is not a target. Add it first with `igft target add {username}`.")
