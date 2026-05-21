"""
Tests for ``controllers/debug.py`` + ``helpers/memory.py``.

Pins the wire shape of ``debug/memory_snapshot`` and the
save / compare / drop baseline contract. ``tracemalloc`` is
process-global, so each test stops + clears state in a fixture
to keep the suite hermetic.
"""

from __future__ import annotations

import tracemalloc
from typing import Any
from unittest.mock import MagicMock

import pytest

from esphome_device_builder.controllers.debug import DebugController
from esphome_device_builder.helpers import memory
from esphome_device_builder.helpers.api import CommandError
from esphome_device_builder.models import ErrorCode


@pytest.fixture(autouse=True)
def _reset_tracemalloc() -> Any:
    """Stop tracemalloc and forget baselines around every test."""
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    memory._baselines.clear()
    yield
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    memory._baselines.clear()


def _controller() -> DebugController:
    """Build a controller against a stub DeviceBuilder — only ``_db`` is held."""
    return DebugController(MagicMock())


async def test_first_call_enables_tracing_returns_note() -> None:
    """Cold call enables tracemalloc and returns the helpful note."""
    assert not tracemalloc.is_tracing()
    result = await _controller().memory_snapshot()
    assert tracemalloc.is_tracing()
    assert result["top_allocators"] == []
    assert result["baseline_names"] == []
    assert "tracemalloc was just enabled" in result["note"]
    assert result["system"]["tracking"] is True


async def test_warm_call_returns_top_allocators() -> None:
    """Second call (with tracing already on) returns real allocator stats."""
    memory.start_tracking()
    # Force at least one Python-level allocation so the snapshot
    # has something to report.
    _payload = [object() for _ in range(100)]
    assert _payload

    result = await _controller().memory_snapshot(top_n=10)

    assert "note" not in result
    assert len(result["top_allocators"]) <= 10
    assert all("traceback" in entry for entry in result["top_allocators"])
    assert all("size_bytes" in entry for entry in result["top_allocators"])
    assert result["system"]["tracking"] is True


async def test_save_and_compare_baseline_roundtrip() -> None:
    """``save_as`` then ``compare_with`` returns a diff against the baseline."""
    memory.start_tracking()
    controller = _controller()

    saved = await controller.memory_snapshot(save_as="before")
    assert "before" in saved["baseline_names"]

    # Allocate something the diff can pick up.
    _growth = [bytearray(1024) for _ in range(50)]
    assert _growth

    diff = await controller.memory_snapshot(compare_with="before", top_n=20)
    assert "before" in diff["baseline_names"]
    # At least one entry should show a non-zero size_diff after the
    # bytearray allocation above.
    assert any(entry["size_diff_bytes"] != 0 for entry in diff["top_allocators"])


async def test_compare_with_unknown_baseline_raises_not_found() -> None:
    """An unsaved baseline name is a user-facing NOT_FOUND error."""
    memory.start_tracking()

    with pytest.raises(CommandError) as exc_info:
        await _controller().memory_snapshot(compare_with="nope")

    assert exc_info.value.code == ErrorCode.NOT_FOUND


async def test_drop_baseline_then_compare_raises_not_found() -> None:
    """``drop_baseline`` removes the named snapshot from the store."""
    memory.start_tracking()
    controller = _controller()

    await controller.memory_snapshot(save_as="tmp")
    assert "tmp" in memory.baseline_names()

    await controller.memory_snapshot(drop_baseline="tmp")
    assert "tmp" not in memory.baseline_names()

    with pytest.raises(CommandError) as exc_info:
        await controller.memory_snapshot(compare_with="tmp")
    assert exc_info.value.code == ErrorCode.NOT_FOUND


@pytest.mark.parametrize("bad_top_n", [0, -1, 1000, "10", None])
async def test_invalid_top_n_raises_invalid_args(bad_top_n: Any) -> None:
    """``top_n`` is range-validated; out-of-bounds / wrong-type rejects with INVALID_ARGS."""
    memory.start_tracking()

    with pytest.raises(CommandError) as exc_info:
        await _controller().memory_snapshot(top_n=bad_top_n)

    assert exc_info.value.code == ErrorCode.INVALID_ARGS


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("save_as", ["list-not-string"]),
        ("save_as", ""),
        ("save_as", "x" * 101),
        ("compare_with", {"dict": "no"}),
        ("compare_with", 42),
        ("drop_baseline", None.__class__),  # arbitrary unhashable-ish junk
    ],
)
async def test_non_string_baseline_name_raises_invalid_args(field: str, value: Any) -> None:
    """Baseline-name fields type-check before reaching the dict, not after."""
    memory.start_tracking()
    with pytest.raises(CommandError) as exc_info:
        await _controller().memory_snapshot(**{field: value})
    assert exc_info.value.code == ErrorCode.INVALID_ARGS


def test_system_stats_includes_tracemalloc_when_tracing() -> None:
    """``system_stats`` adds tracemalloc fields only when tracking is on."""
    cold = memory.system_stats()
    assert cold["tracking"] is False
    assert "tracemalloc_current_bytes" not in cold

    memory.start_tracking()
    warm = memory.system_stats()
    assert warm["tracking"] is True
    assert "tracemalloc_current_bytes" in warm
    assert "tracemalloc_peak_bytes" in warm
