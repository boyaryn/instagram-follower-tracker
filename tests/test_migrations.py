import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from dbutil import SCHEMA_PREFIX, migrated_schema, schema_exists
from igft.db import migrate, tables

RESTRICT_VIOLATION = "23001"


# --- 4.2: the Alembic environment is packaged and needs no alembic.ini -------------------------------------


def test_scripts_are_found_from_an_unrelated_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scripts = ScriptDirectory.from_config(migrate.alembic_config())
    assert scripts.get_heads() == ["0001"]
    assert not list(tmp_path.iterdir())


@pytest.mark.db
def test_upgrade_run_from_an_unrelated_directory_finds_the_scripts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with migrated_schema() as schema, schema.engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001"
    assert not list(tmp_path.iterdir())


# --- 4.3: a throwaway migrated schema per test -------------------------------------------------------------


def _store_a_person(engine):
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM persons")).scalar_one() == 0
        conn.execute(text("INSERT INTO persons (id, username) VALUES (1, 'left_behind')"))


@pytest.mark.db
def test_each_db_test_starts_from_an_empty_schema_a(db_engine):
    _store_a_person(db_engine)


@pytest.mark.db
def test_each_db_test_starts_from_an_empty_schema_b(db_engine):
    _store_a_person(db_engine)


@pytest.mark.db
def test_throwaway_schema_is_dropped_afterwards():
    with migrated_schema() as schema:
        assert schema.name.startswith(SCHEMA_PREFIX)
        assert schema_exists(schema.name)
    assert not schema_exists(schema.name)


# --- 4.4: the initial migration matches the table metadata -------------------------------------------------


@pytest.mark.db
def test_migrated_schema_has_no_drift_from_the_metadata(db_engine):
    with db_engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True, "compare_server_default": True})
        assert compare_metadata(context, tables.metadata) == []


@pytest.mark.db
def test_migrated_schema_has_every_table_and_the_unfinished_scan_index(db_engine):
    inspector = inspect(db_engine)
    assert set(tables.metadata.tables) <= set(inspector.get_table_names())
    (index,) = [i for i in inspector.get_indexes("scans") if i["name"] == "uq_scans_one_unfinished_per_target"]
    assert index["unique"] and index["column_names"] == ["target_id"]
    assert "unfinished" in index["dialect_options"]["postgresql_where"]


@pytest.mark.db
def test_user_owned_columns_default_to_no_tags_and_unmarked(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text("INSERT INTO persons (id, username) VALUES (1, 'a')"))
        assert conn.execute(text("SELECT tags, is_marked FROM persons")).one() == ([], False)


@pytest.mark.db
def test_upgrading_twice_changes_nothing(db_engine):
    migrate.upgrade(db_engine)
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 1


# --- 4.5: the delete guard ---------------------------------------------------------------------------------


def _person(conn, pk, *, tags="{}", marked=False):
    conn.execute(
        text("INSERT INTO persons (id, username, tags, is_marked) VALUES (:id, :u, :t, :m)"),
        {"id": pk, "u": f"user{pk}", "t": tags, "m": marked},
    )


def _guard_error(engine, statement):
    with pytest.raises(DBAPIError) as caught:
        with engine.begin() as conn:
            conn.execute(text(statement))
    assert caught.value.orig.sqlstate == RESTRICT_VIOLATION


