"""Rate limiting utilities for API calls"""

import logging
import threading
import time
from collections import deque
from typing import Callable, Any

logger = logging.getLogger("ott-hooks")


class RateLimiter:
    """Simple rate limiter using sliding window"""
    
    def __init__(self, max_calls: int, period_seconds: int):
        """Initialize rate limiter
        
        Args:
            max_calls: Maximum number of calls allowed
            period_seconds: Time period in seconds
        """
        self.max_calls = max_calls
        self.period = period_seconds
        self.calls = deque()
        self.lock = threading.Lock()
    
    def acquire(self, timeout: float = 30.0) -> bool:
        """Acquire permission to make a call
        
        Blocks until rate limit allows the call or timeout is reached.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if acquired, False if timeout
        """
        start = time.time()
        
        while True:
            with self.lock:
                now = time.time()
                
                # Remove old calls outside the window
                while self.calls and self.calls[0] < now - self.period:
                    self.calls.popleft()
                
                # Check if we can make a call
                if len(self.calls) < self.max_calls:
                    self.calls.append(now)
                    return True
            
            # Check timeout
            if time.time() - start >= timeout:
                logger.warning(f"[RateLimiter] Timeout after {timeout}s")
                return False
            
            # Wait a bit before retrying
            time.sleep(0.1)
    
    def execute(self, func: Callable, *args, timeout: float = 30.0, **kwargs) -> Any:
        """Execute function with rate limiting
        
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
        else:
            logger.error(f"[RateLimiter] Failed to acquire permission for {func.__name__}")
            return None
