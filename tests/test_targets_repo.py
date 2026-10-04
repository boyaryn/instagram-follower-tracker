import inspect
from datetime import timedelta

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError

from igft.db import targets as targets_repo
from igft.db.safety_state import (
    active_cooldowns,
    add_cooldown,
    add_session_check,
    latest_successful_check,
)
from igft.db.scans import create_scan
from igft.db.tables import cooldowns
from igft.db.targets import (
    add_target,
    get_target_by_id,
    get_target_by_username,
    list_targets,
    rename_target,
)
from igft.domain import Backend, SignalKind

pytestmark = pytest.mark.db


# --- targets -----------------------------------------------------------------------------------------------


def test_add_and_look_up_a_target(db_engine):
    with db_engine.begin() as conn:
        added = add_target(conn, 100, "research_target")
    with db_engine.connect() as conn:
        assert get_target_by_id(conn, 100) == added
        assert get_target_by_username(conn, "research_target") == added
    assert (added.id, added.username) == (100, "research_target")
    assert added.added_at is not None


def test_unknown_target_is_none(db_engine):
    with db_engine.connect() as conn:
        assert get_target_by_id(conn, 1) is None
        assert get_target_by_username(conn, "nobody") is None


def test_adding_the_same_id_twice_is_rejected(db_engine):
    with db_engine.begin() as conn:
        add_target(conn, 100, "one")
    with pytest.raises(IntegrityError):
        with db_engine.begin() as conn:
            add_target(conn, 100, "one_again")


def test_rename_keeps_the_id_and_added_date(db_engine):
    with db_engine.begin() as conn:
        added = add_target(conn, 100, "old_name")
    with db_engine.begin() as conn:
        rename_target(conn, 100, "new_name")
    with db_engine.connect() as conn:
        renamed = get_target_by_id(conn, 100)
        assert get_target_by_username(conn, "old_name") is None
    assert (renamed.id, renamed.username, renamed.added_at) == (100, "new_name", added.added_at)


def test_list_targets_in_the_order_they_were_added(db_engine):
    with db_engine.begin() as conn:
        add_target(conn, 300, "first")
    with db_engine.begin() as conn:
        add_target(conn, 100, "second")
    with db_engine.connect() as conn:
        assert [t.username for t in list_targets(conn)] == ["first", "second"]


def test_list_of_no_targets_is_empty(db_engine):
    with db_engine.connect() as conn:
        assert list_targets(conn) == []


def test_targets_repository_has_no_delete_operation():
    functions = inspect.getmembers(targets_repo, inspect.isfunction)
    own = [name for name, fn in functions if fn.__module__ == targets_repo.__name__]
    assert not [name for name in own if "delete" in name or "remove" in name]


# --- cooldowns ---------------------------------------------------------------------------------------------


def test_new_cooldown_is_active_until_its_end(db_engine):
    with db_engine.begin() as conn:
        scan_target = add_target(conn, 100, "target")
        scan = create_scan(conn, scan_target.id, Backend.INSTALOADER, "default")
        added = add_cooldown(
            conn,
            kind=SignalKind.RATE_LIMIT,
            raw_message="Please wait a few minutes",
            source_scan_id=scan.id,
            command="scan",
            duration=timedelta(hours=24),
        )
    with db_engine.connect() as conn:
        assert active_cooldowns(conn) == [added]
    assert added.kind == "rate_limit"
    assert added.raw_message == "Please wait a few minutes"
    assert (added.source_scan_id, added.command, added.requires_session_check) == (scan.id, "scan", False)
    assert added.ends_at - added.started_at == timedelta(hours=24)


def test_ended_cooldown_is_not_active(db_engine):
    with db_engine.begin() as conn:
        added = add_cooldown(
            conn, kind=SignalKind.ACTION_BLOCK, raw_message=None, source_scan_id=None, command="target add",
            duration=timedelta(hours=24),
        )
    with db_engine.begin() as conn:
        conn.execute(
            update(cooldowns).where(cooldowns.c.id == added.id).values(ends_at=text("now() - interval '1 second'"))
        )
    with db_engine.connect() as conn:
        assert active_cooldowns(conn) == []


def test_active_cooldowns_list_the_one_ending_last_first(db_engine):
    with db_engine.begin() as conn:
        short = add_cooldown(
            conn, kind=SignalKind.RATE_LIMIT, raw_message=None, source_scan_id=None, command="scan",
            duration=timedelta(hours=1),
        )
        long = add_cooldown(
            conn, kind=SignalKind.CHALLENGE, raw_message=None, source_scan_id=None, command="scan",
            duration=timedelta(hours=48), requires_session_check=True,
        )
    with db_engine.connect() as conn:
        assert active_cooldowns(conn) == [long, short]
    assert long.requires_session_check is True


def test_cooldown_kind_must_be_a_known_signal(db_engine):
    with pytest.raises(IntegrityError):
        with db_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO cooldowns (kind, command, ends_at) "
                    "VALUES ('bogus', 'scan', now() + interval '1 hour')"
                )
            )


# --- session checks ----------------------------------------------------------------------------------------


def test_no_successful_check_yet(db_engine):
    with db_engine.begin() as conn:
        add_session_check(conn, backend=Backend.INSTALOADER, succeeded=False, message="login_required")
    with db_engine.connect() as conn:
        assert latest_successful_check(conn) is None


def test_latest_successful_check_skips_failures_and_takes_the_newest(db_engine):
    with db_engine.begin() as conn:
        add_session_check(conn, backend=Backend.INSTALOADER, succeeded=True, account_username="older")
    with db_engine.begin() as conn:
        newer = add_session_check(conn, backend=Backend.INSTALOADER, succeeded=True, account_username="newer")
    with db_engine.begin() as conn:
        add_session_check(conn, backend=Backend.INSTALOADER, succeeded=False, message="rejected")
    with db_engine.connect() as conn:
        assert latest_successful_check(conn) == newer
    assert (newer.succeeded, newer.account_username, newer.backend) == (True, "newer", "instaloader")


def test_failed_check_keeps_its_message(db_engine):
    with db_engine.begin() as conn:
        failed = add_session_check(conn, backend=Backend.INSTALOADER, succeeded=False, message="login_required")
    assert (failed.succeeded, failed.account_username, failed.message) == (False, None, "login_required")
