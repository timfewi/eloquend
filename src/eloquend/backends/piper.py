"""Optional Piper backend with one permanently loaded voice model."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
import asyncio

from .base import AudioFormat, SynthesisBackend


def _next_audio(iterator: Iterator[Any]) -> tuple[bool, bytes]:
    try:
        chunk = next(iterator)
    except StopIteration:
        return True, b""
    return False, bytes(chunk.audio_int16_bytes)


class PiperBackend(SynthesisBackend):
    """Stream Piper AudioChunk objects without reloading the ONNX model."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        use_cuda: bool = False,
    ) -> None:
        self._model_path = Path(model_path)
        self._use_cuda = use_cuda
        self._voice: Any | None = None
        self._format: AudioFormat | None = None
        self._inference_lock = asyncio.Lock()

    @property
    def audio_format(self) -> AudioFormat:
        if self._format is None:
            raise RuntimeError("Piper backend has not been started")
        return self._format

    async def startup(self) -> None:
        if self._voice is not None:
            return
        if not self._model_path.is_file():
            raise FileNotFoundError(f"Piper model not found: {self._model_path}")

        try:
            from piper import PiperVoice
        except ImportError as error:
            raise RuntimeError(
                "Piper backend requires the 'piper' Python package"
            ) from error

        self._voice = await asyncio.to_thread(
            PiperVoice.load,
            str(self._model_path),
            use_cuda=self._use_cuda,
        )
        self._format = AudioFormat(
            sample_rate=int(self._voice.config.sample_rate),
            sample_width=2,
            channels=1,
        )

    async def shutdown(self) -> None:
        self._voice = None
        self._format = None

    async def synthesize(
        self, text: str, cancelled: asyncio.Event
    ) -> AsyncIterator[bytes]:
        if self._voice is None:
            raise RuntimeError("Piper backend has not been started")

        if cancelled.is_set():
            return

        # A single model lane gives predictable latency and avoids GPU/CPU
        # oversubscription. Multiple model replicas can be added by a scheduler.
        async with self._inference_lock:
            if cancelled.is_set():
                return
            iterator = iter(self._voice.synthesize(text))
            while not cancelled.is_set():
                done, audio = await asyncio.to_thread(_next_audio, iterator)
                if done:
                    return
                if audio:
                    yield audio
