"""One process at a time may talk to Instagram (design D10)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

from igft.domain import CommandRefused

# A fixed key for pg_try_advisory_lock; "igft" as ASCII bytes.
LOCK_KEY = 0x69676674


@contextmanager
def instagram_lock(engine: Engine) -> Iterator[None]:
    """Hold a session-level advisory lock while the body runs.

    A second igft command gets `CommandRefused` (exit code 3) instead of doubling the request rate.
    """
    with engine.connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}).scalar_one()
        conn.commit()
        if not acquired:
            raise CommandRefused("Another igft command is using Instagram. Wait for it to finish, then try again.")
        try:
            yield
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
            conn.commit()
