"""Table metadata (design D7). The schema itself is created only by the Alembic migrations.

`persons.tags` and `persons.is_marked` belong to the user: they are edited in the user's SQL
client and the tool never writes them (design D8).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP

from igft.domain import Backend, SignalKind

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

TIMESTAMPTZ = TIMESTAMP(timezone=True)
NOW = text("now()")

SCAN_MODES = ("default", "full")
SCAN_STATUSES = ("unfinished", "complete")
STOP_REASONS = (
    "end_of_list", "early_stop", "page_cap", "block_signal", "error", "list_unavailable", "interrupted",
)


def _one_of(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(str(v)) for v in values)})"


targets = Table(
    "targets",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=False),  # Instagram pk
    Column("username", Text, nullable=False),
    Column("added_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
)

persons = Table(
    "persons",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=False),  # Instagram pk
    Column("username", Text, nullable=False),
    Column("full_name", Text),
    Column("is_private", Boolean),
    Column("is_verified", Boolean),
    # User-owned columns: never written by the tool.
    Column("tags", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("is_marked", Boolean, nullable=False, server_default=text("false")),
    Column("first_stored_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
)

scans = Table(
    "scans",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("target_id", BigInteger, ForeignKey("targets.id"), nullable=False),
    Column("backend", Text, nullable=False),
    Column("mode", Text, nullable=False),
    Column("is_baseline", Boolean, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'unfinished'")),
    Column("stop_reason", Text),
    Column("started_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    Column("ended_at", TIMESTAMPTZ),
    Column("runs", Integer, nullable=False, server_default=text("1")),
    Column("pages_fetched", Integer, nullable=False, server_default=text("0")),
    Column("followers_seen", Integer, nullable=False, server_default=text("0")),
    Column("first_seen_followers", Integer, nullable=False, server_default=text("0")),
    Column("cursor", Text),
    Column("signal_type", Text),
    Column("signal_message", Text),
    Column("error_message", Text),
    CheckConstraint(_one_of("backend", Backend), name="backend"),
    CheckConstraint(_one_of("mode", SCAN_MODES), name="mode"),
    CheckConstraint(_one_of("status", SCAN_STATUSES), name="status"),
    CheckConstraint(_one_of("stop_reason", STOP_REASONS), name="stop_reason"),
    CheckConstraint(_one_of("signal_type", SignalKind), name="signal_type"),
    # At most one unfinished scan per target, enforced by the database.
    Index(
        "uq_scans_one_unfinished_per_target",
        "target_id",
        unique=True,
        postgresql_where=text("status = 'unfinished'"),
    ),
)

follows = Table(
    "follows",
    metadata,
    Column("target_id", BigInteger, ForeignKey("targets.id"), nullable=False),
    Column("person_id", BigInteger, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
    Column("first_seen_scan_id", BigInteger, ForeignKey("scans.id"), nullable=False),
    Column("first_seen_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    Column("last_seen_scan_id", BigInteger, ForeignKey("scans.id"), nullable=False),
    Column("last_seen_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    PrimaryKeyConstraint("target_id", "person_id"),
    Index("ix_follows_target_id_first_seen_scan_id", "target_id", "first_seen_scan_id"),
)

cooldowns = Table(
    "cooldowns",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("kind", Text, nullable=False),
    Column("raw_message", Text),
    Column("source_scan_id", BigInteger, ForeignKey("scans.id")),
    Column("command", Text, nullable=False),
    Column("started_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    Column("ends_at", TIMESTAMPTZ, nullable=False),
    Column("requires_session_check", Boolean, nullable=False, server_default=text("false")),
    CheckConstraint(_one_of("kind", SignalKind), name="kind"),
)

session_checks = Table(
    "session_checks",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("backend", Text, nullable=False),
    Column("checked_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    Column("succeeded", Boolean, nullable=False),
    Column("account_username", Text),
    Column("message", Text),
    CheckConstraint(_one_of("backend", Backend), name="backend"),
)