def _person_count(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM persons")).scalar_one()


@pytest.mark.db
def test_deleting_a_tagged_person_fails(db_engine):
    with db_engine.begin() as conn:
        _person(conn, 1, tags="{lab,pilot}")
    _guard_error(db_engine, "DELETE FROM persons WHERE id = 1")
    assert _person_count(db_engine) == 1


@pytest.mark.db
def test_deleting_a_marked_person_fails(db_engine):
    with db_engine.begin() as conn:
        _person(conn, 1, marked=True)
    _guard_error(db_engine, "DELETE FROM persons WHERE id = 1")
    assert _person_count(db_engine) == 1


@pytest.mark.db
def test_tags_cannot_be_set_to_null_by_default(db_engine):
    with db_engine.begin() as conn:
        _person(conn, 1)
    with pytest.raises(DBAPIError):
        with db_engine.begin() as conn:
            conn.execute(text("UPDATE persons SET tags = NULL"))


@pytest.mark.db
def test_deleting_a_marked_person_whose_tags_are_null_fails(db_engine):
    # The column is NOT NULL, so the guard's null handling only matters if someone drops that constraint.
    with db_engine.begin() as conn:
        conn.execute(text("ALTER TABLE persons ALTER COLUMN tags DROP NOT NULL"))
        _person(conn, 1, marked=True)
        conn.execute(text("UPDATE persons SET tags = NULL"))
    _guard_error(db_engine, "DELETE FROM persons WHERE id = 1")
    assert _person_count(db_engine) == 1


@pytest.mark.db
def test_deleting_an_unmarked_person_whose_tags_are_null_succeeds(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text("ALTER TABLE persons ALTER COLUMN tags DROP NOT NULL"))
        _person(conn, 1)
        conn.execute(text("UPDATE persons SET tags = NULL"))
    with db_engine.begin() as conn:
        conn.execute(text("DELETE FROM persons WHERE id = 1"))
    assert _person_count(db_engine) == 0


def _target_scan_and_follow(conn, person_id):
    conn.execute(text("INSERT INTO targets (id, username) VALUES (10, 'target') ON CONFLICT DO NOTHING"))
    scan_id = conn.execute(
        text(
            "INSERT INTO scans (target_id, backend, mode, is_baseline) "
            "VALUES (10, 'instaloader', 'default', true) ON CONFLICT DO NOTHING RETURNING id"
        )
    ).scalar()
    if scan_id is None:
        scan_id = conn.execute(text("SELECT id FROM scans WHERE target_id = 10")).scalar_one()
    conn.execute(
        text(
            "INSERT INTO follows (target_id, person_id, first_seen_scan_id, last_seen_scan_id) "
            "VALUES (10, :p, :s, :s)"
        ),
        {"p": person_id, "s": scan_id},
    )


@pytest.mark.db
def test_deleting_an_untagged_unmarked_person_succeeds_and_cascades_their_follows(db_engine):
    with db_engine.begin() as conn:
        _person(conn, 1)
        _person(conn, 2)
        _target_scan_and_follow(conn, 1)
        _target_scan_and_follow(conn, 2)
    with db_engine.begin() as conn:
        conn.execute(text("DELETE FROM persons WHERE id = 1"))
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT person_id FROM follows")).scalars().all() == [2]
    assert _person_count(db_engine) == 1


@pytest.mark.db
def test_truncating_persons_fails_while_a_tagged_person_exists(db_engine):
    with db_engine.begin() as conn:
        _person(conn, 1, tags="{lab}")
        _person(conn, 2)
    _guard_error(db_engine, "TRUNCATE persons, follows")
    _guard_error(db_engine, "TRUNCATE persons CASCADE")
    assert _person_count(db_engine) == 2


@pytest.mark.db
def test_truncating_persons_fails_while_a_marked_person_exists(db_engine):
    with db_engine.begin() as conn:
        _person(conn, 1, marked=True)
    _guard_error(db_engine, "TRUNCATE persons, follows")
    assert _person_count(db_engine) == 1


@pytest.mark.db
def test_truncating_persons_succeeds_when_nobody_is_tagged_or_marked(db_engine):
    with db_engine.begin() as conn:
        _person(conn, 1)
        _target_scan_and_follow(conn, 1)
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE persons, follows"))
    assert _person_count(db_engine) == 0


# --- 4.6: no downgrade may drop the user's columns ---------------------------------------------------------


@pytest.mark.db
def test_downgrade_raises_and_leaves_the_tags_intact(db_engine):
    with db_engine.begin() as conn:
        _person(conn, 1, tags="{lab,pilot}", marked=True)
    with pytest.raises(RuntimeError, match="tags"):
        migrate.downgrade(db_engine, "base")
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT tags, is_marked FROM persons")).one() == (["lab", "pilot"], True)
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001"
