from __future__ import annotations

from collections.abc import AsyncIterator
import asyncio
from pathlib import Path
import tempfile
import unittest

from eloquend.backends import AudioFormat, SynthesisBackend
from eloquend.client import TTSClient
from eloquend.engine import EngineConfig, StreamingEngine
from eloquend.protocol import Frame, FrameType, read_frame, write_frame
from eloquend.server import TTSServer


class LockedBackend(SynthesisBackend):
    """Synthetic model lane held across enough chunks to fill the queue."""

    def __init__(self, queue_size: int) -> None:
        self.lock = asyncio.Lock()
        self.backpressured = asyncio.Event()
        self.queue_size = queue_size

    @property
    def audio_format(self) -> AudioFormat:
        return AudioFormat(sample_rate=16_000)

    async def synthesize(
        self, text: str, cancelled: asyncio.Event
    ) -> AsyncIterator[bytes]:
        async with self.lock:
            for index in range(self.queue_size + 2):
                if cancelled.is_set():
                    return
                if index == self.queue_size:
                    self.backpressured.set()
                yield b"\x01\x00"


class DisconnectingServer(TTSServer):
    """Reproduce a failed sender while incoming text is backpressured."""

    def __init__(self, engine, socket_path, backend):
        super().__init__(engine, socket_path)
        self.backend = backend
        self.first = True

    async def _send_events(self, session, writer):
        if self.first:
            self.first = False
            await self.backend.backpressured.wait()
            raise ConnectionResetError("synthetic disconnected player")
        await super()._send_events(session, writer)


class DisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_releases_model_without_an_event_consumer(self) -> None:
        for queue_size in (1, 8):
            with self.subTest(queue_size=queue_size):
                backend = LockedBackend(queue_size)
                engine = StreamingEngine(
                    backend,
                    EngineConfig(warmup_text="", audio_queue_size=queue_size),
                )
                await engine.startup()
                try:
                    first = engine.open_session()
                    await first.append_text("Interrupted speech.")
                    await first.finish()
                    await asyncio.wait_for(backend.backpressured.wait(), 1)
                    self.assertTrue(backend.lock.locked())

                    await asyncio.wait_for(first.close(), 1)
                    self.assertFalse(backend.lock.locked())
                    await asyncio.wait_for(first.close(), 1)

                    second = engine.open_session()
                    await second.append_text("Next speech.")
                    await second.finish()

                    async def collect():
                        return [event async for event in second.events()]

                    events = await asyncio.wait_for(collect(), 1)
                    self.assertTrue(any(event.kind == "audio" for event in events))
                    self.assertEqual(events[-1].payload["status"], "completed")
                    await asyncio.wait_for(second.close(), 1)
                finally:
                    await engine.shutdown()

    async def test_failed_sender_unblocks_full_text_and_audio_queues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = LockedBackend(1)
            engine = StreamingEngine(
                backend,
                EngineConfig(
                    warmup_text="", audio_queue_size=1, segment_queue_size=1
                ),
            )
            server = DisconnectingServer(engine, Path(directory) / "tts.sock", backend)
            await server.start()
            try:
                reader, writer = await asyncio.open_unix_connection(
                    Path(directory) / "tts.sock"
                )
                await write_frame(writer, Frame.json(FrameType.START, {}))
                self.assertIs((await read_frame(reader)).kind, FrameType.READY)
                await write_frame(
                    writer,
                    Frame(FrameType.TEXT, b"Synthetic interrupted sentence. " * 100),
                )
                self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
                writer.close()
                await writer.wait_closed()

                second = await TTSClient.connect(Path(directory) / "tts.sock")
                await second.send_text("Next speech.")
                await second.end()

                async def collect():
                    return [frame async for frame in second.events()]

                frames = await asyncio.wait_for(collect(), 1)
                self.assertTrue(any(frame.kind is FrameType.AUDIO for frame in frames))
                self.assertEqual(frames[-1].decode_json()["status"], "completed")
                await second.close()
            finally:
                await server.close()
