from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

T = TypeVar("T")


class AsyncSingleFlight:
    """Coalesce concurrent equivalent reads into one upstream request.

    The first caller waits for a short collection window. Equivalent callers
    then await the same shielded task and receive independent copies of its
    result. Failures are shared only by the current flight and are never cached.
    """

    def __init__(self, window_ms: int = 25) -> None:
        if not 0 <= window_ms <= 500:
            raise ValueError("single-flight window_ms must be between 0 and 500")
        self.window_seconds = window_ms / 1000
        self._lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._flights_started = 0
        self._requests_joined = 0
        self._logical_requests = 0

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            self._logical_requests += 1
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._execute(key, factory))
                self._inflight[key] = task
                self._flights_started += 1
            else:
                self._requests_joined += 1
        return cast(T, copy.deepcopy(await asyncio.shield(task)))

    def metrics(self) -> dict[str, int]:
        return {
            "logical_requests": self._logical_requests,
            "upstream_requests": self._flights_started,
            "flights_started": self._flights_started,
            "requests_joined": self._requests_joined,
            "active_flights": len(self._inflight),
        }

    async def _execute(self, key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
        try:
            if self.window_seconds:
                await asyncio.sleep(self.window_seconds)
            return await factory()
        finally:
            async with self._lock:
                current = self._inflight.get(key)
                if current is asyncio.current_task():
                    self._inflight.pop(key, None)
