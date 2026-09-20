"""Measure warm time-to-first-audio and real-time factor."""

from __future__ import annotations

from pathlib import Path
import argparse
import asyncio
import json
import statistics

from eloquend.backends import PiperBackend, ToneBackend
from eloquend.engine import EngineConfig, StreamingEngine


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


async def _run(args: argparse.Namespace) -> int:
    if args.backend == "piper":
        if args.model is None:
            raise SystemExit("--model is required with --backend piper")
        backend = PiperBackend(args.model, use_cuda=args.cuda)
    else:
        backend = ToneBackend()

    engine = StreamingEngine(backend, EngineConfig())
    await engine.startup()
    samples: list[dict[str, float]] = []
    try:
        for _ in range(args.iterations):
            session = engine.open_session()
            await session.append_text(args.text)
            await session.finish()
            metric: dict[str, object] | None = None
            async for event in session.events():
                if event.kind == "metric":
                    assert isinstance(event.payload, dict)
                    metric = event.payload

            assert metric is not None
            ttfa_ms = metric["ttfa_ms"]
            if not isinstance(ttfa_ms, float):
                raise RuntimeError(f"session produced no audio: {metric}")

            total_ms = float(metric["total_ms"])
            audio_bytes = int(metric["audio_bytes"])
            audio = backend.audio_format
            audio_seconds = (
                audio_bytes
                / audio.sample_rate
                / audio.sample_width
                / audio.channels
            )
            samples.append(
                {
                    "ttfa_ms": ttfa_ms,
                    "total_ms": total_ms,
                    "rtf": total_ms / 1000 / audio_seconds,
                }
            )
    finally:
        await engine.shutdown()

    ttfa = [sample["ttfa_ms"] for sample in samples]
    rtf = [sample["rtf"] for sample in samples]
    result = {
        "backend": args.backend,
        "iterations": args.iterations,
        "ttfa_ms": {
            "min": min(ttfa),
            "mean": statistics.fmean(ttfa),
            "p50": _percentile(ttfa, 0.50),
            "p95": _percentile(ttfa, 0.95),
            "max": max(ttfa),
        },
        "rtf": {
            "mean": statistics.fmean(rtf),
            "p95": _percentile(rtf, 0.95),
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))

    if (
        args.max_p95_ms is not None
        and result["ttfa_ms"]["p95"] > args.max_p95_ms
    ):
        return 1
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("tone", "piper"), default="tone")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--cuda", action="store_true")
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument(
        "--text",
        default="Dies ist ein reproduzierbarer Streaming-Latenztest.",
    )
    parser.add_argument("--max-p95-ms", type=float)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.iterations < 1:
        raise SystemExit("--iterations must be positive")
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
