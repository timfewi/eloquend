"""Hosted speech backend for OpenAI-compatible audio endpoints.

The backend speaks the OpenAI Audio Speech API shape
(``POST <base>/audio/speech``), which OpenRouter and other hosted providers
expose. Audio is requested as raw ``pcm`` so frames can be forwarded without
decoding. The API key is read from a file at startup; no network request is
made until the first phrase, and the HTTP connection is reused between
phrases to keep time-to-first-audio low.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
import asyncio
import http.client
import json
import queue
import threading
import urllib.parse

from .base import AudioFormat, SynthesisBackend

# Bounded hand-off between the HTTP worker thread and the event loop. Sixteen
# frames is roughly half a second of 24 kHz speech and keeps memory flat when
# the player applies backpressure.
_QUEUE_CHUNKS = 16
_PUT_TIMEOUT_S = 0.25
_TERMINAL_WAIT_S = 2.0
_READ_CHUNK_BYTES = 16 * 1024


class OpenAISpeechBackend(SynthesisBackend):
    """Stream PCM from an OpenAI-compatible ``/audio/speech`` endpoint."""

    def __init__(
        self,
        *,
        api_key_file: str | Path,
        model: str,
        voice: str,
        base_url: str = "https://openrouter.ai/api/v1",
        sample_rate: int = 24_000,
        timeout_s: float = 60.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"unsupported speech base URL: {base_url!r}")

        self._api_key_file = Path(api_key_file)
        self._model = model
        self._voice = voice
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port
        self._target = (parsed.path.rstrip("/") or "") + "/audio/speech"
        self._timeout_s = timeout_s
        self._format = AudioFormat(
            sample_rate=sample_rate,
            sample_width=2,
            channels=1,
            encoding="pcm_s16le",
        )

        self._api_key: str | None = None
        self._connection: http.client.HTTPConnection | None = None
        self._connection_lock = threading.Lock()

    @property
    def audio_format(self) -> AudioFormat:
        if self._api_key is None:
            raise RuntimeError("hosted speech backend has not been started")
        return self._format

    async def startup(self) -> None:
        if self._api_key is not None:
            return
        if not self._model or not self._voice:
            raise RuntimeError("hosted speech backend requires a model and a voice")
        try:
            key = await asyncio.to_thread(self._api_key_file.read_text, "utf-8")
        except OSError as error:
            raise RuntimeError(
                f"cannot read speech API key file {self._api_key_file}: {error}"
            ) from error
        key = key.strip()
        if not key:
            raise RuntimeError(
                f"speech API key file {self._api_key_file} is empty"
            )
        self._api_key = key

    async def warmup(self, text: str) -> None:
        # A hosted request would be billed and add startup latency, so the
        # first real phrase is also the first request.
        return

    async def shutdown(self) -> None:
        self._api_key = None
        self._drop_connection()

    async def synthesize(
        self, text: str, cancelled: asyncio.Event
    ) -> AsyncIterator[bytes]:
        if not text or cancelled.is_set():
            return

        chunks: queue.Queue[Any] = queue.Queue(maxsize=_QUEUE_CHUNKS)
        state: dict[str, Any] = {}

        def worker() -> None:
            try:
                self._stream_blocking(text, cancelled, chunks, state)
            except Exception as error:
                self._finish(chunks, error)
            finally:
                self._finish(chunks, None)

        task = asyncio.create_task(asyncio.to_thread(worker))
        try:
            while True:
                item = await asyncio.to_thread(chunks.get)
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                if cancelled.is_set():
                    return
                yield item
        finally:
            if cancelled.is_set():
                self._abort(state)
            await task

    def _stream_blocking(
        self,
        text: str,
        cancelled: asyncio.Event,
        chunks: "queue.Queue[Any]",
        state: dict[str, Any],
    ) -> None:
        payload = json.dumps(
            {
                "model": self._model,
                "input": text,
                "voice": self._voice,
                "response_format": "pcm",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/pcm",
        }

        with self._connection_lock:
            connection = self._connection_for_request()
            state["connection"] = connection
            try:
                connection.request("POST", self._target, body=payload, headers=headers)
                response = connection.getresponse()
                state["response"] = response
                if response.status != 200:
                    detail = response.read(4096).decode("utf-8", "replace").strip()
                    raise RuntimeError(self._error_message(response.status, detail))

                while not cancelled.is_set():
                    chunk = response.read1(_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    if not self._offer(chunks, chunk, cancelled):
                        break
                if cancelled.is_set():
                    self._discard_locked()
            except RuntimeError:
                self._discard_locked()
                raise
            except Exception as error:
                self._discard_locked()
                if not cancelled.is_set():
                    raise RuntimeError(
                        f"speech request failed: {type(error).__name__}: {error}"
                    ) from error
            finally:
                state.pop("response", None)
                state.pop("connection", None)

    def _connection_for_request(self) -> http.client.HTTPConnection:
        if self._connection is None:
            connection_cls = (
                http.client.HTTPSConnection
                if self._scheme == "https"
                else http.client.HTTPConnection
            )
            self._connection = connection_cls(
                self._host, self._port, timeout=self._timeout_s
            )
        return self._connection

    def _discard_locked(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def _drop_connection(self) -> None:
        with self._connection_lock:
            self._discard_locked()

    @staticmethod
    def _abort(state: dict[str, Any]) -> None:
        for target in (state.get("response"), state.get("connection")):
            if target is not None:
                try:
                    target.close()
                except Exception:
                    pass

    @staticmethod
    def _offer(
        chunks: "queue.Queue[Any]", item: bytes, cancelled: asyncio.Event
    ) -> bool:
        while not cancelled.is_set():
            try:
                chunks.put(item, timeout=_PUT_TIMEOUT_S)
                return True
            except queue.Full:
                continue
        return False

    @staticmethod
    def _finish(chunks: "queue.Queue[Any]", item: Any) -> None:
        attempts = max(1, int(_TERMINAL_WAIT_S / _PUT_TIMEOUT_S))
        for _ in range(attempts):
            try:
                chunks.put(item, timeout=_PUT_TIMEOUT_S)
                return
            except queue.Full:
                continue

    @staticmethod
    def _error_message(status: int, detail: str) -> str:
        hint = ""
        if status in (401, 403):
            hint = " (check the API key)"
        elif status == 429:
            hint = " (rate limited)"
        suffix = f": {detail[:300]}" if detail else ""
        return f"speech request failed with HTTP {status}{hint}{suffix}"
