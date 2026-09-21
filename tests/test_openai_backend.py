from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import asyncio
import json
import tempfile
import threading
import time
import unittest

from eloquend.backends import OpenAISpeechBackend


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        pass


class _SpeechServer:
    """Minimal OpenAI-compatible audio endpoint for offline tests."""

    def __init__(
        self,
        *,
        status: int = 200,
        chunks: tuple[bytes, ...] = (b"",),
        body: bytes = b"",
        delay_s: float = 0.0,
    ) -> None:
        self.status = status
        self.chunks = chunks
        self.body = body
        self.delay_s = delay_s
        self.requests: list[dict[str, object]] = []
        self.connections = 0

        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                outer.connections += 1
                super().setup()

            def log_message(self, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                outer.requests.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "json": json.loads(raw),
                    }
                )
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(outer.body)))
                    self.end_headers()
                    self.wfile.write(outer.body)
                    return

                total = sum(len(chunk) for chunk in outer.chunks)
                self.send_response(200)
                self.send_header("Content-Type", "audio/pcm")
                self.send_header("Content-Length", str(total))
                self.end_headers()
                for chunk in outer.chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    if outer.delay_s:
                        time.sleep(outer.delay_s)

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class OpenAISpeechBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._key_file = Path(self._directory.name) / "api-key"
        self._key_file.write_text("test-key\n", encoding="utf-8")
        self._servers: list[_SpeechServer] = []

    def tearDown(self) -> None:
        for server in self._servers:
            server.close()

    def _server(self, **kwargs: object) -> _SpeechServer:
        server = _SpeechServer(**kwargs)
        self._servers.append(server)
        return server

    def _backend(self, server: _SpeechServer, **kwargs: object) -> OpenAISpeechBackend:
        return OpenAISpeechBackend(
            api_key_file=self._key_file,
            model="microsoft/mai-voice-2-flash",
            voice="de-DE-Klaus:MAI-Voice-2",
            base_url=server.base_url,
            **kwargs,
        )

    async def test_streams_pcm_and_reuses_the_connection(self) -> None:
        server = self._server(
            chunks=(b"\x01\x02", b"\x03\x04", b"\x05\x06")
        )
        backend = self._backend(server)
        await backend.startup()

        cancelled = asyncio.Event()
        chunks = [
            chunk async for chunk in backend.synthesize("Hallo Welt.", cancelled)
        ]
        self.assertEqual(b"".join(chunks), b"\x01\x02\x03\x04\x05\x06")
        self.assertEqual(backend.audio_format.sample_rate, 24_000)
        self.assertEqual(backend.audio_format.encoding, "pcm_s16le")

        request = server.requests[0]
        self.assertEqual(request["path"], "/v1/audio/speech")
        self.assertEqual(
            request["headers"]["Authorization"], "Bearer test-key"
        )
        self.assertEqual(
            request["json"],
            {
                "model": "microsoft/mai-voice-2-flash",
                "input": "Hallo Welt.",
                "voice": "de-DE-Klaus:MAI-Voice-2",
                "response_format": "pcm",
            },
        )

        chunks = [
            chunk async for chunk in backend.synthesize("Zweiter Satz.", cancelled)
        ]
        self.assertEqual(b"".join(chunks), b"\x01\x02\x03\x04\x05\x06")
        self.assertEqual(server.connections, 1)
        self.assertEqual(len(server.requests), 2)
        await backend.shutdown()

    async def test_missing_key_file_fails_startup(self) -> None:
        server = self._server()
        backend = OpenAISpeechBackend(
            api_key_file=Path(self._directory.name) / "missing",
            model="microsoft/mai-voice-2-flash",
            voice="de-DE-Klaus:MAI-Voice-2",
            base_url=server.base_url,
        )
        with self.assertRaisesRegex(RuntimeError, "cannot read speech API key"):
            await backend.startup()

    async def test_http_error_reports_status_and_body(self) -> None:
        body = json.dumps(
            {"error": {"message": "No auth credentials found"}}
        ).encode()
        server = self._server(status=401, body=body)
        backend = self._backend(server)
        await backend.startup()

        cancelled = asyncio.Event()
        with self.assertRaisesRegex(
            RuntimeError, "HTTP 401.*No auth credentials found"
        ):
            async for _ in backend.synthesize("Hallo.", cancelled):
                pass
        await backend.shutdown()

    async def test_cancellation_stops_the_stream_quickly(self) -> None:
        server = self._server(
            chunks=tuple(bytes([index]) for index in range(200)),
            delay_s=0.02,
        )
        backend = self._backend(server)
        await backend.startup()

        cancelled = asyncio.Event()
        stream = backend.synthesize("Ein langer Satz.", cancelled)
        started = time.monotonic()
        first = await stream.__anext__()
        self.assertEqual(first, b"\x00")
        cancelled.set()
        await stream.aclose()
        self.assertLess(time.monotonic() - started, 1.5)

        # The aborted connection is discarded and the next phrase reconnects.
        cancelled = asyncio.Event()
        chunks = [
            chunk async for chunk in backend.synthesize("Wieder da.", cancelled)
        ]
        self.assertEqual(b"".join(chunks), bytes(range(200)))
        self.assertGreaterEqual(server.connections, 2)
        await backend.shutdown()

    async def test_warmup_does_not_issue_a_request(self) -> None:
        server = self._server(chunks=(b"\x00\x00",))
        backend = self._backend(server)
        await backend.startup()
        await backend.warmup("Bereit.")
        self.assertEqual(server.requests, [])
        await backend.shutdown()

    async def test_rejects_unsupported_base_url(self) -> None:
        with self.assertRaises(ValueError):
            OpenAISpeechBackend(
                api_key_file=self._key_file,
                model="model",
                voice="voice",
                base_url="ftp://example.com/v1",
            )


if __name__ == "__main__":
    unittest.main()
