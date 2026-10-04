from datetime import timedelta

import pytest
from sqlalchemy import text

from dbutil import migrated_schema
from fakes import FakeFetcher
from igft.domain import BlockSignal, CommandRefused, ProfileInfo, ProfileNotFound, SignalKind
from igft.safety.gate import GuardedFetcher, SafetyGate
from igft.targets import AddOutcome, TargetService
from test_gate import hours, put_cooldown

pytestmark = pytest.mark.db

ALICE = ProfileInfo(pk=1001, username="alice")


def target_rows(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT id, username FROM targets ORDER BY id")).all()


def guarded(engine, fetcher):
    return GuardedFetcher(fetcher, SafetyGate(engine, timedelta(hours=24)), "target add")


def test_add_stores_the_numeric_id_and_username_with_one_profile_request():
    with migrated_schema() as schema:
        fetcher = FakeFetcher(profiles=[ALICE])

        result = TargetService(schema.engine, fetcher).add("alice")

        assert result.outcome is AddOutcome.ADDED
        assert (result.target.id, result.target.username) == (1001, "alice")
        assert target_rows(schema.engine) == [(1001, "alice")]
        assert fetcher.total_calls == 1
        assert fetcher.calls["get_profile"] == 1


def test_add_accepts_a_leading_at_sign():
    with migrated_schema() as schema:
        fetcher = FakeFetcher(profiles=[ALICE])

        TargetService(schema.engine, fetcher).add("@alice")

        assert fetcher.log == [("get_profile", ("alice",))]


def test_private_profile_is_stored_like_any_other_with_no_extra_request():
    # ProfileInfo carries only pk and username: nothing about privacy or follow state is read.
    with migrated_schema() as schema:
        fetcher = FakeFetcher(profiles=[ProfileInfo(pk=2002, username="private_pat")])

        result = TargetService(schema.engine, fetcher).add("private_pat")

        assert result.outcome is AddOutcome.ADDED
        assert fetcher.total_calls == 1


def test_unknown_profile_stores_nothing():
    with migrated_schema() as schema:
        with pytest.raises(ProfileNotFound):
            TargetService(schema.engine, FakeFetcher()).add("ghost")

        assert target_rows(schema.engine) == []


def test_adding_an_existing_target_again_reports_it_and_creates_no_second_target():
    with migrated_schema() as schema:
        service = TargetService(schema.engine, FakeFetcher(profiles=[ALICE]))
        service.add("alice")

        result = service.add("alice")

        assert result.outcome is AddOutcome.EXISTS
        assert target_rows(schema.engine) == [(1001, "alice")]


def test_a_renamed_profile_updates_the_stored_username():
    with migrated_schema() as schema:
        TargetService(schema.engine, FakeFetcher(profiles=[ALICE])).add("alice")
        renamed = FakeFetcher(profiles=[ProfileInfo(pk=1001, username="alice_new")])

        result = TargetService(schema.engine, renamed).add("alice_new")

        assert result.outcome is AddOutcome.RENAMED
        assert result.old_username == "alice"
        assert result.target.username == "alice_new"
        assert target_rows(schema.engine) == [(1001, "alice_new")]


def test_rate_limit_on_target_add_stores_no_target_and_sets_a_cooldown():
    with migrated_schema() as schema:
        fetcher = FakeFetcher(profiles=[ALICE]).raise_on("get_profile", 1, BlockSignal(SignalKind.RATE_LIMIT, "wait"))

        with pytest.raises(BlockSignal):
            TargetService(schema.engine, guarded(schema.engine, fetcher)).add("alice")

        assert target_rows(schema.engine) == []
        with schema.engine.connect() as conn:
            cooldowns = conn.execute(text("SELECT kind, command FROM cooldowns")).all()
        assert cooldowns == [("rate_limit", "target add")]


def test_target_add_is_refused_during_a_cooldown_without_a_request():
    with migrated_schema() as schema:
        put_cooldown(schema.engine, started_ago=timedelta(hours=1), ends_in=hours(23))
        fetcher = FakeFetcher(profiles=[ALICE])

        with pytest.raises(CommandRefused):
            TargetService(schema.engine, guarded(schema.engine, fetcher)).add("alice")

        assert fetcher.total_calls == 0
        assert target_rows(schema.engine) == []
