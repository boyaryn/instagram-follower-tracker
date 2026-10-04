"""Engine setup from the configured database URL."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from igft.config import ConfigError, Settings


def normalise_url(url: str) -> str:
    """Use the psycopg 3 driver for plain `postgresql://` URLs (SQLAlchemy would pick psycopg2)."""
    parsed = make_url(url)
    if parsed.drivername in ("postgresql", "postgres"):
        parsed = parsed.set(drivername="postgresql+psycopg")
    if not parsed.drivername.startswith("postgresql"):
        raise ConfigError(f"database_url must be a PostgreSQL URL, got driver {parsed.drivername!r}")
    return parsed.render_as_string(hide_password=False)


def engine_from_url(url: str) -> Engine:
    return create_engine(normalise_url(url), future=True)


def engine_from_settings(settings: Settings) -> Engine:
    if not settings.database_url:
        raise ConfigError("database_url is not set: add it to the config file or set IGFT_DATABASE_URL.")
    return engine_from_url(settings.database_url)
