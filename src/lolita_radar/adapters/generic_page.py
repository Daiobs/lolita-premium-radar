from __future__ import annotations

from ..fetcher import fetch_text
from ..models import RadarItem, classify_title
from ..parsers import parse_generic_text
from ..rules import keyword_matches
from .base import SourceAdapter


class GenericPageAdapter(SourceAdapter):
    def fetch_items(self) -> list[RadarItem]:
        html_text = fetch_text(
            self.config.url,
            timeout_seconds=int(self.config.options.get("timeout_seconds", 20)),
        )
        text = parse_generic_text(html_text)
        matches = keyword_matches(text, self.config.keywords)
        if self.config.keywords and not matches:
            return []
        title = self.config.options.get("title") or f"{self.config.name} page match"
        if matches:
            title = f"{title}: {', '.join(matches[:5])}"
        return [
            RadarItem(
                source=self.config.name,
                title=title,
                url=self.config.url,
                status=classify_title(title + " " + text[:500]),
                content=text,
                metadata={"matched_keywords": matches},
            )
        ]
