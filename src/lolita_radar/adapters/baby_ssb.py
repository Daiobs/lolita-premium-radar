from __future__ import annotations

from ..fetcher import fetch_text
from ..models import RadarItem
from ..parsers import parse_baby_ssb_news
from .base import SourceAdapter


class BabySsbAdapter(SourceAdapter):
    def fetch_items(self) -> list[RadarItem]:
        html_text = fetch_text(
            self.config.url,
            timeout_seconds=int(self.config.options.get("timeout_seconds", 20)),
        )
        return parse_baby_ssb_news(html_text, self.config.url, source=self.config.name)
