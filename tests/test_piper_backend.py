from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import asyncio
import tempfile
import unittest

from eloquend.backends import PiperBackend


class FakeChunk:
    def __init__(self, audio: bytes) -> None:
        self.audio_int16_bytes = audio


class FakeVoice:
    def __init__(self) -> None:
        self.config = SimpleNamespace(sample_rate=22_050)
        self.requests: list[str] = []

    def synthesize(self, text: str):
        self.requests.append(text)
        return iter((FakeChunk(b"first"), FakeChunk(b"second")))


class PiperBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_is_loaded_once_and_chunks_are_streamed(self) -> None:
        voice = FakeVoice()
        load_calls: list[tuple[str, bool]] = []
        piper = ModuleType("piper")

        class FakePiperVoice:
            @staticmethod
            def load(path: str, *, use_cuda: bool = False) -> FakeVoice:
                load_calls.append((path, use_cuda))
                return voice

        piper.PiperVoice = FakePiperVoice

        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "voice.onnx"
            model_path.write_bytes(b"model")
            backend = PiperBackend(model_path, use_cuda=True)

            with patch.dict("sys.modules", {"piper": piper}):
                await backend.startup()
                await backend.startup()

            self.assertEqual(load_calls, [(str(model_path), True)])
            self.assertEqual(backend.audio_format.sample_rate, 22_050)

            cancelled = asyncio.Event()
            chunks = [
                chunk
                async for chunk in backend.synthesize(
                    "Streaming funktioniert.", cancelled
                )
            ]
            self.assertEqual(chunks, [b"first", b"second"])
            self.assertEqual(voice.requests, ["Streaming funktioniert."])
            await backend.shutdown()

    async def test_pre_cancelled_request_does_not_advance_inference(self) -> None:
        voice = FakeVoice()
        piper = ModuleType("piper")

        class FakePiperVoice:
            @staticmethod
            def load(path: str, *, use_cuda: bool = False) -> FakeVoice:
                return voice

        piper.PiperVoice = FakePiperVoice

        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "voice.onnx"
            model_path.write_bytes(b"model")
            backend = PiperBackend(model_path)

            with patch.dict("sys.modules", {"piper": piper}):
                await backend.startup()

            cancelled = asyncio.Event()
            cancelled.set()
            chunks = [
                chunk
                async for chunk in backend.synthesize("Nicht sprechen.", cancelled)
            ]
            self.assertEqual(chunks, [])
            self.assertEqual(voice.requests, [])
            await backend.shutdown()
