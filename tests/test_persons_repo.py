import pytest
from sqlalchemy import select, text

from igft.db.persons import upsert_persons
from igft.db.tables import persons
from igft.domain import FollowerRecord

pytestmark = pytest.mark.db


def _person(engine, pk):
    with engine.connect() as conn:
        return conn.execute(select(persons).where(persons.c.id == pk)).one()


def _upsert(engine, *followers):
    with engine.begin() as conn:
        upsert_persons(conn, followers)


def test_new_person_has_no_tags_and_no_mark(db_engine):
    _upsert(db_engine, FollowerRecord(1, "alice", "Alice", False, True))
    row = _person(db_engine, 1)
    assert (row.username, row.full_name, row.is_private, row.is_verified) == ("alice", "Alice", False, True)
    assert row.tags == []
    assert row.is_marked is False


def test_username_change_overwrites_and_keeps_no_history(db_engine):
    _upsert(db_engine, FollowerRecord(1, "old_name", "Alice"))
    _upsert(db_engine, FollowerRecord(1, "new_name", "Alice B"))
    row = _person(db_engine, 1)
    assert (row.username, row.full_name) == ("new_name", "Alice B")
    with db_engine.connect() as conn:
        assert conn.execute(select(persons.c.id)).all() == [(1,)]
        assert "old_name" not in conn.execute(text("SELECT persons::text FROM persons")).scalar_one()


def test_field_missing_from_the_follower_list_keeps_its_stored_value(db_engine):
    _upsert(db_engine, FollowerRecord(1, "alice", "Alice", is_private=True, is_verified=True))
    _upsert(db_engine, FollowerRecord(1, "alice", "Alice", is_private=None, is_verified=None))
    row = _person(db_engine, 1)
    assert (row.is_private, row.is_verified) == (True, True)


def test_a_full_name_missing_from_the_follower_list_keeps_the_stored_one(db_engine):
    _upsert(db_engine, FollowerRecord(1, "alice", "Alice A."))
    _upsert(db_engine, FollowerRecord(1, "alice_renamed", None))
    row = _person(db_engine, 1)
    assert (row.username, row.full_name) == ("alice_renamed", "Alice A.")


def test_a_provided_full_name_replaces_the_stored_one(db_engine):
    _upsert(db_engine, FollowerRecord(1, "alice", "Alice A."))
    _upsert(db_engine, FollowerRecord(1, "alice", "Alice B."))
    assert _person(db_engine, 1).full_name == "Alice B."


def test_a_provided_flag_replaces_the_stored_one(db_engine):
    _upsert(db_engine, FollowerRecord(1, "alice", is_private=True, is_verified=False))
    _upsert(db_engine, FollowerRecord(1, "alice", is_private=False, is_verified=True))
    row = _person(db_engine, 1)
    assert (row.is_private, row.is_verified) == (False, True)


def test_tagged_and_marked_person_keeps_both_after_an_upsert(db_engine):
    _upsert(db_engine, FollowerRecord(1, "alice"))
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE persons SET tags = '{lab,pilot}', is_marked = true WHERE id = 1"))
    _upsert(db_engine, FollowerRecord(1, "alice_renamed", "Alice"))
    row = _person(db_engine, 1)
    assert row.username == "alice_renamed"
    assert row.tags == ["lab", "pilot"]
    assert row.is_marked is True


def test_updated_at_moves_forward_and_first_stored_at_does_not(db_engine):
    _upsert(db_engine, FollowerRecord(1, "alice"))
    first = _person(db_engine, 1)
    _upsert(db_engine, FollowerRecord(1, "alice"))
    second = _person(db_engine, 1)
    assert second.first_stored_at == first.first_stored_at
    assert second.updated_at > first.updated_at


def test_many_followers_in_one_page(db_engine):
    _upsert(db_engine, *[FollowerRecord(i, f"user{i}") for i in range(1, 13)])
    _upsert(db_engine, FollowerRecord(12, "user12"), FollowerRecord(13, "user13"))
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM persons")).scalar_one() == 13


def test_empty_page_writes_nothing(db_engine):
    _upsert(db_engine)
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM persons")).scalar_one() == 0
