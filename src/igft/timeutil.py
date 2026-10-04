"""Timestamps are stored in UTC and shown in the user's local time zone (design D13)."""

from __future__ import annotations

from datetime import datetime


def format_local(moment: datetime) -> str:
    return moment.astimezone().strftime("%Y-%m-%d %H:%M %Z")
