"""Backend contract for hot, reusable synthesis models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
import asyncio


@dataclass(frozen=True, slots=True)
class AudioFormat:
    sample_rate: int
    sample_width: int = 2
    channels: int = 1
    encoding: str = "pcm_s16le"

    def as_dict(self) -> dict[str, int | str]:
        return {
            "sample_rate": self.sample_rate,
            "sample_width": self.sample_width,
            "channels": self.channels,
            "encoding": self.encoding,
        }


class SynthesisBackend(ABC):
    """One model instance that is loaded once and reused."""

    @property
    @abstractmethod
    def audio_format(self) -> AudioFormat:
        raise NotImplementedError

    async def startup(self) -> None:
        """Load model weights and allocate inference resources."""

    async def warmup(self, text: str) -> None:
        cancelled = asyncio.Event()
        async for _ in self.synthesize(text, cancelled):
            pass

    async def shutdown(self) -> None:
        """Release inference resources."""

    @abstractmethod
    def synthesize(
        self, text: str, cancelled: asyncio.Event
    ) -> AsyncIterator[bytes]:
        raise NotImplementedError
