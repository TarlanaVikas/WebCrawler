"""In-process publish/subscribe for crawl log events (feeds the SSE endpoint).

Each subscriber gets its own bounded queue, so a slow or absent client can neither
block the crawler nor consume another client's messages. A ring buffer lets a client
that connects late (or reconnects with ``Last-Event-ID``) catch up on recent events.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import AsyncIterator, Deque, Dict, Optional


class LogBroker:
    def __init__(self, history: int = 1000, subscriber_queue: int = 500):
        self._history: Deque[Dict] = deque(maxlen=history)
        self._subscribers: set = set()
        self._queue_size = subscriber_queue
        self._next_id = 1

    def publish(self, session_id: Optional[str], level: str, message: str) -> Dict:
        event = {
            "id": self._next_id,
            "ts": time.time(),
            "session_id": session_id,
            "level": level,
            "message": message,
        }
        self._next_id += 1
        self._history.append(event)
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()  # drop the oldest event for this slow subscriber
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        return event

    def backlog(self, session_id: Optional[str] = None, after_id: int = 0, limit: int = 300) -> list:
        items = [
            e for e in self._history
            if e["id"] > after_id and (session_id is None or e["session_id"] == session_id)
        ]
        return items[-limit:]

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def subscribe(self, session_id: Optional[str] = None, after_id: int = 0) -> AsyncIterator[Optional[Dict]]:
        """Yield matching events; yields ``None`` every 15 s as a keep-alive tick."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        try:
            last_id = after_id
            for event in self.backlog(session_id, after_id):
                last_id = event["id"]
                yield event
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield None
                    continue
                if event["id"] <= last_id:
                    continue  # already delivered from the backlog
                last_id = event["id"]
                if session_id is None or event["session_id"] == session_id:
                    yield event
        finally:
            self._subscribers.discard(queue)
