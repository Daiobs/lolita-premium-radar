from __future__ import annotations

import time
import urllib.error
import urllib.request


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari/537.36"
)


def fetch_text(
    url: str,
    timeout_seconds: int = 20,
    user_agent: str = DEFAULT_USER_AGENT,
    retry_count: int = 2,
    retry_delay_seconds: float = 1.0,
) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    attempts = max(1, int(retry_count) + 1)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError:
            raise
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt >= attempts - 1:
                break
            if retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"failed to fetch {url}")
