# Privacy and host-specific tuning

Hardware information does not belong in this public repository.

## Public/private boundary

The public eloquend source contains only generic capabilities and conservative
defaults. It does not inspect `/proc/cpuinfo`, enumerate PCI devices, invoke
vendor utilities, read a hostname or collect a machine identifier.

Host-specific values belong in the user's separate private Nix or Home Manager
configuration:

- whether CUDA is enabled;
- the selected model path;
- segmentation thresholds and queue sizes;
- service overrides and resource limits.

The public module exposes these as options but does not contain their values.
Do not create a host overlay inside this repository.

## Telemetry

The daemon has no telemetry and no remote API. Per-request metrics contain only:

- terminal status;
- time-to-first-audio and total request duration;
- phrase count;
- emitted audio byte count;
- a local error string when inference fails.

These metrics are returned over the request's local Unix socket. They are not
persisted or uploaded. The Home Manager service restricts the process to
`AF_UNIX`, preventing Internet sockets.

Nix may fetch the hash-pinned runtime and voice while building. Model inference
itself is local and does not download anything.

## Tune without disclosing hardware

Run the benchmark on the private machine:

```console
PYTHONPATH=src python3 benchmarks/ttfa.py \
  --backend piper   --model /private/path/voice.onnx   --iterations 20
```

The JSON result contains aggregate latency and real-time-factor measurements,
not hardware identification. It can remain private. If outside help is wanted,
sharing only `ttfa_ms.p50`, `ttfa_ms.p95` and `rtf.p95` is sufficient for
an initial tuning pass.

Keep the resulting tuning in the private Home Manager configuration:

```nix
services.eloquend = {
  useCuda = true;
  tuning = {
    firstChunkChars = 24;
    minChunkChars = 40;
    maxChunkChars = 140;
    maxWaitMs = 180;
    segmentQueueSize = 4;
    audioQueueSize = 8;
  };
};
```

The values above are generic defaults, not a record of any particular machine.
