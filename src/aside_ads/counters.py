"""Short-lived counters for frequency caps and rate limits.

In-process by default (fine for a single server process). Set ``REDIS_URL`` to
share counters across workers and replicas.
"""

from __future__ import annotations

import time
from typing import Protocol


class Counters(Protocol):
    async def incr(self, key: str, ttl_seconds: int) -> int: ...

    async def get(self, key: str) -> int: ...

    async def close(self) -> None: ...


class MemoryCounters:
    """Fixed-window counters held in a dict. Not shared between processes."""

    def __init__(self, max_keys: int = 200_000) -> None:
        self._data: dict[str, tuple[int, float]] = {}
        self._max_keys = max_keys

    def _sweep(self, now: float) -> None:
        expired = [k for k, (_, exp) in self._data.items() if exp <= now]
        for k in expired:
            del self._data[k]

    async def incr(self, key: str, ttl_seconds: int) -> int:
        # No await between read and write: atomic within the event loop.
        now = time.monotonic()
        value, exp = self._data.get(key, (0, 0.0))
        if exp <= now:
            value, exp = 0, now + ttl_seconds
        value += 1
        self._data[key] = (value, exp)
        if len(self._data) > self._max_keys:
            self._sweep(now)
        return value

    async def get(self, key: str) -> int:
        value, exp = self._data.get(key, (0, 0.0))
        return value if exp > time.monotonic() else 0

    async def close(self) -> None:
        self._data.clear()


class RedisCounters:
    def __init__(self, url: str, prefix: str = "aside:") -> None:
        import redis.asyncio as redis

        self._r = redis.from_url(url)
        self._prefix = prefix

    async def incr(self, key: str, ttl_seconds: int) -> int:
        k = self._prefix + key
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.incr(k)
            pipe.expire(k, ttl_seconds, nx=True)
            value, _ = await pipe.execute()
        return int(value)

    async def get(self, key: str) -> int:
        v = await self._r.get(self._prefix + key)
        return int(v) if v else 0

    async def close(self) -> None:
        await self._r.aclose()


def build_counters(redis_url: str | None) -> Counters:
    return RedisCounters(redis_url) if redis_url else MemoryCounters()
