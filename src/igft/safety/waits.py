"""Reading a stated wait out of Instagram's raw message (design D10)."""

from __future__ import annotations

import re
from datetime import timedelta

_UNITS = {
    "second": timedelta(seconds=1),
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
}

# "wait 48 hours", "wait for at least 30 minutes", "try again in 2 days", "try again after an hour"
_PATTERN = re.compile(
    r"\b(?:wait(?:\s+for)?(?:\s+(?:at\s+least|another|about))?|try\s+again\s+(?:in|after))\s+"
    r"(?P<amount>\d+(?:\.\d+)?|an?)\s*(?P<unit>second|minute|hour|day)s?\b",
    re.IGNORECASE,
)


def parse_stated_wait(message: str) -> timedelta | None:
    """The wait a message states, or None when it states none (for example "please wait a few minutes")."""
    match = _PATTERN.search(message)
    if match is None:
        return None
    amount = match["amount"].lower()
    count = 1.0 if amount in ("a", "an") else float(amount)
    return count * _UNITS[match["unit"].lower()]
