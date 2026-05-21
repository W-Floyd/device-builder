"""
Memory-debugging helpers backing the ``debug/memory_snapshot`` WS command.

Wraps stdlib ``tracemalloc`` plus a small in-memory baseline store
so support requests asking for a heap diff don't need users to
attach a profiler — just enable tracking (set
``ESPHOME_DEBUG_MEMORY=1`` or call the WS command once),
``save_as="before"`` a build, ``compare_with="before"`` after, and
paste the diff. The baseline store is process-local and lost on
restart; that's fine for ad-hoc debugging.
"""

from __future__ import annotations

import gc
import sys
import tracemalloc
from typing import Any

try:
    import resource
except ImportError:
    # Windows doesn't ship the ``resource`` module. The RSS field
    # is best-effort everywhere and just gets omitted there.
    resource = None  # type: ignore[assignment]

_DEFAULT_FRAMES = 25

_baselines: dict[str, tracemalloc.Snapshot] = {}


def start_tracking(frames: int = _DEFAULT_FRAMES) -> None:
    """Enable ``tracemalloc`` allocation tracking. Idempotent."""
    if not tracemalloc.is_tracing():
        tracemalloc.start(frames)


def is_tracking() -> bool:
    """Return whether ``tracemalloc`` is currently tracking allocations."""
    return tracemalloc.is_tracing()


def take_snapshot() -> tracemalloc.Snapshot:
    """Return a fresh ``tracemalloc`` snapshot. Caller ensures tracking is on."""
    return tracemalloc.take_snapshot()


def save_baseline(name: str, snapshot: tracemalloc.Snapshot) -> None:
    """Store *snapshot* under *name* for later ``compare_with``."""
    _baselines[name] = snapshot


def get_baseline(name: str) -> tracemalloc.Snapshot | None:
    """Return the snapshot stored under *name*, or ``None`` if unknown."""
    return _baselines.get(name)


def baseline_names() -> list[str]:
    """List currently-stored baseline names, sorted."""
    return sorted(_baselines)


def drop_baseline(name: str) -> bool:
    """Drop the named baseline; return whether it existed."""
    return _baselines.pop(name, None) is not None


def format_top_allocators(
    snapshot: tracemalloc.Snapshot,
    *,
    baseline: tracemalloc.Snapshot | None = None,
    top_n: int = 25,
) -> list[dict[str, Any]]:
    """
    Return the top-*top_n* allocators in *snapshot* (or diff vs *baseline*).

    Each entry: ``{traceback, size_bytes, size_diff_bytes, count, count_diff}``.
    Diff fields are zero when no baseline is supplied. ``traceback`` is
    a list of ``"<file>:<lineno>"`` strings, deepest frame last.
    """
    if baseline is not None:
        stats = snapshot.compare_to(baseline, "lineno")
    else:
        stats = snapshot.statistics("lineno")
    return [_stat_to_dict(stat) for stat in stats[:top_n]]


def system_stats() -> dict[str, Any]:
    """Return cheap process-wide memory stats — safe to call without tracking."""
    stats: dict[str, Any] = {
        "gc_counts": list(gc.get_count()),
        "sys_allocated_blocks": sys.getallocatedblocks(),
        "tracking": tracemalloc.is_tracing(),
    }
    if tracemalloc.is_tracing():
        current, peak = tracemalloc.get_traced_memory()
        stats["tracemalloc_current_bytes"] = current
        stats["tracemalloc_peak_bytes"] = peak
        stats["tracemalloc_overhead_bytes"] = tracemalloc.get_tracemalloc_memory()
    max_rss = _max_rss_bytes()
    if max_rss is not None:
        stats["max_rss_bytes"] = max_rss
    return stats


def _stat_to_dict(stat: Any) -> dict[str, Any]:
    """Convert a ``tracemalloc`` Statistic / StatisticDiff to wire shape."""
    return {
        "traceback": [str(frame) for frame in stat.traceback],
        "size_bytes": stat.size,
        "size_diff_bytes": getattr(stat, "size_diff", 0),
        "count": stat.count,
        "count_diff": getattr(stat, "count_diff", 0),
    }


def _max_rss_bytes() -> int | None:
    """Best-effort RSS high-water mark; ``None`` when ``resource`` is missing."""
    if resource is None:
        return None
    ru_maxrss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports ru_maxrss in bytes; Linux / BSD report it in KB.
    if sys.platform == "darwin":
        return ru_maxrss
    return ru_maxrss * 1024
