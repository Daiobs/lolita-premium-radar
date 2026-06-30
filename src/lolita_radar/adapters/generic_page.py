from __future__ import annotations

import re

from ..fetcher import fetch_text
from ..models import RadarItem, classify_title
from ..parsers import LinkCandidate, parse_generic_text, parse_links
from ..rules import keyword_matches
from .base import SourceAdapter


DEFAULT_IGNORE_PATTERNS = [
    r"updated at[:：]?\s*[0-9: /.-]+",
    r"last updated[:：]?\s*[0-9: /.-]+",
    r"\d{4}[./-]\d{1,2}[./-]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?",
    r"\b(?:view count|views|page views)[:：]?\s*[\d,]+",
    r"(?:浏览量|阅读量|访问量|閲覧数|ビュー)[:：]?\s*[\d,]+",
]
NAVIGATION_PATTERNS = [
    r"\b(login|account|cart|privacy|contact|company|shop list)\b",
    r"(登录|登入|购物车|隐私|联系|会社概要|ログイン|お問い合わせ)",
]
NAVIGATION_TOKENS = {
    "login",
    "account",
    "cart",
    "privacy",
    "contact",
    "company",
    "shop list",
    "登录",
    "登入",
    "购物车",
    "隐私",
    "联系",
    "会社概要",
    "カート",
    "ログイン",
    "お問い合わせ",
}
NAVIGATION_TOKEN_KEYS = {token.casefold() for token in NAVIGATION_TOKENS}


class GenericPageAdapter(SourceAdapter):
    def fetch_items(self) -> list[RadarItem]:
        html_text = fetch_text(
            self.config.url,
            timeout_seconds=int(self.config.options.get("timeout_seconds", 20)),
        )
        patterns = DEFAULT_IGNORE_PATTERNS + NAVIGATION_PATTERNS + list(self.config.options.get("ignore_patterns") or [])
        text = suppress_duplicate_segments(strip_navigation_tokens(apply_ignore_patterns(parse_generic_text(html_text), patterns)))
        max_content_chars = int(self.config.options.get("max_content_chars", 12000))
        if max_content_chars > 0:
            text = text[:max_content_chars]
        matches = keyword_matches(text, self.config.keywords)
        min_keyword_hits = int(self.config.options.get("min_keyword_hits", 1 if self.config.keywords else 0))
        if len(matches) < min_keyword_hits:
            return []
        link_items = linked_shop_items(
            html_text=html_text,
            config=self.config,
            page_text=text,
            page_matches=matches,
        )
        if link_items:
            return link_items
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
                    "shop": {
                        "name": str(self.config.options.get("shop_name") or self.config.name),
                        "url": self.config.url,
                    },
                    "item": {
                        "title": str(self.config.options.get("item_title") or title),
                        "url": self.config.url,
                    },
                    "source_type": "generic_page",
                    "matched_keywords": matches,
                    "drop_keywords": matches,
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


def strip_navigation_tokens(text: str) -> str:
    kept = []
    for token in text.split():
        normalized = token.strip(" |/\\-_:：[]()（）・,，.。!！?？").casefold()
        if normalized in NAVIGATION_TOKEN_KEYS:
            continue
        kept.append(token)
    return " ".join(kept)


def build_title(template: str, fallback: str, source: str, url: str, matches: list[str]) -> str:
    if not template:
        return fallback
    match_text = ", ".join(matches)
    try:
        return template.format(source=source, url=url, matches=match_text, matched_keywords=match_text)
    except (KeyError, ValueError):
        return fallback


def linked_shop_items(html_text: str, config, page_text: str, page_matches: list[str]) -> list[RadarItem]:
    if config.options.get("extract_item_links", True) is False:
        return []
    shop_name = str(config.options.get("shop_name") or config.name)
    shop_url = str(config.options.get("shop_url") or config.url)
    items = []
    for link in parse_links(html_text, config.url):
        if is_navigation_link(link):
            continue
        haystack = f"{link.title} {link.context} {link.url}"
        matches = keyword_matches(haystack, config.keywords)
        if not matches:
            continue
        title = link.title.strip()
        if not title:
            continue
        content = f"{link.text} {page_text[:500]}".strip()
        items.append(
            RadarItem(
                source=config.name,
                title=title,
                url=link.url,
                status=classify_title(f"{title} {haystack}"),
                content=content,
                metadata={
                    "shop": {"name": shop_name, "url": shop_url},
                    "item": {"title": title, "url": link.url},
                    "source_type": "generic_page",
                    "matched_keywords": matches,
                    "drop_keywords": matches,
                    "content_change_alert": bool(config.options.get("content_change_alert", True)),
                },
            )
        )
    return dedupe_items(items)


def is_navigation_link(link: LinkCandidate) -> bool:
    lowered = f"{link.title} {link.url}".casefold()
    if any(token in lowered for token in NAVIGATION_TOKEN_KEYS):
        return True
    return not link.url.startswith(("http://", "https://"))


def dedupe_items(items: list[RadarItem]) -> list[RadarItem]:
    seen = set()
    deduped = []
    for item in items:
        if item.identity_hash in seen:
            continue
        seen.add(item.identity_hash)
        deduped.append(item)
    return deduped


def suppress_duplicate_segments(text: str) -> str:
    seen = set()
    kept = []
    for segment in re.split(r"(?<=[。.!?])\s+|\s{2,}", text):
        cleaned = segment.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(cleaned)
    return " ".join(kept)
