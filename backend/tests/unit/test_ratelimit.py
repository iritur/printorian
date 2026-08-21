"""Ceilings on cost, and a wall in front of password guessing."""

from __future__ import annotations

from datetime import timedelta

import pytest

from printorian.core.clock import FixedClock
from printorian.core.errors import RateLimitedError
from printorian.core.ratelimit import Lockout, RateLimiter

MINUTE = timedelta(minutes=1)


def test_calls_under_the_ceiling_pass(clock: FixedClock) -> None:
    limiter = RateLimiter(clock)
    for _ in range(3):
        limiter.check("quote:1.2.3.4", limit=3, window=MINUTE)


def test_the_call_past_the_ceiling_is_refused(clock: FixedClock) -> None:
    limiter = RateLimiter(clock)
    for _ in range(3):
        limiter.check("quote:1.2.3.4", limit=3, window=MINUTE)

    with pytest.raises(RateLimitedError) as raised:
        limiter.check("quote:1.2.3.4", limit=3, window=MINUTE)

    assert raised.value.code == "error.rate_limited"
    # What the handler turns into `Retry-After`. A client told to back off and
    # not told for how long retries immediately.
    assert raised.value.details["retry_after_seconds"] >= 1


def test_one_caller_cannot_spend_another_caller_s_allowance(clock: FixedClock) -> None:
    limiter = RateLimiter(clock)
    limiter.check("quote:1.2.3.4", limit=1, window=MINUTE)

    limiter.check("quote:5.6.7.8", limit=1, window=MINUTE)


def test_buckets_do_not_share_an_allowance(clock: FixedClock) -> None:
    """Spending the quote budget must not close the sign-in door, or vice versa."""
    limiter = RateLimiter(clock)
    limiter.check("quote:1.2.3.4", limit=1, window=MINUTE)

    limiter.check("auth:1.2.3.4", limit=1, window=MINUTE)


def test_the_window_rolls_over(clock: FixedClock) -> None:
    limiter = RateLimiter(clock)
    limiter.check("quote:1.2.3.4", limit=1, window=MINUTE)

    clock.advance(timedelta(minutes=1, seconds=1))

    limiter.check("quote:1.2.3.4", limit=1, window=MINUTE)


# ------------------------------------------------------------------ lockout


def test_failures_under_the_limit_do_not_lock(clock: FixedClock) -> None:
    lockout = Lockout(clock)
    for _ in range(2):
        lockout.record_failure("a@b.c|1.2.3.4", limit=3, penalty=timedelta(minutes=15))

    lockout.guard("a@b.c|1.2.3.4")


def test_reaching_the_limit_locks_the_pair(clock: FixedClock) -> None:
    lockout = Lockout(clock)
    for _ in range(3):
        lockout.record_failure("a@b.c|1.2.3.4", limit=3, penalty=timedelta(minutes=15))

    with pytest.raises(RateLimitedError) as raised:
        lockout.guard("a@b.c|1.2.3.4")

    assert raised.value.code == "error.identity.locked_out"


def test_a_lockout_is_scoped_to_the_pair_not_the_account(clock: FixedClock) -> None:
    """Otherwise guessing at somebody's email locks them out of their own shop.

    That turns a defence into a denial of service, which is why the key is the
    account *and* the address rather than either alone.
    """
    lockout = Lockout(clock)
    for _ in range(3):
        lockout.record_failure("a@b.c|1.2.3.4", limit=3, penalty=timedelta(minutes=15))

    lockout.guard("a@b.c|9.9.9.9")


def test_the_lock_expires(clock: FixedClock) -> None:
    lockout = Lockout(clock)
    for _ in range(3):
        lockout.record_failure("a@b.c|1.2.3.4", limit=3, penalty=timedelta(minutes=15))

    clock.advance(timedelta(minutes=16))

    lockout.guard("a@b.c|1.2.3.4")


def test_a_success_forgets_the_failures(clock: FixedClock) -> None:
    lockout = Lockout(clock)
    for _ in range(2):
        lockout.record_failure("a@b.c|1.2.3.4", limit=3, penalty=timedelta(minutes=15))

    lockout.clear("a@b.c|1.2.3.4")

    # The third failure now starts a fresh count rather than tripping the lock.
    lockout.record_failure("a@b.c|1.2.3.4", limit=3, penalty=timedelta(minutes=15))
    lockout.guard("a@b.c|1.2.3.4")
