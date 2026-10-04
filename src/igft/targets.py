"""Registering the profiles whose followers are tracked."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from sqlalchemy import Engine

from igft.db import scans as scans_repo
from igft.db import targets as targets_repo
from igft.domain import Fetcher


class AddOutcome(enum.StrEnum):
    ADDED = "added"
    EXISTS = "exists"
    RENAMED = "renamed"


@dataclass(frozen=True)
class AddResult:
    outcome: AddOutcome
    target: targets_repo.Target
    old_username: str | None = None


class TargetService:
    def __init__(self, engine: Engine, fetcher: Fetcher) -> None:
        self._engine = engine
        self._fetcher = fetcher

    def add(self, username: str) -> AddResult:
        """Resolve `username` with one profile request and store it, keyed by its numeric ID.

        Whether the profile is private or followed by the research account is not checked: the
        profile page does not say. A profile that is already a target is reported, and a changed
        username on an existing target is updated.
        """
        profile = self._fetcher.get_profile(username.strip().removeprefix("@"))
        with self._engine.begin() as conn:
            existing = targets_repo.get_target_by_id(conn, profile.pk)
            if existing is None:
                return AddResult(AddOutcome.ADDED, targets_repo.add_target(conn, profile.pk, profile.username))
            if existing.username == profile.username:
                return AddResult(AddOutcome.EXISTS, existing)
            targets_repo.rename_target(conn, profile.pk, profile.username)
            renamed = targets_repo.get_target_by_id(conn, profile.pk)
            return AddResult(AddOutcome.RENAMED, renamed, old_username=existing.username)


def targets_with_latest_scan(engine: Engine) -> list[tuple[targets_repo.Target, scans_repo.Scan | None]]:
    """Every target with its most recent scan, read from the database only."""
    with engine.connect() as conn:
        return [(t, scans_repo.latest_scan(conn, t.id)) for t in targets_repo.list_targets(conn)]


def scan_status_label(scan: scans_repo.Scan) -> str:
    """e.g. "complete (early stop)"; an unfinished scan with no recorded reason was interrupted."""
    reason = scan.stop_reason or ("interrupted" if scan.status == "unfinished" else None)
    return f"{scan.status} ({reason.replace('_', ' ')})" if reason else scan.status
