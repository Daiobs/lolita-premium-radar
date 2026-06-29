from __future__ import annotations

import re

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
        text = apply_ignore_patterns(parse_generic_text(html_text), self.config.options.get("ignore_patterns") or [])
        max_content_chars = int(self.config.options.get("max_content_chars", 12000))
        if max_content_chars > 0:
            text = text[:max_content_chars]
        matches = keyword_matches(text, self.config.keywords)
        min_keyword_hits = int(self.config.options.get("min_keyword_hits", 1 if self.config.keywords else 0))
        if len(matches) < min_keyword_hits:
            return []
        title = build_title(
            template=str(self.config.options.get("title_template") or ""),
            fallback=str(self.config.options.get("title") or f"{self.config.name} page match"),
            source=self.config.name,
            url=self.config.url,
            matches=matches,
        )
        if matches and not self.config.options.get("title_template"):
            title = f"{title}: {', '.join(matches[:5])}"
        return [
            RadarItem(
                source=self.config.name,
                title=title,
                url=self.config.url,
                status=classify_title(title + " " + text[:500]),
                content=text,
                metadata={
                    "matched_keywords": matches,
                    "content_change_alert": bool(self.config.options.get("content_change_alert", True)),
                },
            )
        ]


def apply_ignore_patterns(text: str, patterns: list[object]) -> str:
    cleaned = text
    for pattern in patterns:
        raw = str(pattern)
        if not raw:
            continue
        try:
            cleaned = re.sub(raw, " ", cleaned, flags=re.IGNORECASE)
        except re.error:
            cleaned = cleaned.replace(raw, " ")
    return " ".join(cleaned.split())


def build_title(template: str, fallback: str, source: str, url: str, matches: list[str]) -> str:
    if not template:
        return fallback
    match_text = ", ".join(matches)
    try:
        return template.format(source=source, url=url, matches=match_text, matched_keywords=match_text)
    except (KeyError, ValueError):
        return fallback
