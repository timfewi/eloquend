"""Async client for feeding LLM token fragments into the daemon."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import asyncio

from .protocol import Frame, FrameType, read_frame, write_frame


class TTSClient:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        ready: dict[str, object],
    ) -> None:
        self._reader = reader
        self._writer = writer
        self.ready = ready
        self._input_closed = False
        self._cancel_sent = False
        self._terminal_received = False
        self._write_lock = asyncio.Lock()

    @classmethod
    async def connect(
        cls,
        socket_path: str | Path,
        *,
        request_id: str | None = None,
    ) -> "TTSClient":
        reader, writer = await asyncio.open_unix_connection(socket_path)
        options = {} if request_id is None else {"request_id": request_id}
        await write_frame(writer, Frame.json(FrameType.START, options))
        frame = await read_frame(reader)
        if frame.kind is not FrameType.READY:
            writer.close()
            await writer.wait_closed()
            raise RuntimeError(f"expected READY, received {frame.kind.name}")
        try:
            ready = frame.decode_json()
            if not isinstance(ready, dict):
                raise RuntimeError("READY payload is not a JSON object")
        except Exception:
            writer.close()
            await writer.wait_closed()
            raise
        return cls(reader, writer, ready)

    async def send_text(self, text: str) -> None:
        if not text:
            return
        await self._send(Frame(FrameType.TEXT, text.encode("utf-8")))

    async def flush(self) -> None:
        await self._send(Frame(FrameType.FLUSH))

    async def end(self) -> None:
        if self._input_closed:
            return
        self._input_closed = True
        await self._send(Frame(FrameType.END), allow_input_closed=True)

    async def cancel(self) -> None:
        if self._cancel_sent or self._terminal_received:
            return
        self._input_closed = True
        self._cancel_sent = True
        await self._send(Frame(FrameType.CANCEL), allow_input_closed=True)

    async def events(self) -> AsyncIterator[Frame]:
        while True:
            frame = await read_frame(self._reader)
            yield frame
            if frame.kind in (FrameType.DONE, FrameType.ERROR):
                self._terminal_received = True
                return

    async def close(self) -> None:
        if not self._terminal_received:
            await self.cancel()
        self._writer.close()
        await self._writer.wait_closed()

    async def _send(
        self,
        frame: Frame,
        *,
        allow_input_closed: bool = False,
    ) -> None:
        if self._input_closed and not allow_input_closed:
            raise RuntimeError("request input has ended")
        async with self._write_lock:
            await write_frame(self._writer, frame)
