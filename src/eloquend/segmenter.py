"""Incremental, latency-bounded text segmentation."""

from __future__ import annotations

from dataclasses import dataclass
import re

_SENTENCE_END = re.compile(r"[.!?…](?:[\"'»”)]*)?(?=\s|$)")


@dataclass(frozen=True, slots=True)
class SegmenterConfig:
    """Trade prosody for bounded time-to-first-audio."""

    first_chunk_chars: int = 24
    min_chunk_chars: int = 40
    max_chunk_chars: int = 140
    max_wait_ms: int = 180

    def __post_init__(self) -> None:
        if self.first_chunk_chars < 1:
            raise ValueError("first_chunk_chars must be positive")
        if self.min_chunk_chars < 1:
            raise ValueError("min_chunk_chars must be positive")
        if self.max_chunk_chars < max(
            self.first_chunk_chars, self.min_chunk_chars
        ):
            raise ValueError("max_chunk_chars must cover minimum chunk sizes")
        if self.max_wait_ms < 1:
            raise ValueError("max_wait_ms must be positive")


class IncrementalSegmenter:
    """Turn arbitrary LLM token fragments into synthesis-sized phrases."""

    def __init__(self, config: SegmenterConfig | None = None) -> None:
        self.config = config or SegmenterConfig()
        self._buffer = ""
        self._chunks_emitted = 0

    @property
    def has_text(self) -> bool:
        return bool(self._buffer.strip())

    @property
    def buffered_chars(self) -> int:
        return len(self._buffer)

    def append(self, text: str) -> list[str]:
        if not text:
            return []

        self._buffer += text
        chunks: list[str] = []
        while self.has_text:
            sentence_end = self._sentence_end()
            if sentence_end is not None:
                chunks.append(self._take(sentence_end))
                continue

            if len(self._buffer) >= self.config.max_chunk_chars:
                chunks.append(self._take(self._split_at(self.config.max_chunk_chars)))
                continue

            break

        return [chunk for chunk in chunks if chunk]

    def flush_due(self) -> str | None:
        """Flush a phrase when the latency deadline expires."""

        if not self.has_text:
            return None

        minimum = (
            self.config.first_chunk_chars
            if self._chunks_emitted == 0
            else self.config.min_chunk_chars
        )
        if len(self._buffer.strip()) < minimum:
            return None

        return self._take(len(self._buffer))

    def flush(self) -> str | None:
        """Flush all remaining text at end-of-input."""

        if not self.has_text:
            self._buffer = ""
            return None
        return self._take(len(self._buffer))

    def _sentence_end(self) -> int | None:
        match = _SENTENCE_END.search(self._buffer)
        if match is None:
            return None

        end = match.end()
        while end < len(self._buffer) and self._buffer[end].isspace():
            end += 1
        return end

    def _split_at(self, limit: int) -> int:
        bounded = min(limit, len(self._buffer))
        split = self._buffer.rfind(" ", 0, bounded + 1)
        if split <= 0:
            return bounded
        return split + 1

    def _take(self, end: int) -> str:
        chunk = self._buffer[:end].strip()
        self._buffer = self._buffer[end:].lstrip()
        if chunk:
            self._chunks_emitted += 1
        return chunk
