"""Debug WS commands — memory snapshots for support / leak hunts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..helpers import memory
from ..helpers.api import CommandError, api_command
from ..models import ErrorCode

if TYPE_CHECKING:
    from ..device_builder import DeviceBuilder

_LOGGER = logging.getLogger(__name__)

_MAX_TOP_N = 200
_MAX_BASELINE_NAME_LEN = 100


def _validate_baseline_name(value: Any, *, field: str) -> str:
    """Return *value* as a non-empty bounded-length ``str`` or raise INVALID_ARGS."""
    if not isinstance(value, str) or not value or len(value) > _MAX_BASELINE_NAME_LEN:
        raise CommandError(
            ErrorCode.INVALID_ARGS,
            f"{field} must be a non-empty string of at most {_MAX_BASELINE_NAME_LEN} characters",
        )
    return value


class DebugController:
    """Owns the ``debug/*`` WS commands. Stateless beyond ``helpers.memory``."""

    def __init__(self, device_builder: DeviceBuilder) -> None:
        self._db = device_builder

    @api_command("debug/memory_snapshot")
    async def memory_snapshot(
        self,
        *,
        top_n: int = 25,
        save_as: str | None = None,
        compare_with: str | None = None,
        drop_baseline: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """
        Return process memory stats + the top ``tracemalloc`` allocators.

        First call enables ``tracemalloc`` lazily and returns an empty
        ``top_allocators`` (allocations before this call aren't traced).
        Set ``ESPHOME_DEBUG_MEMORY=1`` at process start to catch
        startup allocations too.

        ``save_as``: bookmark the snapshot for later ``compare_with``.
        ``compare_with``: diff against a previously-saved baseline.
        ``drop_baseline``: forget the named baseline; succeeds silently
        if it wasn't saved.
        """
        if not isinstance(top_n, int) or top_n < 1 or top_n > _MAX_TOP_N:
            raise CommandError(
                ErrorCode.INVALID_ARGS,
                f"top_n must be an int between 1 and {_MAX_TOP_N}",
            )

        if drop_baseline is not None:
            memory.drop_baseline(_validate_baseline_name(drop_baseline, field="drop_baseline"))

        if not memory.is_tracking():
            memory.start_tracking()
            _LOGGER.info("Memory tracking enabled via debug/memory_snapshot")
            return {
                "system": memory.system_stats(),
                "top_allocators": [],
                "baseline_names": memory.baseline_names(),
                "note": (
                    "tracemalloc was just enabled — allocations before "
                    "this call aren't traced. Run a build, then call "
                    "again with save_as to bookmark a baseline, and "
                    "again later with compare_with to see what grew."
                ),
            }

        snapshot = memory.take_snapshot()

        baseline = None
        if compare_with is not None:
            compare_with = _validate_baseline_name(compare_with, field="compare_with")
            baseline = memory.get_baseline(compare_with)
            if baseline is None:
                raise CommandError(
                    ErrorCode.NOT_FOUND,
                    f"baseline {compare_with!r} not saved; known: {memory.baseline_names()}",
                )

        if save_as is not None:
            memory.save_baseline(_validate_baseline_name(save_as, field="save_as"), snapshot)

        return {
            "system": memory.system_stats(),
            "top_allocators": memory.format_top_allocators(
                snapshot, baseline=baseline, top_n=top_n
            ),
            "baseline_names": memory.baseline_names(),
        }
