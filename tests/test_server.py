from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from eloquend.backends import ToneBackend
from eloquend.client import TTSClient
from eloquend.engine import EngineConfig, StreamingEngine
from eloquend.protocol import Frame, FrameType, read_frame, write_frame
from eloquend.server import TTSServer


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_regular_file_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "tts.sock"
            socket_path.write_text("keep", encoding="utf-8")
            server = TTSServer(
                StreamingEngine(
                    ToneBackend(),
                    EngineConfig(warmup_text=""),
                ),
                socket_path,
            )

            with self.assertRaises(FileExistsError):
                await server.start()

            self.assertEqual(socket_path.read_text(encoding="utf-8"), "keep")
            await server.close()

    async def test_cancel_is_accepted_after_end_while_audio_drains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "tts.sock"
            server = TTSServer(
                StreamingEngine(
                    ToneBackend(realtime=True),
                    EngineConfig(warmup_text=""),
                ),
                socket_path,
            )
            await server.start()
            serve_task = asyncio.create_task(server.serve_forever())

            try:
                client = await TTSClient.connect(socket_path)
                await client.send_text(
                    "Dieser längere Text wird während der "
                    "Audioausgabe abgebrochen."
                )
                await client.end()
                events = client.events()
                self.assertEqual((await anext(events)).kind, FrameType.AUDIO)
                await client.cancel()

                status: str | None = None
                async for frame in events:
                    if frame.kind is FrameType.DONE:
                        status = frame.decode_json()["status"]
                self.assertEqual(status, "cancelled")
                await client.close()
            finally:
                serve_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await serve_task
                await server.close()

    async def test_unix_socket_streams_audio_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "tts.sock"
            engine = StreamingEngine(
                ToneBackend(),
                EngineConfig(warmup_text=""),
            )
            server = TTSServer(engine, socket_path)
            await server.start()
            serve_task = asyncio.create_task(server.serve_forever())

            try:
                reader, writer = await asyncio.open_unix_connection(socket_path)
                await write_frame(
                    writer,
                    Frame.json(FrameType.START, {"request_id": "test"}),
                )
                ready = await read_frame(reader)
                self.assertEqual(ready.kind, FrameType.READY)
                self.assertEqual(
                    ready.decode_json()["audio"]["encoding"], "pcm_s16le"
                )

                await write_frame(
                    writer,
                    Frame(FrameType.TEXT, "Hallo vom Stream.".encode()),
                )
                await write_frame(writer, Frame(FrameType.END))

                kinds: list[FrameType] = []
                metric: dict[str, object] | None = None
                while FrameType.DONE not in kinds:
                    frame = await read_frame(reader)
                    kinds.append(frame.kind)
                    if frame.kind is FrameType.METRIC:
                        metric = frame.decode_json()

                self.assertIn(FrameType.AUDIO, kinds)
                self.assertLess(
                    kinds.index(FrameType.AUDIO), kinds.index(FrameType.DONE)
                )
                assert metric is not None
                self.assertEqual(metric["status"], "completed")
                writer.close()
                await writer.wait_closed()
            finally:
                serve_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await serve_task
                await server.close()

            self.assertFalse(socket_path.exists())
