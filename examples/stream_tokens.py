"""Minimal LLM-token-to-TTS integration example."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import argparse
import asyncio
import os
import sys

from eloquend.client import TTSClient
from eloquend.protocol import FrameType


async def fake_llm_tokens(text: str) -> AsyncIterator[str]:
    for word in text.split():
        yield word + " "
        await asyncio.sleep(0.03)


async def run(socket_path: Path, text: str) -> None:
    client = await TTSClient.connect(socket_path, request_id="example")

    async def send() -> None:
        async for token in fake_llm_tokens(text):
            await client.send_text(token)
        await client.end()

    async def receive() -> None:
        async for frame in client.events():
            if frame.kind is FrameType.AUDIO:
                sys.stdout.buffer.write(frame.payload)
                sys.stdout.buffer.flush()
            elif frame.kind is FrameType.METRIC:
                print(frame.decode_json(), file=sys.stderr)

    try:
        await asyncio.gather(send(), receive())
    finally:
        await client.close()


def main() -> None:
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/tmp")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--socket",
        type=Path,
        default=Path(runtime) / (
            "eloquend.sock"
            if "XDG_RUNTIME_DIR" in os.environ
            else f"eloquend-{os.getuid()}.sock"
        ),
    )
    parser.add_argument(
        "text",
        nargs="?",
        default="Hallo! Diese Wörter kommen wie bei einem Sprachmodell einzeln an.",
    )
    args = parser.parse_args()
    asyncio.run(run(args.socket, args.text))


if __name__ == "__main__":
    main()
