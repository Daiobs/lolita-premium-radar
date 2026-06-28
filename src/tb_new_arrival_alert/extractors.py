import html
import re
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from .models import Item


ITEM_ID_RE = re.compile(r"(?:[?&](?:id|item_id|item_num_id)=)(\d{5,})")
PRICE_RE = re.compile(
    r"(?:[¥￥]\s*|RMB\s*|CNY\s*)(\d{1,6}(?:\.\d{1,2})?)"
    r"|(\d{1,6}(?:\.\d{1,2})?)\s*(?:元|RMB|CNY)",
    re.IGNORECASE,
)
PLAIN_PRICE_RE = re.compile(r"(?<!\d)(\d{2,6}(?:\.\d{1,2})?)(?!\d)")


class ItemLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._anchor_stack: List[Dict[str, str]] = []
        self.links: List[Dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "a":
            self._anchor_stack.append(
                {
                    "href": attr.get("href", ""),
                    "text": attr.get("title", "") or attr.get("aria-label", ""),
                }
            )
        elif tag.lower() == "img" and self._anchor_stack:
            alt = attr.get("alt", "") or attr.get("title", "")
            if alt:
                self._anchor_stack[-1]["text"] += " " + alt

    def handle_data(self, data: str) -> None:
        if self._anchor_stack:
            self._anchor_stack[-1]["text"] += " " + data

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._anchor_stack:
            return
        link = self._anchor_stack.pop()
        href = normalize_url(link.get("href", ""), self.base_url)
        item_id = extract_item_id(href)
        if not item_id:
            return
        self.links.append(
            {
                "item_id": item_id,
                "url": canonical_item_url(href),
                "title": clean_text(link.get("text", "")),
            }
        )


def extract_items(html_text: str, base_url: str) -> List[Item]:
    parser = ItemLinkParser(base_url)
    parser.feed(html.unescape(html_text))

    items_by_id: Dict[str, Item] = {}
    for link in parser.links:
        title = link["title"] or f"Taobao item {link['item_id']}"
        source_text = f"{title} {link['url']}"
        item = Item(
            item_id=link["item_id"],
            title=title,
            url=link["url"],
            price=extract_price(title),
            source_text=source_text,
        )
        items_by_id[item.item_id] = item
    return list(items_by_id.values())


def extract_item_id(url: str) -> Optional[str]:
    match = ITEM_ID_RE.search(url)
    if match:
        return match.group(1)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("id", "item_id", "item_num_id"):
        values = query.get(key)
        if values and values[0].isdigit():
            return values[0]
    return None


def canonical_item_url(url: str) -> str:
    parsed = urlparse(url)
    item_id = extract_item_id(url)
    if not item_id:
        return url
    host = parsed.netloc or "item.taobao.com"
    if "tmall" in host:
        path = "/item.htm"
        host = "detail.tmall.com"
    else:
        path = "/item.htm"
        host = "item.taobao.com"
    query = urlencode({"id": item_id})
    return urlunparse((parsed.scheme or "https", host, path, "", query, ""))


def normalize_url(href: str, base_url: str) -> str:
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    return urljoin(base_url, href)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def extract_price(text: str) -> Optional[float]:
    for match in PRICE_RE.finditer(text):
        raw = match.group(1) or match.group(2)
        value = float(raw)
        if 0 < value < 1_000_000:
            return value

    for match in PLAIN_PRICE_RE.finditer(text):
        value = float(match.group(1))
        if 0 < value < 1_000_000:
            return value

    return None


def item_keys(items: Iterable[Item]) -> set[str]:
    return {item.item_id for item in items}
