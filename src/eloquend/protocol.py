"""Length-prefixed full-duplex protocol for Unix sockets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import asyncio
import json
import struct

_HEADER = struct.Struct("!BI")
MAX_PAYLOAD_BYTES = 1_048_576


class FrameType(IntEnum):
    START = 0x01
    TEXT = 0x02
    FLUSH = 0x03
    END = 0x04
    CANCEL = 0x05

    READY = 0x81
    AUDIO = 0x82
    METRIC = 0x83
    DONE = 0x84
    ERROR = 0xFF


@dataclass(frozen=True, slots=True)
class Frame:
    kind: FrameType
    payload: bytes = b""

    @classmethod
    def json(cls, kind: FrameType, value: object) -> "Frame":
        return cls(
            kind,
            json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            ),
        )

    def decode_json(self) -> object:
        return json.loads(self.payload.decode("utf-8"))


def encode_frame(frame: Frame) -> bytes:
    if len(frame.payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("frame payload exceeds limit")
    return _HEADER.pack(int(frame.kind), len(frame.payload)) + frame.payload


async def read_frame(reader: asyncio.StreamReader) -> Frame:
    header = await reader.readexactly(_HEADER.size)
    raw_kind, size = _HEADER.unpack(header)
    if size > MAX_PAYLOAD_BYTES:
        raise ValueError("frame payload exceeds limit")
    try:
        kind = FrameType(raw_kind)
    except ValueError as error:
        raise ValueError(f"unknown frame type: {raw_kind:#x}") from error
    return Frame(kind, await reader.readexactly(size))


async def write_frame(writer: asyncio.StreamWriter, frame: Frame) -> None:
    writer.write(encode_frame(frame))
    await writer.drain()
