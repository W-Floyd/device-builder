"""API command registration helpers."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

# Type alias for command handler functions
CommandHandler = Callable[..., Coroutine[Any, Any, Any]]


def api_command(command: str) -> Callable[[CommandHandler], CommandHandler]:
    """Decorate a controller method to register it as a WebSocket API command.

    Usage:
        @api_command("boards/get_boards")
        async def get_boards(self, *, query=None, limit=50, ...) -> PagedBoardsResponse:
            ...

    The decorated method is discoverable via `_api_command` attribute.
    DeviceBuilder scans controllers for these and builds its command registry.
    """

    def decorator(func: CommandHandler) -> CommandHandler:
        func._api_command = command  # type: ignore[attr-defined]
        return func

    return decorator


def collect_api_commands(obj: object) -> dict[str, CommandHandler]:
    """Scan an object for methods decorated with @api_command.

    Returns {command_name: bound_method} dict.
    """
    handlers: dict[str, CommandHandler] = {}
    for name in dir(obj):
        if name.startswith("_"):
            continue
        method = getattr(obj, name, None)
        if callable(method) and hasattr(method, "_api_command"):
            handlers[method._api_command] = method
    return handlers
