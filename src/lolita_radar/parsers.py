from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

from .models import RadarItem, classify_title


DATE_RE = re.compile(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})")


@dataclass(frozen=True)
class LinkCandidate:
    title: str
    url: str
    text: str
    published_at: str = ""


class LinkTextParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._stack: list[dict[str, str]] = []
        self.links: list[LinkCandidate] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "a":
            self._stack.append(
                {
                    "href": attr.get("href", ""),
                    "text": attr.get("title", "") or attr.get("aria-label", ""),
                }
            )

    def handle_data(self, data: str) -> None:
        cleaned = clean_text(data)
        if not cleaned:
            return
        self.text_parts.append(cleaned)
        if self._stack:
            self._stack[-1]["text"] += " " + cleaned

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._stack:
            return
        raw = self._stack.pop()
        title = clean_text(raw["text"])
        href = raw["href"].strip()
        if not title or not href or href.startswith("#"):
            return
        self.links.append(
            LinkCandidate(
                title=title,
                url=urljoin(self.base_url, href),
                text=title,
                published_at=extract_date(title),
            )
        )


def parse_generic_text(html_text: str) -> str:
    parser = LinkTextParser("")
    parser.feed(html.unescape(html_text))
    return clean_text(" ".join(parser.text_parts))


def parse_links(html_text: str, base_url: str) -> list[LinkCandidate]:
    parser = LinkTextParser(base_url)
    parser.feed(html.unescape(html_text))
    return dedupe_links(parser.links)


def parse_metamorphose_news(html_text: str, base_url: str, source: str = "metamorphose") -> list[RadarItem]:
    links = parse_links(html_text, base_url)
    items = []
    for link in links:
        if not is_probable_news_link(link):
            continue
        title = strip_date(link.title)
        if not title:
            continue
        items.append(
            RadarItem(
                source=source,
                title=title,
                url=link.url,
                published_at=link.published_at or extract_date(link.text),
                status=classify_title(title),
                content=link.text,
            )
        )
    return dedupe_items(items)


def is_probable_news_link(link: LinkCandidate) -> bool:
    lowered = link.url.lower()
    title = link.title.lower()
    if any(token in lowered for token in ("/news", "news_", "detail", "info")):
        return True
    return any(
        token in title
        for token in (
            "new arrival",
            "pre-order",
            "preorder",
            "restock",
            "reservation",
            "release",
            "shop news",
        )
    )


def dedupe_links(links: list[LinkCandidate]) -> list[LinkCandidate]:
    seen = set()
    results = []
    for link in links:
        key = (link.url, link.title)
        if key in seen:
            continue
        seen.add(key)
        results.append(link)
    return results


def dedupe_items(items: list[RadarItem]) -> list[RadarItem]:
    seen = set()
    results = []
    for item in items:
        if item.identity_hash in seen:
            continue
        seen.add(item.identity_hash)
        results.append(item)
    return results


def extract_date(text: str) -> str:
    match = DATE_RE.search(text)
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def strip_date(text: str) -> str:
    return clean_text(DATE_RE.sub(" ", text).strip(" -|:："))


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
