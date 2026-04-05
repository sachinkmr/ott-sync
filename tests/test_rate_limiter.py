"""Tests for the sliding-window rate limiter"""

import threading
import time

import pytest

from ott.utils.rate_limiter import RateLimiter, RateLimiterTimeout


class TestRateLimiterAcquire:
    """acquire() blocks correctly within the sliding window."""

    def test_acquires_up_to_limit(self):
        rl = RateLimiter(max_calls=3, period_seconds=10)
        assert rl.acquire(timeout=0.1)
        assert rl.acquire(timeout=0.1)
        assert rl.acquire(timeout=0.1)

    def test_fourth_acquire_times_out(self):
        rl = RateLimiter(max_calls=2, period_seconds=10)
        assert rl.acquire(timeout=0.1)
        assert rl.acquire(timeout=0.1)
        t0 = time.monotonic()
        assert rl.acquire(timeout=0.15) is False
        elapsed = time.monotonic() - t0
        # ~150ms timeout, with small scheduling margin
        assert 0.1 <= elapsed <= 0.35

    def test_slot_reclaimed_after_window(self):
        """A call from >period seconds ago no longer counts."""
        rl = RateLimiter(max_calls=1, period_seconds=1)
        assert rl.acquire(timeout=0.1)
        time.sleep(1.1)
        assert rl.acquire(timeout=0.1)

    def test_available_calls_tracks_usage(self):
        rl = RateLimiter(max_calls=3, period_seconds=10)
        assert rl.available_calls() == 3
        rl.acquire(timeout=0.1)
        assert rl.available_calls() == 2
        rl.acquire(timeout=0.1)
        assert rl.available_calls() == 1
        rl.acquire(timeout=0.1)
        assert rl.available_calls() == 0


class TestRateLimiterReset:
    """reset() wipes tracked calls and unblocks waiters."""

    def test_reset_restores_all_slots(self):
        rl = RateLimiter(max_calls=2, period_seconds=60)
        rl.acquire(timeout=0.1)
        rl.acquire(timeout=0.1)
        assert rl.available_calls() == 0
        rl.reset()
        assert rl.available_calls() == 2

    def test_reset_wakes_waiter(self):
        """A waiter blocked in acquire() returns True once reset() fires."""
        rl = RateLimiter(max_calls=1, period_seconds=30)
        rl.acquire(timeout=0.1)  # fill the one slot
        result: list[bool] = []

        def waiter():
            result.append(rl.acquire(timeout=3.0))

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)  # let the thread reach _cond.wait
        rl.reset()
        t.join(timeout=2.0)
        assert not t.is_alive(), "reset() did not wake the waiter"
        assert result == [True]


class TestExecute:
    """execute() and execute_raising() wrappers."""

    def test_execute_runs_func(self):
        rl = RateLimiter(max_calls=1, period_seconds=30)
        assert rl.execute(lambda x: x + 1, 41) == 42

    def test_execute_returns_none_on_timeout(self):
        rl = RateLimiter(max_calls=1, period_seconds=30)
        rl.acquire(timeout=0.1)  # fill the slot
        assert rl.execute(lambda: "should not run", timeout=0.1) is None

    def test_execute_raising_raises_on_timeout(self):
        rl = RateLimiter(max_calls=1, period_seconds=30)
        rl.acquire(timeout=0.1)
        with pytest.raises(RateLimiterTimeout):
            rl.execute_raising(lambda: "never", timeout=0.1)

    def test_execute_raising_distinguishes_none_result_from_timeout(self):
        """execute() can't tell 'func returned None' from 'timed out'.
        execute_raising() separates the two."""
        rl = RateLimiter(max_calls=5, period_seconds=10)
        # Plenty of slots - func legitimately returns None
        assert rl.execute_raising(lambda: None) is None
