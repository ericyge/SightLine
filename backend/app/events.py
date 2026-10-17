"""In-memory broadcaster feeding the dashboard WebSocket (/ws/events)."""
import asyncio
from typing import Any


class Broadcaster:
    """Fan-out of JSON events to every connected dashboard. Single process only."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        """Register a new listener and return its queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        """Push an event to all listeners; slow listeners drop events instead of blocking."""
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @property
    def listener_count(self) -> int:
        return len(self._subs)


broadcaster = Broadcaster()
