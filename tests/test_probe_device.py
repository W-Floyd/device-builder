"""
Tests for ``DeviceStateMonitor.probe_device``.

Adoption / wizard / on-disk YAML drops all need an eager mDNS
probe so the new device card lands fully populated (IP, version,
config_hash, api_encryption) instead of waiting on the next
periodic announcement. The probe takes the cache fast-path when
zeroconf already has the service, otherwise it spawns a fire-
and-forget resolve task.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from esphome_device_builder.controllers._device_state_monitor import (
    DeviceStateMonitor,
)


def _make_monitor() -> DeviceStateMonitor:
    monitor = DeviceStateMonitor.__new__(DeviceStateMonitor)
    monitor._zeroconf = MagicMock()
    monitor._zeroconf.zeroconf = MagicMock()
    monitor._tasks = set()
    return monitor


@pytest.mark.asyncio
async def test_probe_device_cache_hit_applies_synchronously(monkeypatch) -> None:
    """A cached service info applies inline; no task is spawned."""
    monitor = _make_monitor()
    apply = MagicMock()
    monkeypatch.setattr(monitor, "_apply_service_info", apply)

    fake_info = MagicMock()
    fake_info.load_from_cache.return_value = True
    monkeypatch.setattr(
        "esphome_device_builder.controllers._device_state_monitor.AsyncServiceInfo",
        lambda *_args, **_kw: fake_info,
    )

    monitor.probe_device("kitchen")

    apply.assert_called_once_with("kitchen", fake_info)
    assert not monitor._tasks


@pytest.mark.asyncio
async def test_probe_device_cache_miss_spawns_task(monkeypatch) -> None:
    """Cache miss → fire-and-forget resolve task tracked in ``_tasks``."""
    monitor = _make_monitor()
    apply = MagicMock()
    monkeypatch.setattr(monitor, "_apply_service_info", apply)

    fake_info = MagicMock()
    fake_info.load_from_cache.return_value = False
    monkeypatch.setattr(
        "esphome_device_builder.controllers._device_state_monitor.AsyncServiceInfo",
        lambda *_args, **_kw: fake_info,
    )

    async def fake_resolve(*_args, **_kw) -> None:
        return None

    monkeypatch.setattr(monitor, "_resolve_and_apply", fake_resolve)

    monitor.probe_device("kitchen")

    apply.assert_not_called()
    # One task was registered for tracking. Wait it out so the
    # done-callback can run and prune the set; otherwise pending
    # tasks would leak into the next test's event loop.
    assert len(monitor._tasks) == 1
    await asyncio.gather(*monitor._tasks)


def test_probe_device_no_zeroconf_is_a_noop() -> None:
    """Pre-start (or zeroconf-failed) probe must not raise."""
    monitor = DeviceStateMonitor.__new__(DeviceStateMonitor)
    monitor._zeroconf = None
    monitor._tasks = set()

    monitor.probe_device("kitchen")  # no exception, no tasks
    assert not monitor._tasks
