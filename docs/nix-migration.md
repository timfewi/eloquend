# Migration from a lazy reader wrapper

A `lazy-reader-nix`-style command wrapper is the wrong lifecycle boundary
for strict TTS latency: laziness saves idle memory but puts process and model
startup directly before every utterance.

## Target Nix layout

Package three concerns independently:

1. **Core daemon** — the Python source, launcher, tests and systemd user unit.
2. **Inference closure** — Piper, ONNX Runtime and optionally the matching CUDA
   closure.
3. **Voice data** — a fixed-output derivation or explicitly configured local
   path containing both the ONNX model and its JSON configuration.

The systemd user service should start at graphical/session login, use
`Restart=on-failure`, and point its socket into `%t` (the user runtime
directory). Pre-start model download is deliberately excluded from the
service: startup must be deterministic and must not require network access.

Conceptual service command:

```text
eloquend --backend piper \
  --model /nix/store/<hash>-de-voice/model.onnx \
  --socket %t/eloquend.sock
```

## Private host overlay

Import the public module from the machine's separate private Home Manager
configuration. Model selection, CUDA choice, tuning values and service resource
limits stay there; they are not committed to eloquend. The daemon does not
collect hardware identifiers. See [privacy.md](privacy.md).

## Migration sequence

1. Keep the current reader as the audio consumer temporarily.
2. Replace its per-request TTS command with `TTSClient` over the Unix socket.
3. Feed LLM tokens immediately instead of passing the completed answer.
4. Compare warm TTFA p50/p95 and underruns against the old path.
5. Remove the legacy wrapper only after cancellation and service restart tests
   pass.
6. Add an idle model TTL only if measured memory pressure justifies the cold
   starts it introduces.

## Pinning requirement

Do not add an unlocked `nixos-unstable` input merely to make the prototype
look packaged. The final flake must commit its lock file, use the repository's
selected nixpkgs revision, and test both the package and the user service.
CUDA and CPU outputs should be separate so CPU users do not acquire a GPU
runtime closure.

[../nix/package.nix](../nix/package.nix) and
[../nix/module.nix](../nix/module.nix) now implement the reusable package and
Home Manager user service. The
[pinned German voice](../nix/voices/de_DE-thorsten-medium.nix) supplies a
22.05 kHz medium model with fixed repository revision and content hashes. The
voice repository declares MIT and the model card lists the dataset as CC0. These expressions intentionally consume the importing repository's
pinned nixpkgs; a second standalone flake is unnecessary.

The current workspace still has no `flake.nix`, `.envrc` or usable `nix`
executable, so Nix evaluation and service activation could not be run here.
The importing repository must verify both against its own lock file. A recent
nixpkgs is required for the maintained Python-based `pkgs.piper-tts`;
`withPiper = false` remains available for testing the streaming core alone.
