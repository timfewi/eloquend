"""Streaming engine: segmentation, backpressure, cancellation, and metrics."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from typing import Literal
import asyncio
import time

from .backends.base import SynthesisBackend
from .segmenter import IncrementalSegmenter, SegmenterConfig


@dataclass(frozen=True, slots=True)
class EngineConfig:
    segmenter: SegmenterConfig = field(default_factory=SegmenterConfig)
    segment_queue_size: int = 4
    audio_queue_size: int = 8
    warmup_text: str = "Bereit."

    def __post_init__(self) -> None:
        if self.segment_queue_size < 1:
            raise ValueError("segment_queue_size must be positive")
        if self.audio_queue_size < 1:
            raise ValueError("audio_queue_size must be positive")


@dataclass(frozen=True, slots=True)
class EngineEvent:
    kind: Literal["audio", "metric", "done"]
    payload: bytes | dict[str, object]


class StreamingEngine:
    """Own one hot backend and create independently cancellable sessions."""

    def __init__(
        self,
        backend: SynthesisBackend,
        config: EngineConfig | None = None,
    ) -> None:
        self.backend = backend
        self.config = config or EngineConfig()
        self._started = False

    async def startup(self) -> None:
        if self._started:
            return
        await self.backend.startup()
        if self.config.warmup_text:
            await self.backend.warmup(self.config.warmup_text)
        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return
        await self.backend.shutdown()
        self._started = False

    def open_session(self) -> "EngineSession":
        if not self._started:
            raise RuntimeError("engine has not been started")
        return EngineSession(self.backend, self.config)


class EngineSession:
    def __init__(self, backend: SynthesisBackend, config: EngineConfig) -> None:
        self._backend = backend
        self._config = config
        self._segmenter = IncrementalSegmenter(config.segmenter)
        self._segments: asyncio.Queue[str | None] = asyncio.Queue(
            maxsize=config.segment_queue_size
        )
        self._output: asyncio.Queue[EngineEvent] = asyncio.Queue(
            maxsize=config.audio_queue_size
        )
        self._cancelled = asyncio.Event()
        self._input_closed = False
        self._input_started_ns: int | None = None
        self._first_audio_ns: int | None = None
        self._audio_bytes = 0
        self._segment_count = 0
        self._deadline_task: asyncio.Task[None] | None = None
        self._worker_task = asyncio.create_task(self._run())

    async def append_text(self, text: str) -> None:
        self._ensure_open()
        if not text:
            return
        if self._input_started_ns is None:
            self._input_started_ns = time.monotonic_ns()

        for segment in self._segmenter.append(text):
            await self._segments.put(segment)
        self._ensure_deadline()

    async def flush(self) -> None:
        self._ensure_open()
        segment = self._segmenter.flush()
        if segment:
            await self._segments.put(segment)
        self._cancel_deadline()

    async def finish(self) -> None:
        if self._input_closed:
            return
        segment = self._segmenter.flush()
        if segment:
            await self._segments.put(segment)
        self._input_closed = True
        self._cancel_deadline()
        await self._segments.put(None)

    async def cancel(self) -> None:
        if self._cancelled.is_set() or self._worker_task.done():
            return
        self._input_closed = True
        self._cancelled.set()
        self._cancel_deadline()

        while True:
            try:
                self._segments.get_nowait()
            except asyncio.QueueEmpty:
                break
        await self._segments.put(None)

    async def close(self) -> None:
        """Release a disconnected session without interrupting model inference.

        The event sender must be stopped before calling this method. Discarding
        its queue lets a suspended backend finish and release the shared model
        lane, even when the audio queue or terminal events are backpressured.
        """
        await self.cancel()

        async def discard_output() -> None:
            while True:
                await self._output.get()

        discard = asyncio.create_task(discard_output())
        try:
            await self._worker_task
        finally:
            discard.cancel()
            with suppress(asyncio.CancelledError):
                await discard

    async def events(self) -> AsyncIterator[EngineEvent]:
        while True:
            event = await self._output.get()
            yield event
            if event.kind == "done":
                await self._worker_task
                return

    def _ensure_open(self) -> None:
        if self._input_closed:
            raise RuntimeError("session input has ended")

    def _ensure_deadline(self) -> None:
        if not self._segmenter.has_text:
            return
        if self._deadline_task is None or self._deadline_task.done():
            self._deadline_task = asyncio.create_task(self._deadline_loop())

    def _cancel_deadline(self) -> None:
        if self._deadline_task is not None:
            self._deadline_task.cancel()
            self._deadline_task = None

    async def _deadline_loop(self) -> None:
        delay = self._config.segmenter.max_wait_ms / 1000
        try:
            while not self._input_closed and self._segmenter.has_text:
                await asyncio.sleep(delay)
                segment = self._segmenter.flush_due()
                if segment:
                    await self._segments.put(segment)
        except asyncio.CancelledError:
            return

    async def _run(self) -> None:
        status = "completed"
        error_detail: str | None = None
        try:
            while True:
                segment = await self._segments.get()
                if segment is None or self._cancelled.is_set():
                    break

                self._segment_count += 1
                async with aclosing(
                    self._backend.synthesize(segment, self._cancelled)
                ) as audio_stream:
                    async for audio in audio_stream:
                        if self._cancelled.is_set():
                            status = "cancelled"
                            break
                        now = time.monotonic_ns()
                        if self._first_audio_ns is None:
                            self._first_audio_ns = now
                        self._audio_bytes += len(audio)
                        await self._output.put(EngineEvent("audio", audio))

                if self._cancelled.is_set():
                    status = "cancelled"
                    break
        except Exception as error:
            status = "error"
            error_detail = f"{type(error).__name__}: {error}"
        finally:
            if self._cancelled.is_set():
                status = "cancelled"

            finished_ns = time.monotonic_ns()
            started_ns = self._input_started_ns or finished_ns
            ttfa_ms = (
                (self._first_audio_ns - started_ns) / 1_000_000
                if self._first_audio_ns is not None
                else None
            )
            metrics: dict[str, object] = {
                "status": status,
                "ttfa_ms": ttfa_ms,
                "total_ms": (finished_ns - started_ns) / 1_000_000,
                "segments": self._segment_count,
                "audio_bytes": self._audio_bytes,
            }
            if error_detail is not None:
                metrics["error"] = error_detail
            await self._output.put(EngineEvent("metric", metrics))
            await self._output.put(EngineEvent("done", {"status": status}))
