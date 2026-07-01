from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_USER_AGENT = "lolita-premium-radar/0.1 (+https://github.com/Daiobs/lolita-premium-radar)"


@dataclass(frozen=True)
class FetchResult:
    text: str
    status_code: int = 200
    latency_ms: int = 0
    warnings: list[str] = field(default_factory=list)


def fetch_text(
    url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int | float = 20,
    retries: int = 1,
    backoff: int | float = 0.25,
) -> FetchResult:
    if url.startswith("file://"):
        return read_local_file(Path(url.removeprefix("file://")))
    path = Path(url)
    if path.exists():
        return read_local_file(path)

    attempts = max(1, int(retries) + 1)
    last_error: Exception | None = None
    started = time.monotonic()
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": user_agent})
            with urlopen(request, timeout=max(0.001, float(timeout))) as response:
                text = response.read().decode("utf-8", "replace")
                return FetchResult(text=text, status_code=int(response.status), latency_ms=elapsed_ms(started))
        except HTTPError as exc:
            last_error = exc
            if exc.code in {403, 429} and attempt == attempts - 1:
                return FetchResult(
                    text="",
                    status_code=int(exc.code),
                    latency_ms=elapsed_ms(started),
                    warnings=[f"http {exc.code} degraded for {url}"],
                )
        except (TimeoutError, socket.timeout) as exc:
            last_error = exc
        except URLError as exc:
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(max(0.0, float(backoff)) * (attempt + 1))
    raise RuntimeError(f"fetch failed for {url}: {last_error}")


def read_local_file(path: Path) -> FetchResult:
    started = time.monotonic()
    return FetchResult(text=path.read_text(encoding="utf-8"), latency_ms=elapsed_ms(started))


def elapsed_ms(started: float) -> int:
    return max(0, int(round((time.monotonic() - started) * 1000)))
