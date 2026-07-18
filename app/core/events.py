"""A tiny in-process pub/sub hub.

The download engine publishes progress/status events; the WebSocket endpoint and
(optionally) the Telegram bot subscribe. Keeping this decoupled means the engine
never needs to know who is listening.
"""

import asyncio


class EventBus:
    def __init__(self, maxsize=200):
        self._subscribers: set[asyncio.Queue] = set()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    def publish(self, event: dict):
        """Fan out to every subscriber. A slow/full consumer drops the event
        rather than blocking the producer — progress is advisory, not a log."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
