"""Unix-socket server exposing the full-duplex TTS protocol."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
import asyncio
import stat

from .engine import EngineEvent, EngineSession, StreamingEngine
from .protocol import Frame, FrameType, read_frame, write_frame


class TTSServer:
    def __init__(self, engine: StreamingEngine, socket_path: str | Path) -> None:
        self._engine = engine
        self._socket_path = Path(socket_path)
        self._server: asyncio.AbstractServer | None = None
        self._socket_identity: tuple[int, int] | None = None

    async def start(self) -> None:
        await self._engine.startup()
        try:
            self._prepare_socket_path()
            self._server = await asyncio.start_unix_server(
                self._handle_client,
                path=self._socket_path,
            )
            socket_stat = self._socket_path.lstat()
            self._socket_identity = (socket_stat.st_dev, socket_stat.st_ino)
            self._socket_path.chmod(0o600)
        except Exception:
            await self._engine.shutdown()
            raise

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self._engine.shutdown()
        self._unlink_socket_if_owned()

    def _prepare_socket_path(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            mode = self._socket_path.lstat().st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(mode):
            raise FileExistsError(
                f"refusing to replace non-socket path: {self._socket_path}"
            )
        self._socket_path.unlink()

    def _unlink_socket_if_owned(self) -> None:
        try:
            socket_stat = self._socket_path.lstat()
        except FileNotFoundError:
            self._socket_identity = None
            return

        identity = (socket_stat.st_dev, socket_stat.st_ino)
        if (
            stat.S_ISSOCK(socket_stat.st_mode)
            and identity == self._socket_identity
        ):
            self._socket_path.unlink()
        self._socket_identity = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        session: EngineSession | None = None
        sender: asyncio.Task[None] | None = None
        receiver: asyncio.Task[None] | None = None
        try:
            first = await read_frame(reader)
            if first.kind is not FrameType.START:
                raise ValueError("first frame must be START")
            if first.payload:
                options = first.decode_json()
                if not isinstance(options, dict):
                    raise ValueError("START payload must be a JSON object")

            session = self._engine.open_session()
            ready = {
                "protocol": 1,
                "audio": self._engine.backend.audio_format.as_dict(),
            }
            await write_frame(writer, Frame.json(FrameType.READY, ready))
            sender = asyncio.create_task(self._send_events(session, writer))

            receiver = asyncio.create_task(self._receive_text(session, reader))
            completed, _ = await asyncio.wait(
                (receiver, sender),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receiver in completed:
                await receiver
            await sender
        except (asyncio.IncompleteReadError, ConnectionError):
            if session is not None:
                await session.cancel()
        except Exception as error:
            if session is not None:
                await session.cancel()
            try:
                await write_frame(
                    writer,
                    Frame.json(
                        FrameType.ERROR,
                        {"error": f"{type(error).__name__}: {error}"},
                    ),
                )
            except ConnectionError:
                pass
        finally:
            tasks = [task for task in (receiver, sender) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            writer.close()
            try:
                if session is not None:
                    await session.close()
            finally:
                with suppress(ConnectionError):
                    await writer.wait_closed()

    async def _receive_text(
        self,
        session: EngineSession,
        reader: asyncio.StreamReader,
    ) -> None:
        while True:
            frame = await read_frame(reader)
            if frame.kind is FrameType.TEXT:
                await session.append_text(frame.payload.decode("utf-8"))
            elif frame.kind is FrameType.FLUSH:
                await session.flush()
            elif frame.kind is FrameType.END:
                await session.finish()
            elif frame.kind is FrameType.CANCEL:
                await session.cancel()
            else:
                raise ValueError(f"unexpected client frame: {frame.kind.name}")

    async def _send_events(
        self,
        session: EngineSession,
        writer: asyncio.StreamWriter,
    ) -> None:
        async for event in session.events():
            await write_frame(writer, self._event_frame(event))

    @staticmethod
    def _event_frame(event: EngineEvent) -> Frame:
        if event.kind == "audio":
            assert isinstance(event.payload, bytes)
            return Frame(FrameType.AUDIO, event.payload)
        if event.kind == "metric":
            return Frame.json(FrameType.METRIC, event.payload)
        return Frame.json(FrameType.DONE, event.payload)
