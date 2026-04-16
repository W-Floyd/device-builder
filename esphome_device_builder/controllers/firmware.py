"""Firmware controller — build queue, compile, upload, validate, clean, download."""

from __future__ import annotations

import asyncio
import gzip
import importlib
import logging
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from esphome.components.esp32 import VARIANTS as ESP32_VARIANTS
from esphome.storage_json import StorageJSON, ext_storage_path

from ..controllers.config import _load_metadata, _save_metadata
from ..helpers.api import api_command
from ..models import EventType, FirmwareJob, JobStatus, JobType

if TYPE_CHECKING:
    from ..device_builder import DeviceBuilder

_LOGGER = logging.getLogger(__name__)
_ESPHOME_CMD = [sys.executable, "-m", "esphome"]
_JOBS_KEY = "_firmware_jobs"


class FirmwareController:
    """Manage firmware build jobs with a persistent queue.

    Only one job runs at a time. Jobs are persisted to disk so they
    survive page refreshes and server restarts. Progress is broadcast
    via the event bus to all connected clients.
    """

    def __init__(self, device_builder: DeviceBuilder) -> None:
        self._db = device_builder
        self._queue: asyncio.Queue[FirmwareJob] = asyncio.Queue()
        self._jobs: dict[str, FirmwareJob] = {}
        self._current_job: FirmwareJob | None = None
        self._current_process: asyncio.subprocess.Process | None = None
        self._runner_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the queue processor and restore persisted jobs."""
        await self._load_jobs()
        self._runner_task = self._db.create_background_task(self._run_queue())

    # ------------------------------------------------------------------
    # Queue processing
    # ------------------------------------------------------------------

    async def _run_queue(self) -> None:
        """Background loop: process one job at a time."""
        try:
            while True:
                job = await self._queue.get()
                if job.status == JobStatus.CANCELLED:
                    continue
                await self._execute_job(job)
        except asyncio.CancelledError:
            pass

    async def _execute_job(self, job: FirmwareJob) -> None:
        """Execute a single firmware job."""
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC).isoformat()
        self._current_job = job
        self._db.bus.fire(EventType.JOB_STARTED, {"job": job})
        await self._persist_jobs()

        try:
            config_path = str(self._db.settings.rel_path(job.configuration))
            cmd = self._build_command(job.job_type, config_path, job.port)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._current_process = proc

            assert proc.stdout is not None
            async for line_bytes in proc.stdout:
                line = line_bytes.decode("utf-8", errors="replace")
                job.output.append(line)
                self._db.bus.fire(
                    EventType.JOB_OUTPUT,
                    {
                        "job_id": job.job_id,
                        "line": line,
                    },
                )

            exit_code = await proc.wait()
            job.exit_code = exit_code
            job.status = JobStatus.COMPLETED if exit_code == 0 else JobStatus.FAILED
            job.completed_at = datetime.now(UTC).isoformat()

            event = EventType.JOB_COMPLETED if exit_code == 0 else EventType.JOB_FAILED
            self._db.bus.fire(event, {"job": job})

        except asyncio.CancelledError:
            if self._current_process:
                self._current_process.terminate()
            job.status = JobStatus.CANCELLED
            job.completed_at = datetime.now(UTC).isoformat()
            raise
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error = str(exc)
            job.completed_at = datetime.now(UTC).isoformat()
            self._db.bus.fire(EventType.JOB_FAILED, {"job": job})
            _LOGGER.exception("Job %s failed", job.job_id)
        finally:
            self._current_job = None
            self._current_process = None
            await self._persist_jobs()

    @staticmethod
    def _build_command(job_type: JobType, config_path: str, port: str) -> list[str]:
        """Build the esphome CLI command for a job type."""
        cmd_map = {
            JobType.COMPILE: "compile",
            JobType.UPLOAD: "upload",
            JobType.RUN: "run",
            JobType.VALIDATE: "config",
            JobType.CLEAN: "clean",
        }
        cmd = [*_ESPHOME_CMD, cmd_map[job_type], config_path]
        if job_type in (JobType.UPLOAD, JobType.RUN) and port:
            cmd.extend(["--device", port])
        return cmd

    # ------------------------------------------------------------------
    # Job management
    # ------------------------------------------------------------------

    def _create_job(self, configuration: str, job_type: JobType, port: str = "") -> FirmwareJob:
        """Create a new job and add it to the queue."""
        job = FirmwareJob(
            job_id=uuid4().hex[:12],
            configuration=configuration,
            job_type=job_type,
            created_at=datetime.now(UTC).isoformat(),
            port=port,
        )
        self._jobs[job.job_id] = job
        return job

    async def _enqueue(self, job: FirmwareJob) -> FirmwareJob:
        """Enqueue a job, persist, and fire event."""
        await self._queue.put(job)
        self._db.bus.fire(EventType.JOB_QUEUED, {"job": job})
        await self._persist_jobs()
        return job

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _load_jobs(self) -> None:
        """Load persisted jobs and re-queue incomplete ones."""
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, _load_metadata, self._db.settings.config_dir)
        for job_data in data.get(_JOBS_KEY, []):
            try:
                job = FirmwareJob.from_dict(job_data)
                self._jobs[job.job_id] = job
                # Re-queue incomplete jobs
                if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    job.status = JobStatus.QUEUED
                    await self._queue.put(job)
            except Exception:
                _LOGGER.warning("Failed to restore job: %s", job_data.get("job_id", "?"))

    async def _persist_jobs(self) -> None:
        """Save all jobs to disk."""
        loop = asyncio.get_running_loop()
        config_dir = self._db.settings.config_dir

        def _save() -> None:
            data = _load_metadata(config_dir)
            data[_JOBS_KEY] = [j.to_dict() for j in self._jobs.values()]
            _save_metadata(config_dir, data)

        await loop.run_in_executor(None, _save)

    # ------------------------------------------------------------------
    # API commands
    # ------------------------------------------------------------------

    @api_command("firmware/compile")
    async def compile(self, *, configuration: str, **kwargs: Any) -> FirmwareJob:
        """Queue a compile job."""
        job = self._create_job(configuration, JobType.COMPILE)
        return await self._enqueue(job)

    @api_command("firmware/upload")
    async def upload(self, *, configuration: str, port: str = "", **kwargs: Any) -> FirmwareJob:
        """Queue an upload job."""
        job = self._create_job(configuration, JobType.UPLOAD, port=port)
        return await self._enqueue(job)

    @api_command("firmware/validate")
    async def validate(self, *, configuration: str, **kwargs: Any) -> FirmwareJob:
        """Queue a validation job."""
        job = self._create_job(configuration, JobType.VALIDATE)
        return await self._enqueue(job)

    @api_command("firmware/clean")
    async def clean(self, *, configuration: str, **kwargs: Any) -> FirmwareJob:
        """Queue a build clean job."""
        job = self._create_job(configuration, JobType.CLEAN)
        return await self._enqueue(job)

    @api_command("firmware/run")
    async def run(self, *, configuration: str, port: str = "OTA", **kwargs: Any) -> FirmwareJob:
        """Queue a compile + upload job (esphome run). Defaults to OTA."""
        job = self._create_job(configuration, JobType.RUN, port=port)
        return await self._enqueue(job)

    @api_command("firmware/compile_bulk")
    async def compile_bulk(self, *, configurations: list[str], **kwargs: Any) -> list[FirmwareJob]:
        """Queue multiple compile jobs at once."""
        jobs = []
        for config in configurations:
            job = self._create_job(config, JobType.COMPILE)
            await self._enqueue(job)
            jobs.append(job)
        return jobs

    @api_command("firmware/run_bulk")
    async def run_bulk(
        self, *, configurations: list[str], port: str = "OTA", **kwargs: Any
    ) -> list[FirmwareJob]:
        """Queue compile + upload for multiple devices. Defaults to OTA."""
        jobs = []
        for config in configurations:
            job = self._create_job(config, JobType.RUN, port=port)
            await self._enqueue(job)
            jobs.append(job)
        return jobs

    @api_command("firmware/get_jobs")
    async def get_jobs(
        self,
        *,
        status: JobStatus | str | None = None,
        configuration: str | None = None,
        **kwargs: Any,
    ) -> list[FirmwareJob]:
        """List jobs, optionally filtered by status or configuration."""
        jobs = list(self._jobs.values())
        if status:
            jobs = [j for j in jobs if j.status == status]
        if configuration:
            jobs = [j for j in jobs if j.configuration == configuration]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    @api_command("firmware/get_job")
    async def get_job(self, *, job_id: str, **kwargs: Any) -> FirmwareJob | None:
        """Get a specific job with full output."""
        return self._jobs.get(job_id)

    @api_command("firmware/cancel")
    async def cancel(self, *, job_id: str, **kwargs: Any) -> None:
        """Cancel a queued job. Running jobs cannot be cancelled."""
        job = self._jobs.get(job_id)
        if not job:
            msg = f"Job not found: {job_id}"
            raise ValueError(msg)
        if job.status != JobStatus.QUEUED:
            msg = f"Can only cancel queued jobs, job is {job.status}"
            raise ValueError(msg)
        job.status = JobStatus.CANCELLED
        job.completed_at = datetime.now(UTC).isoformat()
        await self._persist_jobs()

    @api_command("firmware/clear")
    async def clear(self, *, status: JobStatus | str | None = None, **kwargs: Any) -> None:
        """Remove finished jobs from the list.

        If status is given, only remove jobs with that status.
        Otherwise removes completed, failed, and cancelled jobs.
        """
        terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
        to_remove = [
            jid
            for jid, job in self._jobs.items()
            if (status and job.status == status) or (not status and job.status in terminal)
        ]
        for jid in to_remove:
            del self._jobs[jid]
        await self._persist_jobs()

    # ------------------------------------------------------------------
    # Binary download
    # ------------------------------------------------------------------

    @api_command("firmware/get_binaries")
    async def get_binaries(self, *, configuration: str, **kwargs: Any) -> list[dict]:
        """List available firmware binaries for a compiled device.

        Returns [{title, file}] — the file names can be passed to
        firmware/download to retrieve the binary content.
        """
        loop = asyncio.get_running_loop()

        def _get_types() -> list[dict]:
            storage = StorageJSON.load(ext_storage_path(configuration))
            if storage is None:
                return []
            platform = (storage.target_platform or "").lower()
            try:
                if platform.upper() in ESP32_VARIANTS:
                    platform_ = "esp32"
                elif platform in ("rtl87xx", "bk72xx", "ln882x", "libretiny"):
                    platform_ = "libretiny"
                else:
                    platform_ = platform
                module = importlib.import_module(f"esphome.components.{platform_}")
                return module.get_download_types(storage)
            except Exception:
                _LOGGER.warning("Could not determine download types for %s", configuration)
                return []

        return await loop.run_in_executor(None, _get_types)

    @api_command("firmware/download")
    async def download(
        self,
        *,
        configuration: str,
        file: str,
        compressed: bool = False,
        **kwargs: Any,
    ) -> dict:
        """Download a compiled firmware binary.

        Returns {filename, data, content_type} where data is base64-encoded.
        For Web Serial flashing, the frontend decodes the base64 data.
        """
        import base64

        loop = asyncio.get_running_loop()

        def _read_binary() -> dict:
            storage = StorageJSON.load(ext_storage_path(configuration))
            if storage is None or storage.firmware_bin_path is None:
                msg = "No firmware binary — compile the device first"
                raise FileNotFoundError(msg)

            base_dir = storage.firmware_bin_path.parent.resolve()
            path = (base_dir / file).resolve()
            # Path traversal protection
            path.relative_to(base_dir)

            if not path.is_file():
                msg = f"Binary not found: {file}"
                raise FileNotFoundError(msg)

            data = path.read_bytes()
            if compressed:
                data = gzip.compress(data, 9)

            filename = f"{storage.name}-{file}"
            if compressed:
                filename += ".gz"

            return {
                "filename": filename,
                "data": base64.b64encode(data).decode("ascii"),
                "size": len(data),
                "compressed": compressed,
            }

        return await loop.run_in_executor(None, _read_binary)
