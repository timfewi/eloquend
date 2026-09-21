# Architecture and latency model

## Components

1. The Unix-socket ingress authenticates through filesystem permissions and
   converts protocol frames into session operations.
2. The incremental segmenter receives arbitrary LLM token fragments. Sentence
   endings flush immediately; a 180 ms deadline releases sufficiently long
   text; 140 characters is the hard maximum.
3. A bounded segment queue applies backpressure to the LLM integration.
4. A backend owns a single warm model. Piper inference is serialized through
   one lane for predictable p95 latency.
5. A bounded audio queue streams PCM chunks to the client and propagates slow
   consumers back to synthesis.
6. Every session records TTFA, total duration, segment count, byte count and
   terminal status.

## Lifecycle

```text
process start
    |
load model -> warm-up inference -> READY
                                  |
                         +--------+---------+
                         |                  |
                     session A          session B
                         |                  |
                  complete/cancel    complete/cancel
                         +--------+---------+
                                  |
                         model remains hot
```

The daemon should normally start with the user session. Socket activation
alone leaves model loading on the critical path and does not meet a strict
first-audio target. A future idle policy may unload only the model worker
after a long configurable TTL while leaving the small controller resident.

## Latency budget

Warm TTFA consists of:

```text
token accumulation + segmentation wait + queue wait + first inference chunk
```

The default segmentation deadline is 180 ms. That value is not appropriate
for every model: very short phrases reduce prosody quality, while long phrases
increase TTFA. Tune with actual German prompts and report p50 plus p95 rather
than one favorable sample.

Playback should begin after 60–120 ms of audio is buffered. PCM is preferred
locally. Low-delay Opus is appropriate across a network, but encoding must
operate continuously rather than once per phrase.

## Concurrency and cancellation

One model lane avoids CPU/GPU oversubscription. If concurrent callers become
important, run a measured number of complete backend replicas and schedule
whole sessions or phrases between them. Do not allow each request to create
its own ONNX session.

Cancellation sets a session event, drops queued phrases and stops forwarding
new audio. Some native inference calls cannot be interrupted mid-call; the
largest unavoidable delay is therefore one backend-generated chunk.

## Backend choices

- Piper/ONNX: first implementation for local German CPU inference and low
  operational complexity.
- Higher-quality neural models: place behind the same backend contract after
  measuring warm TTFA and real-time factor on the target GPU.
- Voice-cloning models: use a separate worker pool because their memory and
  latency profile differs substantially.
- Hosted streaming API: implemented as the `openai` backend for
  OpenAI-compatible `/audio/speech` endpoints (for example OpenRouter). It
  streams raw PCM, reuses one keep-alive connection, serializes requests
  through one lane and makes no request at startup. Selecting it changes the
  privacy, cost and failure assumptions.

The protocol intentionally contains no Piper-specific fields, so replacing
the model does not change the LLM or audio-player integration.
