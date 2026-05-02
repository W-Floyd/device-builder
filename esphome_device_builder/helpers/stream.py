"""Helpers for consuming subprocess output streams."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

_DEFAULT_CHUNK_SIZE = 4096


async def iter_lines(
    reader: asyncio.StreamReader,
    *,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> AsyncIterator[str]:
    r"""
    Yield decoded chunks from *reader*, split on ``\n`` _or_ ``\r``.

    Carriage-return-based in-place updates (esptool's
    ``Writing at 0x... (5%)\r``, PlatformIO's progress bars) need to
    survive the pipe instead of getting buffered until the next
    newline; the default ``async for line in reader`` only splits on
    ``\n`` and leaves them piling up. Each emitted chunk keeps its
    trailing terminator so the consumer can decide whether to append a
    new line or overwrite the last one. ``\r\n`` is treated as a
    single terminator so Windows-style line endings don't produce
    spurious empty chunks.

    Bytes are decoded as UTF-8 with ``errors="replace"``. Trailing
    bytes that arrive without a terminator are flushed at EOF.
    """
    buf = b""
    while True:
        data = await reader.read(chunk_size)
        if not data:
            if buf:
                yield buf.decode("utf-8", errors="replace")
            return
        buf += data
        while buf:
            nl = buf.find(b"\n")
            cr = buf.find(b"\r")
            if nl == -1 and cr == -1:
                break  # need more bytes before we can split
            if nl == -1:
                idx = cr
            elif cr == -1:
                idx = nl
            else:
                idx = min(nl, cr)
            # If ``\r`` is the last byte we've seen, defer — the next
            # read may bring a ``\n`` we want to coalesce with it.
            if buf[idx] == 0x0D and idx == len(buf) - 1:
                break
            end = idx + 1
            if buf[idx] == 0x0D and idx + 1 < len(buf) and buf[idx + 1] == 0x0A:
                end = idx + 2
            chunk = buf[:end]
            buf = buf[end:]
            yield chunk.decode("utf-8", errors="replace")
