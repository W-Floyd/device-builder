"""WebSocket API message models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mashumaro.mixins.orjson import DataClassORJSONMixin


class ErrorCode(StrEnum):
    """WebSocket API error codes."""

    INVALID_MESSAGE = "invalid_message"
    UNKNOWN_COMMAND = "unknown_command"
    INVALID_ARGS = "invalid_args"
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    INTERNAL_ERROR = "internal_error"
    NOT_AUTHENTICATED = "not_authenticated"
    RATE_LIMITED = "rate_limited"
    # Phase 4a additions, used by the offloader-side pair flow.
    # ``UNAVAILABLE`` means we couldn't reach a *remote* dashboard
    # (TLS handshake failed, host refused, timed out); distinct
    # from INTERNAL_ERROR, which means *this* dashboard hit
    # something unexpected. ``UNAUTHORIZED`` means a *remote*
    # dashboard rejected our bearer — distinct from
    # NOT_AUTHENTICATED, which is about the client's WS auth to
    # this dashboard. ``PRECONDITION_FAILED`` covers the cert-pin
    # mismatch at confirm-pair (the pin was valid at preview but
    # changed before we re-handshook).
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    PRECONDITION_FAILED = "precondition_failed"


@dataclass
class CommandMessage(DataClassORJSONMixin):
    """Client -> Server: a command request."""

    command: str
    message_id: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResultMessage(DataClassORJSONMixin):
    """Server -> Client: successful command result."""

    message_id: str
    result: Any = None


@dataclass
class ErrorMessage(DataClassORJSONMixin):
    """Server -> Client: command error."""

    message_id: str
    error_code: ErrorCode
    details: str = ""


@dataclass
class EventMessage(DataClassORJSONMixin):
    """Server -> Client: streaming output or push event."""

    message_id: str
    event: str
    data: Any = None


@dataclass
class ServerInfoMessage(DataClassORJSONMixin):
    """Server -> Client: sent on connection."""

    server_version: str
    esphome_version: str
    port: int
    ha_addon: bool = False
    requires_auth: bool = False
