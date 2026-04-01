"""Simple synchronous event bus."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from ..models import EventType

_LOGGER = logging.getLogger(__name__)


@dataclass
class Event:
    """A device builder event."""

    event_type: EventType
    data: dict[str, Any]


class EventBus:
    """Simple synchronous event bus for dashboard state changes."""

    def __init__(self) -> None:
        self._listeners: dict[EventType, set[Callable[[Event], None]]] = {}

    def add_listener(
        self, event_type: EventType, listener: Callable[[Event], None]
    ) -> Callable[[], None]:
        """Add a listener. Returns an unsubscribe callback."""
        self._listeners.setdefault(event_type, set()).add(listener)
        return partial(self._remove_listener, event_type, listener)

    def _remove_listener(self, event_type: EventType, listener: Callable[[Event], None]) -> None:
        self._listeners.get(event_type, set()).discard(listener)

    def fire(self, event_type: EventType, data: dict[str, Any] | None = None) -> None:
        """Fire an event to all listeners."""
        event = Event(event_type, data or {})
        for listener in list(self._listeners.get(event_type, set())):
            try:
                listener(event)
            except Exception:
                _LOGGER.exception("Event listener raised an exception")
