"""Randomised delay between requests (design D11)."""

from __future__ import annotations

import random
import time
from collections.abc import Callable


class Pacer:
    """Keeps the gap between the *starts* of consecutive requests within [min_s, max_s].

    Pacing from the previous request's start means a slow database commit counts towards the
    delay instead of being added on top of it. The first request of a run does not wait.
    `clock`, `sleep` and `rng` are injectable so tests run instantly.
    """

    def __init__(
        self,
        min_s: float,
        max_s: float,
        *,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_s < 0 or max_s < 0 or min_s > max_s:
            raise ValueError(f"invalid delay range: {min_s}..{max_s}")
        self._min = min_s
        self._max = max_s
        self._rng = rng or random.Random()
        self._clock = clock
        self._sleep = sleep
        self._last_start: float | None = None
        self._planned_gap: float | None = None

    def plan_next(self) -> float:
        """Draw the gap before the next request now, and return the seconds `wait()` would still sleep.

        `wait()` uses the drawn gap, so the gaps stay within [min_s, max_s] whether or not this is called.
        """
        if self._last_start is None:
            return 0.0
        if self._planned_gap is None:
            self._planned_gap = self._rng.uniform(self._min, self._max)
        return max(0.0, self._last_start + self._planned_gap - self._clock())

    def wait(self) -> None:
        """Call immediately before each request."""
        now = self._clock()
        if self._last_start is None:
            self._last_start = now
            return
        gap = self._planned_gap if self._planned_gap is not None else self._rng.uniform(self._min, self._max)
        self._planned_gap = None
        due = self._last_start + gap
        if now < due:
            self._sleep(due - now)
            self._last_start = due
        else:
            self._last_start = now
