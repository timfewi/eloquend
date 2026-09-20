from __future__ import annotations

from collections.abc import AsyncIterator
import asyncio
import unittest

from eloquend.backends import AudioFormat, SynthesisBackend, ToneBackend
from eloquend.engine import EngineConfig, StreamingEngine
from eloquend.segmenter import SegmenterConfig


class FailingBackend(SynthesisBackend):
    @property
    def audio_format(self) -> AudioFormat:
        return AudioFormat(sample_rate=16_000)

    async def synthesize(
        self, text: str, cancelled: asyncio.Event
    ) -> AsyncIterator[bytes]:
        if False:
            yield b""
        raise RuntimeError("inference failed")


class StreamingEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = EngineConfig(
            segmenter=SegmenterConfig(max_wait_ms=10),
            warmup_text="",
        )
        self.engine = StreamingEngine(ToneBackend(), config)
        await self.engine.startup()

    async def asyncTearDown(self) -> None:
        await self.engine.shutdown()

    async def test_audio_is_streamed_before_done(self) -> None:
        session = self.engine.open_session()
        await session.append_text("Eine kurze Ausgabe.")
        await session.finish()
        events = [event async for event in session.events()]

        kinds = [event.kind for event in events]
        self.assertIn("audio", kinds)
        self.assertLess(kinds.index("audio"), kinds.index("done"))

        metric = next(event.payload for event in events if event.kind == "metric")
        self.assertEqual(metric["status"], "completed")
        self.assertIsNotNone(metric["ttfa_ms"])
        self.assertGreater(metric["audio_bytes"], 0)

    async def test_backend_error_has_one_metric_and_done_event(self) -> None:
        engine = StreamingEngine(
            FailingBackend(),
            EngineConfig(warmup_text=""),
        )
        await engine.startup()
        try:
            session = engine.open_session()
            await session.append_text("Diese Inferenz schlägt fehl.")
            await session.finish()
            events = [event async for event in session.events()]
        finally:
            await engine.shutdown()

        metrics = [event for event in events if event.kind == "metric"]
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0].payload["status"], "error")
        self.assertIn("inference failed", metrics[0].payload["error"])
        self.assertEqual(events[-1].kind, "done")
        self.assertEqual(events[-1].payload["status"], "error")

    async def test_deadline_starts_synthesis_without_end_frame(self) -> None:
        session = self.engine.open_session()
        await session.append_text(
            "Dieser Text ist lang genug für den zeitgesteuerten Start"
        )

        first = await anext(session.events())
        self.assertEqual(first.kind, "audio")
        await session.cancel()
        remaining = [event async for event in session.events()]
        done = next(event for event in remaining if event.kind == "done")
        self.assertEqual(done.payload["status"], "cancelled")
