from __future__ import annotations

import asyncio
import unittest

from eloquend.protocol import Frame, FrameType, encode_frame, read_frame


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_binary_frame_round_trip(self) -> None:
        source = Frame(FrameType.AUDIO, b"\x00\x01\xff")
        reader = asyncio.StreamReader()
        reader.feed_data(encode_frame(source))
        reader.feed_eof()

        self.assertEqual(await read_frame(reader), source)

    async def test_json_frame_round_trip(self) -> None:
        source = Frame.json(FrameType.READY, {"sample_rate": 16_000})
        reader = asyncio.StreamReader()
        reader.feed_data(encode_frame(source))
        reader.feed_eof()

        decoded = await read_frame(reader)
        self.assertEqual(decoded.kind, FrameType.READY)
        self.assertEqual(decoded.decode_json(), {"sample_rate": 16_000})

    def test_oversized_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_frame(Frame(FrameType.TEXT, b"x" * 1_048_577))
