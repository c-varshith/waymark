"""Process-local SSE broadcaster for cell-level live updates."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .realtime_config import HEARTBEAT_SECONDS, REPLAY_BUFFER, STREAM_MAX_SUBSCRIBERS

log = logging.getLogger("waymark.realtime")

EVENT_KEYS: dict[str, frozenset[str]] = {
    "incident.created": frozenset({"cell_id", "kind", "at"}),
    "incident.reviewed": frozenset({"cell_id", "decision", "at"}),
    "prediction.updated": frozenset({
        "cell_id", "risk_score", "live_risk_score", "active_incident_count",
        "incident_overlay_points", "scored_at",
    }),
    "context.changed": frozenset({"at"}),
    "resync": frozenset(),
    "heartbeat": frozenset({"at"}),
}


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    id: int
    name: str
    data: dict[str, Any]


@dataclass(slots=True)
class Subscriber:
    token: int
    queue: asyncio.Queue[RealtimeEvent]


class SubscriberLimitError(RuntimeError):
    """The process has reached its configured SSE subscriber limit."""


class Broadcaster:
    """A loop-owned event hub whose publish method is safe from worker threads."""

    def __init__(self, *, heartbeat_seconds: float = HEARTBEAT_SECONDS,
                 max_subscribers: int = STREAM_MAX_SUBSCRIBERS,
                 replay_buffer: int = REPLAY_BUFFER, queue_size: int = 100):
        self.heartbeat_seconds = heartbeat_seconds
        self.max_subscribers = max_subscribers
        self.replay_buffer_size = replay_buffer
        self.queue_size = queue_size
        self._loop: asyncio.AbstractEventLoop | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._events: deque[RealtimeEvent] = deque(maxlen=replay_buffer)
        self._subscribers: dict[int, Subscriber] = {}
        self._next_subscriber = 1
        self._next_event_id = 1
        self._stopping = False

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def version(self) -> int:
        return self._next_event_id - 1

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is loop and self._heartbeat_task and not self._heartbeat_task.done():
            return
        self._loop = loop
        self._stopping = False
        self._heartbeat_task = loop.create_task(self._heartbeat_loop(), name="waymark-sse-heartbeat")

    async def stop(self) -> None:
        self._stopping = True
        task, self._heartbeat_task = self._heartbeat_task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._subscribers.clear()
        self._loop = None

    def _validate(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        expected = EVENT_KEYS.get(name)
        if expected is None or set(data) != expected:
            raise ValueError(f"Invalid realtime event payload for {name!r}.")
        # Keep nested mutable data out of the queue and replay buffer.
        return dict(data)

    def publish(self, name: str, data: dict[str, Any]) -> bool:
        """Schedule an event on the serving loop; callers never wait for subscribers."""
        try:
            payload = self._validate(name, data)
        except Exception:
            log.warning("Rejected malformed realtime event %s", name, exc_info=True)
            return False
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        try:
            loop.call_soon_threadsafe(self._publish_on_loop, name, payload)
            return True
        except RuntimeError:
            return False

    def _new_event(self, name: str, data: dict[str, Any]) -> RealtimeEvent:
        event = RealtimeEvent(self._next_event_id, name, data)
        self._next_event_id += 1
        self._events.append(event)
        return event

    @staticmethod
    def _discard_queue(queue: asyncio.Queue[RealtimeEvent]) -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _publish_on_loop(self, name: str, data: dict[str, Any]) -> None:
        event = self._new_event(name, data)
        overflowed: list[Subscriber] = []
        for subscriber in tuple(self._subscribers.values()):
            try:
                subscriber.queue.put_nowait(event)
            except asyncio.QueueFull:
                overflowed.append(subscriber)
        if not overflowed:
            return

        # One sequenced resync marker represents every dropped queue tail for this publish.
        resync = self._new_event("resync", {})
        overflow_tokens = {subscriber.token for subscriber in overflowed}
        for subscriber in tuple(self._subscribers.values()):
            queue = subscriber.queue
            if subscriber.token in overflow_tokens:
                self._discard_queue(queue)
                queue.put_nowait(resync)
                continue
            try:
                queue.put_nowait(resync)
            except asyncio.QueueFull:
                self._discard_queue(queue)
                queue.put_nowait(resync)

    async def subscribe(self, last_event_id: int | None = None) -> Subscriber:
        if self._loop is not asyncio.get_running_loop():
            await self.start()
        if len(self._subscribers) >= self.max_subscribers:
            raise SubscriberLimitError("The live update stream is at capacity. Try again shortly.")

        subscriber = Subscriber(self._next_subscriber, asyncio.Queue(maxsize=self.queue_size))
        self._next_subscriber += 1
        self._subscribers[subscriber.token] = subscriber
        if last_event_id is None:
            return subscriber

        oldest_id = self._events[0].id if self._events else self.version + 1
        is_out_of_range = last_event_id > self.version or last_event_id < oldest_id - 1
        missed = [event for event in self._events if event.id > last_event_id]
        if is_out_of_range or len(missed) > self.queue_size:
            marker = self._new_event("resync", {})
            subscriber.queue.put_nowait(marker)
            return subscriber
        for event in missed:
            subscriber.queue.put_nowait(event)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.pop(subscriber.token, None)

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._stopping:
                await asyncio.sleep(self.heartbeat_seconds)
                if self._subscribers:
                    self._publish_on_loop("heartbeat", {"at": utc_iso()})
        except asyncio.CancelledError:
            raise
