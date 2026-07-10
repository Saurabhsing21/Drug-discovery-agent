from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunEvent:
    event: str
    data: dict[str, Any]
    created_at_ms: int


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[RunEvent]]] = {}
        self._history: dict[str, deque[RunEvent]] = {}
        self._last_seen_ms: dict[str, int] = {}

    def ensure_run(self, run_id: str) -> None:
        self._subscribers.setdefault(run_id, set())
        self._history.setdefault(run_id, deque(maxlen=500))
        self._last_seen_ms[run_id] = int(time.time() * 1000)

    def subscribe(self, run_id: str) -> asyncio.Queue[RunEvent]:
        self.ensure_run(run_id)
        queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=2000)
        self._subscribers[run_id].add(queue)
        for item in list(self._history[run_id]):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                break
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[RunEvent]) -> None:
        subs = self._subscribers.get(run_id)
        if not subs:
            return
        subs.discard(queue)

    def publish(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        self.ensure_run(run_id)
        envelope = RunEvent(event=event, data=data, created_at_ms=int(time.time() * 1000))
        self._history[run_id].append(envelope)
        for queue in list(self._subscribers.get(run_id, set())):
            while True:
                try:
                    queue.put_nowait(envelope)
                    break
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

    def has_run(self, run_id: str) -> bool:
        return run_id in self._history


BUS = EventBus()
