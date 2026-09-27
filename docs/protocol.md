# Streaming protocol

The local transport is a Unix stream socket. Every frame has a five-byte
network-order header followed by its payload:

```text
+-------------+----------------------+------------------+
| type: u8    | payload length: u32  | payload bytes    |
+-------------+----------------------+------------------+
```

Payloads are limited to 1 MiB. JSON frames use UTF-8; audio frames contain raw
PCM with the format announced by `READY`.

## Frame sequence

| Type | Value | Direction | Payload |
|---|---:|---|---|
| `START` | `0x01` | client → daemon | JSON options |
| `TEXT` | `0x02` | client → daemon | UTF-8 token fragment |
| `FLUSH` | `0x03` | client → daemon | empty |
| `END` | `0x04` | client → daemon | empty |
| `CANCEL` | `0x05` | client → daemon | empty |
| `READY` | `0x81` | daemon → client | JSON audio metadata |
| `AUDIO` | `0x82` | daemon → client | PCM bytes |
| `METRIC` | `0x83` | daemon → client | JSON timings |
| `DONE` | `0x84` | daemon → client | JSON status |
| `ERROR` | `0xff` | daemon → client | JSON error |

The first client frame must be `START`; the first server frame is
`READY`. A connection carries one synthesis session. After `READY`, input
and output are full duplex: the client continues writing `TEXT` while it
reads `AUDIO`.

`FLUSH` forces the buffered text into the synthesis queue without ending the
request. `END` flushes and drains all work. `CANCEL` discards queued
phrases and signals the active backend inference.

Backpressure is deliberate: socket writes and the audio queue are bounded, so
a slow player stops synthesis instead of allowing unbounded memory growth.
Text ingestion itself is capped per session but never blocks the receiver:
`CANCEL` arrives behind queued text on the same stream, so blocking on text
would postpone cancellation until the active phrase completed.
