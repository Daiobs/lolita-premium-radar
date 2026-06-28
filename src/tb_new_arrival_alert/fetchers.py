from __future__ import annotations

import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Protocol


class Fetcher(Protocol):
    def fetch(self, url: str) -> str:
        ...


class HttpFetcher:
    def __init__(self, timeout_seconds: int = 20, user_agent: str | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari/537.36"
        )

    def fetch(self, url: str) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")


class FileFetcher:
    def fetch(self, url: str) -> str:
        path = Path(url.removeprefix("file://"))
        return path.read_text(encoding="utf-8")


class PlaywrightFetcher:
    def __init__(
        self,
        user_data_dir: str = ".browser-profile",
        headless: bool = False,
        wait_seconds: int = 5,
    ) -> None:
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.wait_seconds = wait_seconds

    def fetch(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run: python -m pip install '.[browser]' "
                "and python -m playwright install chromium"
            ) from exc

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=self.headless,
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            if self.wait_seconds > 0:
                time.sleep(self.wait_seconds)
            content = page.content()
            context.close()
            return content


def make_fetcher(config: Dict[str, Any]) -> Fetcher:
    fetcher_config = dict(config.get("fetcher", {}))
    fetcher_type = str(fetcher_config.get("type", "http")).lower()
    if fetcher_type == "http":
        return HttpFetcher(
            timeout_seconds=int(fetcher_config.get("timeout_seconds", 20)),
            user_agent=fetcher_config.get("user_agent"),
        )
    if fetcher_type == "file":
        return FileFetcher()
    if fetcher_type == "playwright":
        return PlaywrightFetcher(
            user_data_dir=str(fetcher_config.get("user_data_dir", ".browser-profile")),
            headless=bool(fetcher_config.get("headless", False)),
            wait_seconds=int(fetcher_config.get("wait_seconds", 5)),
        )
    raise ValueError(f"Unknown fetcher type: {fetcher_type}")
