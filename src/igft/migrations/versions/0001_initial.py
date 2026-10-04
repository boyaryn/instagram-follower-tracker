"""Initial schema (design D7) with the delete guard on persons (design D8).

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TIMESTAMPTZ = sa.TIMESTAMP(timezone=True)
NOW = sa.text("now()")

# Frozen copies: a migration must not change when the application enums do.
BACKENDS = ("instaloader",)
SIGNAL_KINDS = ("rate_limit", "challenge", "action_block", "session_rejected")
SCAN_MODES = ("default", "full")
SCAN_STATUSES = ("unfinished", "complete")
STOP_REASONS = (
    "end_of_list", "early_stop", "page_cap", "block_signal", "error", "list_unavailable", "interrupted",
)


def _one_of(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


GUARD_PERSON_DELETE = """
CREATE FUNCTION igft_guard_person_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(cardinality(OLD.tags), 0) > 0 OR OLD.is_marked THEN
        RAISE EXCEPTION 'person % has tags or is marked and cannot be deleted', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$
"""

GUARD_PERSONS_TRUNCATE = """
CREATE FUNCTION igft_guard_persons_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    protected boolean;
BEGIN
    EXECUTE format(
        'SELECT EXISTS (SELECT 1 FROM %I.%I WHERE coalesce(cardinality(tags), 0) > 0 OR is_marked)',
        TG_TABLE_SCHEMA, TG_TABLE_NAME
    ) INTO protected;
    IF protected THEN
        RAISE EXCEPTION 'persons has tagged or marked people and cannot be truncated'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        "targets",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("added_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_targets")),
    )

    op.create_table(
        "persons",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("is_private", sa.Boolean(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("is_marked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("first_stored_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_persons")),
    )

    op.create_table(
        "scans",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("backend", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("is_baseline", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'unfinished'"), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("started_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.Column("ended_at", TIMESTAMPTZ, nullable=True),
        sa.Column("runs", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("pages_fetched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("followers_seen", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("first_seen_followers", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("signal_type", sa.Text(), nullable=True),
        sa.Column("signal_message", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(_one_of("backend", BACKENDS), name=op.f("ck_scans_backend")),
        sa.CheckConstraint(_one_of("mode", SCAN_MODES), name=op.f("ck_scans_mode")),
        sa.CheckConstraint(_one_of("status", SCAN_STATUSES), name=op.f("ck_scans_status")),
        sa.CheckConstraint(_one_of("stop_reason", STOP_REASONS), name=op.f("ck_scans_stop_reason")),
        sa.CheckConstraint(_one_of("signal_type", SIGNAL_KINDS), name=op.f("ck_scans_signal_type")),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"], name=op.f("fk_scans_target_id_targets")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scans")),
    )
    op.create_index(
        "uq_scans_one_unfinished_per_target",
        "scans",
        ["target_id"],
        unique=True,
        postgresql_where=sa.text("status = 'unfinished'"),
    )

    op.create_table(
        "follows",
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.Column("first_seen_scan_id", sa.BigInteger(), nullable=False),
        sa.Column("first_seen_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.Column("last_seen_scan_id", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"], name=op.f("fk_follows_target_id_targets")),
        sa.ForeignKeyConstraint(
            ["person_id"], ["persons.id"], name=op.f("fk_follows_person_id_persons"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_scan_id"], ["scans.id"], name=op.f("fk_follows_first_seen_scan_id_scans")
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_scan_id"], ["scans.id"], name=op.f("fk_follows_last_seen_scan_id_scans")
        ),
        sa.PrimaryKeyConstraint("target_id", "person_id", name=op.f("pk_follows")),
    )
    op.create_index("ix_follows_target_id_first_seen_scan_id", "follows", ["target_id", "first_seen_scan_id"])

    op.create_table(
        "cooldowns",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("raw_message", sa.Text(), nullable=True),
        sa.Column("source_scan_id", sa.BigInteger(), nullable=True),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("started_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.Column("ends_at", TIMESTAMPTZ, nullable=False),
        sa.Column("requires_session_check", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint(_one_of("kind", SIGNAL_KINDS), name=op.f("ck_cooldowns_kind")),
        sa.ForeignKeyConstraint(
            ["source_scan_id"], ["scans.id"], name=op.f("fk_cooldowns_source_scan_id_scans")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cooldowns")),
    )

    op.create_table(
        "session_checks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("backend", sa.Text(), nullable=False),
        sa.Column("checked_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("account_username", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.CheckConstraint(_one_of("backend", BACKENDS), name=op.f("ck_session_checks_backend")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_checks")),
    )

    # Delete guard (design D8): the row trigger covers DELETE, the statement trigger covers TRUNCATE,
    # which bypasses row triggers.
    op.execute(GUARD_PERSON_DELETE)
    op.execute(
        "CREATE TRIGGER persons_guard_delete BEFORE DELETE ON persons "
        "FOR EACH ROW EXECUTE FUNCTION igft_guard_person_delete()"
    )
    op.execute(GUARD_PERSONS_TRUNCATE)
    op.execute(
        "CREATE TRIGGER persons_guard_truncate BEFORE TRUNCATE ON persons "
        "FOR EACH STATEMENT EXECUTE FUNCTION igft_guard_persons_truncate()"
    )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to downgrade the initial schema: it would drop persons.tags and persons.is_marked, "
        "which belong to the user. Restore from a backup or drop the tables by hand if you really mean it."
    )
