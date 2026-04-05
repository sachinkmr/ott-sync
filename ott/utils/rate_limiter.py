"""Rate limiting utilities for API calls"""

import logging
import threading
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger("ott-hooks")


class RateLimiterTimeout(Exception):
    """Raised by execute_raising() when the rate limit cannot be acquired."""


class RateLimiter:
    """Sliding-window rate limiter.

    Thread-safe. Blocks callers in acquire() until a slot is free or a
    timeout expires. Uses a Condition so a slot freeing up wakes waiters
    immediately instead of forcing a 100ms poll.
    """

    def __init__(self, max_calls: int, period_seconds: int):
        """Initialize rate limiter

        Args:
            max_calls: Maximum number of calls allowed
            period_seconds: Time period in seconds
        """
        self.max_calls = max_calls
        self.period = period_seconds
        self.calls: deque[float] = deque()
        self._cond = threading.Condition()

    def _evict_expired(self, now: float) -> None:
        """Drop call timestamps outside the sliding window. Caller holds _cond."""
        cutoff = now - self.period
        while self.calls and self.calls[0] < cutoff:
            self.calls.popleft()

    def available_calls(self) -> int:
        """Return how many slots are currently free.

        Snapshot only; the value can change the moment this method returns.
        Useful for logging / metrics, not for gating decisions.
        """
        with self._cond:
            self._evict_expired(time.monotonic())
            return max(0, self.max_calls - len(self.calls))

    def reset(self) -> None:
        """Forget all tracked calls. Intended for tests."""
        with self._cond:
            self.calls.clear()
            self._cond.notify_all()

    def acquire(self, timeout: float = 30.0) -> bool:
        """Acquire permission to make a call.

        Blocks until a slot is free or timeout expires. When another waiter
        uses up a slot we reach here again via the condition's wait-loop.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if acquired, False if timeout
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                now = time.monotonic()
                self._evict_expired(now)
                if len(self.calls) < self.max_calls:
                    self.calls.append(now)
                    return True
                # Wait until the oldest call ages out OR reset() wakes us.
                wait_for = self.calls[0] + self.period - now
                wait_for = min(wait_for, deadline - now)
                if wait_for <= 0:
                    logger.warning(f"[RateLimiter] Timeout after {timeout}s")
                    return False
                self._cond.wait(timeout=wait_for)

    def execute(self, func: Callable, *args, timeout: float = 30.0, **kwargs) -> Any:
        """Execute func with rate limiting. Returns None on timeout.

        Args:
            func: Function to execute
            *args: Positional arguments for func
            timeout: Maximum time to wait for rate limit
            **kwargs: Keyword arguments for func

        Returns:
            Function result or None if timeout
        """
        if self.acquire(timeout=timeout):
            return func(*args, **kwargs)
        logger.error(f"[RateLimiter] Failed to acquire permission for {func.__name__}")
        return None

    def execute_raising(self, func: Callable, *args, timeout: float = 30.0, **kwargs) -> Any:
        """Execute func with rate limiting. Raises RateLimiterTimeout on timeout.

        Prefer this over execute() when None is a valid return value for func
        and callers need to distinguish rate-limit timeouts from real results.
        """
        if self.acquire(timeout=timeout):
            return func(*args, **kwargs)
        raise RateLimiterTimeout(
            f"Failed to acquire rate limiter permission for {func.__name__} "
            f"after {timeout}s (max_calls={self.max_calls}/period={self.period}s)"
        )
