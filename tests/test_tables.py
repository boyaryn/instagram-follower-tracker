import pytest

from igft.config import ConfigError
from igft.db import tables
from igft.db.engine import normalise_url

EXPECTED_COLUMNS = {
    "targets": {"id", "username", "added_at"},
    "persons": {
        "id", "username", "full_name", "is_private", "is_verified",
        "tags", "is_marked", "first_stored_at", "updated_at",
    },
    "follows": {"target_id", "person_id", "first_seen_scan_id", "first_seen_at", "last_seen_scan_id", "last_seen_at"},
    "scans": {
        "id", "target_id", "backend", "mode", "is_baseline", "status", "stop_reason", "started_at", "ended_at",
        "runs", "pages_fetched", "followers_seen", "first_seen_followers", "cursor",
        "signal_type", "signal_message", "error_message",
    },
    "cooldowns": {"id", "kind", "raw_message", "source_scan_id", "command", "started_at", "ends_at",
                  "requires_session_check"},
    "session_checks": {"id", "backend", "checked_at", "succeeded", "account_username", "message"},
}


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_table_columns(table):
    assert set(tables.metadata.tables[table].columns.keys()) == EXPECTED_COLUMNS[table]


def test_no_profile_picture_column_anywhere():
    for table in tables.metadata.tables.values():
        assert not [c for c in table.columns.keys() if "pic" in c]


def test_user_owned_columns_have_defaults():
    persons = tables.persons
    assert not persons.c.tags.nullable and persons.c.tags.server_default is not None
    assert not persons.c.is_marked.nullable and persons.c.is_marked.server_default is not None


def test_follows_cascade_from_persons():
    (fk,) = tables.follows.c.person_id.foreign_keys
    assert fk.ondelete == "CASCADE"


def test_instagram_ids_are_primary_keys_without_autoincrement():
    assert tables.persons.c.id.autoincrement is False
    assert tables.targets.c.id.autoincrement is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u@localhost/db", "postgresql+psycopg://u@localhost/db"),
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u@h/db", "postgresql+psycopg://u@h/db"),
    ],
)
def test_normalise_url(url, expected):
    assert normalise_url(url) == expected


def test_non_postgres_url_rejected():
    with pytest.raises(ConfigError):
        normalise_url("sqlite:///x.db")
