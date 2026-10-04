import random

import pytest

from igft.safety.pacer import Pacer


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def work(self, seconds: float) -> None:
        self.now += seconds


def make(min_s=15.0, max_s=45.0, seed=1):
    clock = FakeClock()
    return Pacer(min_s, max_s, rng=random.Random(seed), clock=clock, sleep=clock.sleep), clock


def test_the_first_request_does_not_wait():
    pacer, clock = make()
    pacer.wait()
    assert clock.sleeps == []


def test_every_gap_between_request_starts_is_within_the_configured_range():
    pacer, clock = make(seed=7)
    starts = []
    for _ in range(200):
        pacer.wait()
        starts.append(clock.now)
        clock.work(0.3)  # the request and its commit
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(15.0 <= gap <= 45.0 for gap in gaps)
    assert min(gaps) < 20 and max(gaps) > 40  # the delay is random, not a constant


def test_a_slow_commit_is_not_added_on_top_of_the_delay():
    pacer, clock = make(min_s=20.0, max_s=20.0)
    pacer.wait()
    start = clock.now
    clock.work(12.0)  # a slow commit
    pacer.wait()
    assert clock.sleeps == [pytest.approx(8.0)]
    assert clock.now - start == pytest.approx(20.0)


def test_no_wait_when_the_work_already_took_longer_than_the_delay():
    pacer, clock = make(min_s=10.0, max_s=10.0)
    pacer.wait()
    clock.work(25.0)
    pacer.wait()
    assert clock.sleeps == []


@pytest.mark.parametrize(("low", "high"), [(30, 10), (-1, 5), (5, -1)])
def test_an_invalid_range_is_rejected(low, high):
    with pytest.raises(ValueError):
        Pacer(low, high)


def test_plan_next_returns_the_wait_the_next_call_will_sleep():
    pacer, clock = make(min_s=20.0, max_s=20.0)
    assert pacer.plan_next() == 0.0  # no request has started yet
    pacer.wait()
    clock.work(5.0)
    assert pacer.plan_next() == pytest.approx(15.0)
    pacer.wait()
    assert clock.sleeps == [pytest.approx(15.0)]


def test_planning_the_gap_does_not_change_the_bounds_or_the_draw():
    planned, planned_clock = make(seed=3)
    plain, plain_clock = make(seed=3)
    for _ in range(50):
        planned.plan_next()
        planned.plan_next()  # asking twice must not draw a second gap
        planned.wait()
        plain.wait()
        planned_clock.work(0.3)
        plain_clock.work(0.3)
    assert planned_clock.sleeps == plain_clock.sleeps
