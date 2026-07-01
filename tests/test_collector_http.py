import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lolita_radar.collector.http import fetch_text


class CollectorHttpTests(unittest.TestCase):
    def test_fetch_success_uses_user_agent_and_latency(self) -> None:
        seen_headers = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                seen_headers["user_agent"] = self.headers.get("User-Agent", "")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *_args) -> None:
                return

        server, url = self.server(Handler)
        try:
            result = fetch_text(url, user_agent="test-agent", retries=0)
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(result.text, "ok")
        self.assertEqual(result.status_code, 200)
        self.assertGreaterEqual(result.latency_ms, 0)
        self.assertEqual(seen_headers["user_agent"], "test-agent")

    def test_429_returns_degraded_warning(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(429)
                self.end_headers()

            def log_message(self, *_args) -> None:
                return

        server, url = self.server(Handler)
        try:
            result = fetch_text(url, retries=0)
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.text, "")
        self.assertIn("http 429 degraded", result.warnings[0])

    def test_500_raises_after_retries(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(500)
                self.end_headers()

            def log_message(self, *_args) -> None:
                return

        server, url = self.server(Handler)
        try:
            with self.assertRaisesRegex(RuntimeError, "fetch failed"):
                fetch_text(url, retries=1, backoff=0)
        finally:
            server.shutdown()
            server.server_close()

    def test_timeout_raises_after_retries(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                time.sleep(0.2)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args) -> None:
                return

        server, url = self.server(Handler)
        try:
            with self.assertRaisesRegex(RuntimeError, "fetch failed"):
                fetch_text(url, timeout=0.01, retries=1, backoff=0)
        finally:
            server.shutdown()
            server.server_close()

    def server(self, handler_cls):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        return server, f"http://{host}:{port}/"


if __name__ == "__main__":
    unittest.main()
