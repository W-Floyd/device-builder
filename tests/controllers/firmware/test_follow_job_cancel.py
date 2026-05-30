"""``follow_job`` registers its stream so ``devices/stop_stream`` can cancel it."""

from __future__ import annotations

import asyncio

import pytest

from esphome_device_builder.models import EventType, FirmwareJob, JobStatus, JobType

from ...conftest import FakeWebSocketClient
from .conftest import FirmwareControllerFactory, make_follow_race_controller


async def test_live_follow_registers_and_stop_stream_cancels(
    firmware_controller_factory: FirmwareControllerFactory,
) -> None:
    """A live follow is registered, cancellable by id, and detaches on cancel."""
    job = FirmwareJob(
        job_id="abc",
        configuration="kitchen.yaml",
        job_type=JobType.COMPILE,
        status=JobStatus.RUNNING,
        output=["history\n"],
    )
    controller = make_follow_race_controller(firmware_controller_factory, job)
    bus = controller._db.bus
    client = FakeWebSocketClient()

    follow_task = asyncio.create_task(
        controller.follow_job(job_id="abc", client=client, message_id="m1")
    )
    # Let follow_job run through registration + snapshot and park on
    # the drain loop with its listener attached.
    for _ in range(50):
        await asyncio.sleep(0)
        if "m1" in client._stream_tasks:
            break

    assert "m1" in client._stream_tasks
    assert len(bus._listeners.get(EventType.JOB_OUTPUT, set())) == 1

    # This is exactly what ``devices/stop_stream`` invokes.
    assert client.cancel_stream("m1") is True

    with pytest.raises(asyncio.CancelledError):
        await follow_task

    # Drain pending callbacks so the handler's ``finally`` (unregister)
    # and ``stream_events``'s listener teardown both run.
    for _ in range(20):
        await asyncio.sleep(0)

    assert "m1" not in client._stream_tasks
    assert bus._listeners.get(EventType.JOB_OUTPUT, set()) == set()


async def test_terminal_follow_unregisters_without_leak(
    firmware_controller_factory: FirmwareControllerFactory,
) -> None:
    """A terminal job replays + ends and leaves no registry entry behind."""
    job = FirmwareJob(
        job_id="abc",
        configuration="kitchen.yaml",
        job_type=JobType.COMPILE,
        status=JobStatus.COMPLETED,
        output=["line a\n"],
        exit_code=0,
    )
    controller = make_follow_race_controller(firmware_controller_factory, job)
    client = FakeWebSocketClient()

    await controller.follow_job(job_id="abc", client=client, message_id="m1")

    assert "m1" not in client._stream_tasks
    assert client.cancel_stream("m1") is False
