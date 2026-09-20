"""Deterministic test backend; it proves streaming without a model download."""

from __future__ import annotations

from collections.abc import AsyncIterator
import asyncio
import math
import struct

from .base import AudioFormat, SynthesisBackend


class ToneBackend(SynthesisBackend):
    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        frame_ms: int = 20,
        realtime: bool = False,
    ) -> None:
        self._format = AudioFormat(sample_rate=sample_rate)
        self._frame_ms = frame_ms
        self._realtime = realtime

    @property
    def audio_format(self) -> AudioFormat:
        return self._format

    async def synthesize(
        self, text: str, cancelled: asyncio.Event
    ) -> AsyncIterator[bytes]:
        frame_samples = self._format.sample_rate * self._frame_ms // 1000
        duration_ms = max(80, len(text) * 12)
        frame_count = max(1, math.ceil(duration_ms / self._frame_ms))
        phase = 0

        for _ in range(frame_count):
            if cancelled.is_set():
                return

            samples = [
                int(4_000 * math.sin(2 * math.pi * 220 * (phase + index) /
                                    self._format.sample_rate))
                for index in range(frame_samples)
            ]
            phase += frame_samples
            yield struct.pack(f"<{len(samples)}h", *samples)

            if self._realtime:
                await asyncio.sleep(self._frame_ms / 1000)
            else:
                await asyncio.sleep(0)
