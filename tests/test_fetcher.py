import urllib.error
import unittest

from lolita_radar import fetcher


class FakeHeaders:
    def get_content_charset(self) -> str:
        return "utf-8"


class FakeResponse:
    headers = FakeHeaders()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def read(self) -> bytes:
        return "ok".encode("utf-8")


class FetcherTests(unittest.TestCase):
    def test_fetch_text_retries_transient_url_error(self) -> None:
        calls = []
        original = fetcher.urllib.request.urlopen

        def flaky_urlopen(_request, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise urllib.error.URLError("temporary ssl eof")
            return FakeResponse()

        try:
            fetcher.urllib.request.urlopen = flaky_urlopen
            body = fetcher.fetch_text("https://example.com", retry_count=1, retry_delay_seconds=0)
        finally:
            fetcher.urllib.request.urlopen = original

        self.assertEqual(body, "ok")
        self.assertEqual(calls, [20, 20])

    def test_fetch_text_does_not_retry_http_error(self) -> None:
        calls = []
        original = fetcher.urllib.request.urlopen

        def http_error(_request, timeout):
            calls.append("called")
            raise urllib.error.HTTPError("https://example.com", 404, "not found", None, None)

        try:
            fetcher.urllib.request.urlopen = http_error
            with self.assertRaises(urllib.error.HTTPError):
                fetcher.fetch_text("https://example.com", retry_count=2, retry_delay_seconds=0)
        finally:
            fetcher.urllib.request.urlopen = original

        self.assertEqual(calls, ["called"])


if __name__ == "__main__":
    unittest.main()
