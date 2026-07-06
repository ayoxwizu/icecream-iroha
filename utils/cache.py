"""
Small helpers used to keep autocomplete snappy without hammering MongoDB
on every keystroke.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, TypeVar

log = logging.getLogger("utils.cache")

T = TypeVar("T")


class TTLCache:
    """A minimal per-key cache with a fixed time-to-live."""

    def __init__(self, ttl_seconds: float = 15.0):
        self.ttl_seconds = ttl_seconds
        self._store: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        self._store[key] = (time.monotonic() + self.ttl_seconds, value)

    def invalidate(self, key: Any) -> None:
        self._store.pop(key, None)


async def run_with_timeout(coro: Awaitable[T], timeout: float, default: T) -> T:
    """Await `coro`, but fall back to `default` instead of raising if it's slow."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        log.warning("run_with_timeout: timed out after %.1fs, using default", timeout)
        return default
    except Exception:
        log.exception("run_with_timeout: coroutine raised, using default")
        return default
