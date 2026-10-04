import pytest

from fakes import FakeFetcher, followers
from igft.domain import BlockSignal, Fetcher, ProfileInfo, ProfileNotFound, SignalKind


def test_fake_fetcher_satisfies_protocol():
    assert isinstance(FakeFetcher(), Fetcher)


def test_pages_are_served_by_cursor_until_the_end():
    fake = FakeFetcher([followers(1, 2), followers(3), followers(4)])
    first = fake.fetch_followers_page(10, None, username="target")
    assert [f.pk for f in first.followers] == [1, 2] and first.next_cursor == "c1"
    second = fake.fetch_followers_page(10, first.next_cursor, username="target")
    assert [f.pk for f in second.followers] == [3] and second.next_cursor == "c2"
    last = fake.fetch_followers_page(10, second.next_cursor, username="target")
    assert [f.pk for f in last.followers] == [4] and last.next_cursor is None


def test_calls_are_counted_per_method():
    fake = FakeFetcher([followers(1)], profiles=[ProfileInfo(5, "target")])
    fake.get_profile("target")
    fake.fetch_followers_page(5, None, username="target")
    fake.fetch_followers_page(5, None, username="target")
    fake.check_session()
    assert fake.calls == {"get_profile": 1, "fetch_followers_page": 2, "check_session": 1}
    assert fake.total_calls == 4
    assert fake.log[1] == ("fetch_followers_page", (5, None))


def test_scripted_exception_on_nth_call_counts_the_call():
    fake = FakeFetcher([followers(1), followers(2)])
    fake.raise_on_call(2, BlockSignal(SignalKind.RATE_LIMIT, "429"))
    fake.fetch_followers_page(1, None, username="target")
    with pytest.raises(BlockSignal):
        fake.fetch_followers_page(1, "c1", username="target")
    assert fake.calls["fetch_followers_page"] == 2


def test_scripted_exception_for_one_method():
    fake = FakeFetcher(account="me").raise_on("check_session", 1, BlockSignal(SignalKind.SESSION_REJECTED, "login_required"))
    with pytest.raises(BlockSignal):
        fake.check_session()
    assert fake.check_session() == "me"


def test_unknown_profile_raises_profile_not_found():
    fake = FakeFetcher([followers(1), followers(2)])
    with pytest.raises(ProfileNotFound):
        fake.get_profile("nobody")
