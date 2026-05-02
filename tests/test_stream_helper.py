"""Tests for the stream-line helper."""

from __future__ import annotations

import asyncio

from esphome_device_builder.helpers.stream import iter_lines


def _reader_from(*chunks: bytes) -> asyncio.StreamReader:
    """Build a ``StreamReader`` pre-fed with *chunks* and EOF."""
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    return reader


async def _collect(reader: asyncio.StreamReader) -> list[str]:
    return [line async for line in iter_lines(reader)]


async def test_carriage_return_progress_chunks_flush_separately() -> None:
    r"""The exact case from issue #83 — three ``\r`` chunks must surface independently."""
    reader = _reader_from(b"chunk1\rchunk2\rchunk3\n")
    assert await _collect(reader) == ["chunk1\r", "chunk2\r", "chunk3\n"]


async def test_mixed_newline_and_carriage_return() -> None:
    reader = _reader_from(b"a\nb\rc\n")
    assert await _collect(reader) == ["a\n", "b\r", "c\n"]


async def test_crlf_pair_coalesces_into_single_chunk() -> None:
    r"""``\r\n`` (Windows line endings) must surface as one chunk, not two.

    Without coalescing, ``rstrip("\n\r")`` callers would emit a spurious
    empty event for every line on Windows.
    """
    reader = _reader_from(b"line\r\n")
    assert await _collect(reader) == ["line\r\n"]


async def test_crlf_split_across_reads_still_coalesces() -> None:
    r"""When ``\r`` and ``\n`` arrive in separate reads, still coalesce them."""
    reader = asyncio.StreamReader()

    async def feed() -> None:
        reader.feed_data(b"alpha\r")
        await asyncio.sleep(0)
        reader.feed_data(b"\nbeta\r\n")
        reader.feed_eof()

    feeder = asyncio.create_task(feed())
    chunks = await _collect(reader)
    await feeder
    assert chunks == ["alpha\r\n", "beta\r\n"]


async def test_lone_carriage_return_at_eof_is_flushed() -> None:
    r"""A trailing ``\r`` with no following ``\n`` (and EOF) flushes as-is."""
    reader = _reader_from(b"progress\r")
    assert await _collect(reader) == ["progress\r"]


async def test_carriage_return_followed_by_non_newline_does_not_coalesce() -> None:
    r"""``\r`` followed by a regular byte stays a standalone terminator."""
    reader = _reader_from(b"a\rb\n")
    assert await _collect(reader) == ["a\r", "b\n"]


async def test_trailing_bytes_without_terminator_are_flushed_at_eof() -> None:
    reader = _reader_from(b"trailing")
    assert await _collect(reader) == ["trailing"]


async def test_empty_stream_yields_nothing() -> None:
    reader = _reader_from()
    assert await _collect(reader) == []


async def test_invalid_utf8_is_replaced() -> None:
    reader = _reader_from(b"x\xe9y\n")
    chunks = await _collect(reader)
    assert chunks == ["x�y\n"]


async def test_split_across_reads_buffers_correctly() -> None:
    """Terminator detection still works when a line straddles two reads."""
    reader = asyncio.StreamReader()

    async def feed() -> None:
        reader.feed_data(b"par")
        await asyncio.sleep(0)
        reader.feed_data(b"tial\nsecond\n")
        reader.feed_eof()

    feeder = asyncio.create_task(feed())
    chunks = await _collect(reader)
    await feeder
    assert chunks == ["partial\n", "second\n"]


async def test_chunk_size_smaller_than_line_still_works() -> None:
    """Small ``chunk_size`` exercises the cross-read buffer path on every byte."""
    reader = _reader_from(b"abcdef\nxyz\r")
    collected = [line async for line in iter_lines(reader, chunk_size=2)]
    assert collected == ["abcdef\n", "xyz\r"]
