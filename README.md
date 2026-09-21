# eloquend

A low-latency local TTS daemon that accepts text fragments while an LLM is
still generating and returns raw audio before the complete answer exists.

The model is loaded once at daemon startup. Request sessions remain cheap and
independently cancellable.

## Current status

This repository contains a working streaming core and three backends:

- `tone`: deterministic dependency-free backend for protocol tests and
  latency measurements.
- `piper`: optional real TTS backend using Piper's Python API. The
  `.onnx` voice is loaded once, warmed up, and reused.
- `openai`: hosted OpenAI-compatible speech API (for example OpenRouter).
  No local model is loaded; raw PCM streams from the provider and the HTTP
  connection is reused between phrases.

The repository started as an empty Git worktree. It now includes a reusable
Nix package and Home Manager systemd-user module. They deliberately consume
the caller's pinned nixpkgs instead of adding an unlocked standalone flake.
The real Piper/model closure still has to be built and measured on the target
machine.

## Why this is faster than a lazy CLI wrapper

A command-per-utterance design pays model loading, ONNX initialization and
process startup for every answer. Here those costs happen before the first
request:

```text
LLM tokens -> segmenter -> bounded queue -> hot model -> PCM frames -> player
                 |               |
            180 ms deadline   cancellation/backpressure
```

Sentence punctuation starts synthesis immediately. Without punctuation, the
first sufficiently long phrase is released after a configurable deadline.
Audio frames use PCM s16le, so no complete WAV or MP3 file must be buffered.

## Run the reference backend

The core has no runtime dependencies outside Python 3.11 or newer.

```console
PYTHONPATH=src python3 -m eloquend --backend tone
```

In a second terminal, stream simulated LLM tokens. Audio is written as raw
16 kHz mono PCM to stdout and metrics to stderr:

```console
PYTHONPATH=src python3 examples/stream_tokens.py > speech.pcm
```

## Run Piper

Install the maintained Piper Python package in the runtime environment and
provide a voice model plus its adjacent JSON configuration:

```console
PYTHONPATH=src python3 -m eloquend \
  --backend piper \
  --model /models/de_DE-voice-medium.onnx
```

Use `--cuda` only with a matching GPU-enabled ONNX Runtime closure. The
daemon serializes inference through one model lane to keep tail latency
predictable. Scale with explicit model replicas rather than unbounded
per-request inference.

## Run a hosted backend

Point the `openai` backend at an OpenAI-compatible audio API. The key is read
from a runtime file, never from the Nix store or the command line:

```console
PYTHONPATH=src python3 -m eloquend \
  --backend openai \
  --model microsoft/mai-voice-2-flash \
  --voice de-DE-Klaus:MAI-Voice-2 \
  --api-key-file /run/secrets/openrouter-api-key \
  --base-url https://openrouter.ai/api/v1
```

The provider must return raw `pcm` (24 kHz mono signed 16-bit little-endian
by default; override with `--sample-rate` when a provider differs). Every
phrase is sent to the provider, so enable this backend only when the text may
leave the machine. See [docs/privacy.md](docs/privacy.md).

## Integrate with pinned Nixpkgs

Import [nix/module.nix](nix/module.nix) from an existing Home Manager
configuration whose flake already pins nixpkgs:

```nix
{ pkgs, ... }:
let
  deVoice =
    pkgs.callPackage
      ./path/to/eloquend/nix/voices/de_DE-thorsten-medium.nix
      { };
in
{
  imports = [ ./path/to/eloquend/nix/module.nix ];

  services.eloquend = {
    enable = true;
    model = "${deVoice}/${deVoice.modelFile}";
  };
}
```

The supplied German Thorsten voice is medium quality, 22.05 kHz, about 63 MB
and published by the voice repository under MIT; its training dataset is
listed as CC0. The repository revision, ONNX SHA256 and configuration SHA256
are fixed in the derivation.

The same module selects a hosted backend with `backend`, `model`, `voice`,
`apiKeyFile`, `baseUrl` and `sampleRate`. When the openai backend is active,
the service receives Internet address families and a CA bundle; the Piper
path stays restricted to `AF_UNIX`.

The default package uses `pkgs.piper-tts` when that attribute exists and
disables Piper's training, HTTP and alignment extras. To build only the
dependency-free reference core:

```nix
pkgs.callPackage ./nix/package.nix { withPiper = false; }
```

The service starts at user login, warms the model, stores its socket below
`%t`, and restarts after failures. See
[docs/nix-migration.md](docs/nix-migration.md) for the staged replacement of
the old reader wrapper.

## Connect an LLM

Use `TTSClient` and call `send_text(token)` for every generated token.
Sending and receiving must run concurrently, as shown in
[examples/stream_tokens.py](examples/stream_tokens.py). Call `end()` when
generation completes or `cancel()` immediately when the user interrupts.

The Unix socket is mode 0600. The binary protocol and frame types are described
in [docs/protocol.md](docs/protocol.md).

Hardware and host tuning stay outside this repository. The daemon performs no
hardware discovery or telemetry; request metrics remain on the local socket.
See [docs/privacy.md](docs/privacy.md) for the private Home Manager overlay and
benchmark workflow.

## Verify latency

```console
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 benchmarks/ttfa.py \
  --backend tone --iterations 50 --max-p95-ms 50
PYTHONPATH=src python3 benchmarks/ttfa.py \
  --backend piper --model /models/voice.onnx --iterations 20
```

The benchmark reports warm p50/p95 time-to-first-audio and real-time factor.
Measure cold startup separately. Practical initial gates for the real model
are:

- warm TTFA p95 below 500 ms, then optimize toward 300 ms;
- real-time factor p95 below 1.0;
- no audio underruns with a 60–120 ms player buffer;
- cancellation observed before another queued phrase begins.

See [docs/architecture.md](docs/architecture.md) for the design and
[docs/nix-migration.md](docs/nix-migration.md) for replacing a
`lazy-reader-nix` wrapper.
