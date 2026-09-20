"""Command-line entry point."""

from __future__ import annotations

from pathlib import Path
import argparse
import asyncio
import os

from .backends import PiperBackend, ToneBackend
from .engine import EngineConfig, StreamingEngine
from .segmenter import SegmenterConfig
from .server import TTSServer


def _default_socket() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "eloquend.sock"
    return Path("/tmp") / f"eloquend-{os.getuid()}.sock"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Low-latency local streaming TTS daemon"
    )
    parser.add_argument("--socket", type=Path, default=_default_socket())
    parser.add_argument(
        "--backend", choices=("piper", "tone"), default="piper"
    )
    parser.add_argument("--model", type=Path)
    parser.add_argument("--cuda", action="store_true")
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--first-chunk-chars", type=int, default=24)
    parser.add_argument("--min-chunk-chars", type=int, default=40)
    parser.add_argument("--max-chunk-chars", type=int, default=140)
    parser.add_argument("--max-wait-ms", type=int, default=180)
    parser.add_argument("--segment-queue-size", type=int, default=4)
    parser.add_argument("--audio-queue-size", type=int, default=8)
    return parser


async def _run(args: argparse.Namespace) -> None:
    if args.backend == "piper":
        if args.model is None:
            raise SystemExit("--model is required for the piper backend")
        backend = PiperBackend(args.model, use_cuda=args.cuda)
    else:
        backend = ToneBackend(realtime=True)

    config = EngineConfig(
        segmenter=SegmenterConfig(
            first_chunk_chars=args.first_chunk_chars,
            min_chunk_chars=args.min_chunk_chars,
            max_chunk_chars=args.max_chunk_chars,
            max_wait_ms=args.max_wait_ms,
        ),
        segment_queue_size=args.segment_queue_size,
        audio_queue_size=args.audio_queue_size,
        warmup_text="" if args.no_warmup else "Bereit.",
    )
    server = TTSServer(StreamingEngine(backend, config), args.socket)
    await server.start()
    print(f"eloquend ready on {args.socket}", flush=True)
    try:
        await server.serve_forever()
    finally:
        await server.close()


def main() -> None:
    args = _parser().parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
