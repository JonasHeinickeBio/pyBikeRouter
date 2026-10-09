"""Downloading BRouter map tiles on request.

Only ever started by an explicit call (the web form's *Download* button after the user was
told the size): a tile is 100-200 MB. Everything that could go wrong with a download that
large is bounded here -- the source is the configured segment location (the official
brouter.de by default) and nothing else, names are validated tile names, one job runs at a
time, sizes and free disk space are checked before the first byte is stored, and a tile only
appears under its real name once it is complete (``.part`` file, then an atomic rename).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SEGMENT_NAME_RE = re.compile(r"^[EW]\d{1,3}_[NS]\d{1,2}$")
MAX_SEGMENTS_PER_JOB = 4
# Kept free on the disk beyond the tiles themselves.
_DISK_MARGIN_BYTES = 200 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_USER_AGENT = "bike-routing-agent/0.1 (+https://github.com/JonasHeinickeBio/pyBikeRouter)"

SegmentState = Literal["pending", "downloading", "done", "present", "failed"]
JobState = Literal["running", "done", "failed"]


class SegmentInfo(BaseModel):
    name: str
    present: bool
    # From the source's Content-Length; None when it could not be asked.
    size_bytes: int | None = None


class SegmentProgress(BaseModel):
    name: str
    state: SegmentState = "pending"
    bytes: int = 0
    total_bytes: int | None = None
    error: str | None = None


class DownloadJob(BaseModel):
    id: str
    state: JobState = "running"
    segments: list[SegmentProgress] = Field(default_factory=list)
    error: str | None = None


class DownloadBusyError(Exception):
    """A download is already running."""


class SegmentDownloader:
    def __init__(
        self,
        directory: Path | str,
        *,
        base_url: str,
        max_bytes: int,
        timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._dir = Path(directory)
        self._base_url = base_url if base_url.endswith("/") else base_url + "/"
        self._max_bytes = max_bytes
        self._timeout_s = timeout_s
        self._client = client
        self._jobs: dict[str, DownloadJob] = {}
        self._running: asyncio.Task[None] | None = None

    # ------------------------------------------------------------- inspection

    def free_bytes(self) -> int:
        return shutil.disk_usage(self._dir).free

    def is_present(self, name: str) -> bool:
        return (self._dir / f"{name}.rd5").is_file()

    async def inspect(self, names: list[str]) -> list[SegmentInfo]:
        """Which of the tiles exist already, and how big each one is at the source."""
        names = self._valid(names)
        sizes = await asyncio.gather(*(self._remote_size(n) for n in names))
        return [
            SegmentInfo(name=n, present=self.is_present(n), size_bytes=size)
            for n, size in zip(names, sizes, strict=True)
        ]

    # ------------------------------------------------------------------- jobs

    def start(self, names: list[str]) -> DownloadJob:
        """Begin downloading the tiles not present yet; ``DownloadBusyError`` if a job runs."""
        names = self._valid(names)
        if self._running is not None and not self._running.done():
            raise DownloadBusyError("a map download is already running")
        job = DownloadJob(
            id=uuid.uuid4().hex,
            segments=[
                SegmentProgress(name=n, state="present" if self.is_present(n) else "pending")
                for n in names
            ],
        )
        self._jobs[job.id] = job
        # Keep a short history only: one job at a time, the web form asks about the latest.
        for old in list(self._jobs)[:-5]:
            del self._jobs[old]
        self._running = asyncio.get_running_loop().create_task(self._run(job))
        return job

    def job(self, job_id: str) -> DownloadJob | None:
        return self._jobs.get(job_id)

    async def wait(self) -> None:
        """Wait for the running job (tests, shutdown)."""
        if self._running is not None:
            await asyncio.shield(self._running)

    # --------------------------------------------------------------- internals

    @staticmethod
    def _valid(names: list[str]) -> list[str]:
        unique = list(dict.fromkeys(names))
        bad = [n for n in unique if not SEGMENT_NAME_RE.fullmatch(n)]
        if bad:
            raise ValueError(f"not BRouter tile names: {bad}")
        if not 1 <= len(unique) <= MAX_SEGMENTS_PER_JOB:
            raise ValueError(f"between 1 and {MAX_SEGMENTS_PER_JOB} tiles per download")
        return unique

    def _url(self, name: str) -> str:
        return f"{self._base_url}{name}.rd5"

    async def _remote_size(self, name: str) -> int | None:
        try:
            if self._client is not None:
                response = await self._client.head(
                    self._url(name), headers={"User-Agent": _USER_AGENT}, timeout=self._timeout_s
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_s, headers={"User-Agent": _USER_AGENT}
                ) as client:
                    response = await client.head(self._url(name))
        except httpx.HTTPError:
            return None
        length = response.headers.get("content-length")
        return int(length) if response.status_code == 200 and length and length.isdigit() else None

    async def _run(self, job: DownloadJob) -> None:
        try:
            for progress in job.segments:
                if progress.state == "present":
                    continue
                await self._download_one(progress)
            job.state = "done"
        except Exception as exc:  # the job reports it; nothing may escape a background task
            logger.warning("map download failed: %s", exc)
            job.state = "failed"
            job.error = str(exc) or type(exc).__name__
            for progress in job.segments:
                if progress.state in ("pending", "downloading"):
                    progress.state = "failed"
                    progress.error = progress.error or "not downloaded"

    async def _download_one(self, progress: SegmentProgress) -> None:
        progress.state = "downloading"
        target = self._dir / f"{progress.name}.rd5"
        part = self._dir / f"{progress.name}.rd5.part"
        try:
            async with self._open(progress.name) as response:
                if response.status_code != 200:
                    raise RuntimeError(
                        f"{self._url(progress.name)} answered HTTP {response.status_code}"
                    )
                length = response.headers.get("content-length")
                if not length or not length.isdigit():
                    raise RuntimeError("the source did not say how big the tile is")
                total = int(length)
                if total > self._max_bytes:
                    raise RuntimeError(
                        f"{progress.name} is {total // 2**20} MB, over the "
                        f"{self._max_bytes // 2**20} MB limit"
                    )
                if self.free_bytes() < total + _DISK_MARGIN_BYTES:
                    raise RuntimeError(
                        f"not enough free disk space for {progress.name} "
                        f"({total // 2**20} MB plus a margin)"
                    )
                progress.total_bytes = total
                with part.open("wb") as handle:
                    async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                        handle.write(chunk)
                        progress.bytes += len(chunk)
                        if progress.bytes > total:
                            raise RuntimeError("the source sent more than it announced")
                    handle.flush()
                    os.fsync(handle.fileno())
                if progress.bytes != total:
                    raise RuntimeError(f"incomplete download ({progress.bytes} of {total} bytes)")
            os.replace(part, target)
            progress.state = "done"
        except BaseException as exc:
            progress.state = "failed"
            progress.error = str(exc) or type(exc).__name__
            with contextlib.suppress(OSError):
                part.unlink()
            raise

    @contextlib.asynccontextmanager
    async def _open(self, name: str) -> AsyncIterator[httpx.Response]:
        """The streamed GET of a tile. Redirects are not followed: the source stays the source."""
        url = self._url(name)
        if self._client is not None:
            async with self._client.stream(
                "GET", url, headers={"User-Agent": _USER_AGENT}, timeout=self._timeout_s
            ) as response:
                yield response
            return
        timeout = httpx.Timeout(self._timeout_s, read=60.0)
        async with (
            httpx.AsyncClient(
                timeout=timeout, headers={"User-Agent": _USER_AGENT}, follow_redirects=False
            ) as client,
            client.stream("GET", url) as response,
        ):
            yield response
