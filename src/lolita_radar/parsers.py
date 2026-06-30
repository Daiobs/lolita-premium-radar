from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

from .models import RadarItem, classify_title


DATE_RE = re.compile(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})")
COMPACT_DATE_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
IMAGE_TITLE_RE = re.compile(r"(?:^/|%2f|\.(?:jpe?g|png|webp|gif))(?:.*の画像)?", re.IGNORECASE)
PRICE_RE = re.compile(r"(?:[¥￥]\s?[\d,]+|[\d,]+\s?円)")
NAVIGATION_TOKENS = (
    "login",
    "account",
    "cart",
    "privacy",
    "contact",
    "company",
    "shop list",
    "店舗",
    "マイページ",
    "/cart",
    "/category/",
    "/area/",
    "/shop/",
    "/shoplist",
)


@dataclass(frozen=True)
class LinkCandidate:
    title: str
    url: str
    text: str
    published_at: str = ""
    context: str = ""


class LinkTextParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._stack: list[dict[str, str]] = []
        self._containers: list[dict[str, object]] = []
        self.links: list[LinkCandidate] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered_tag = tag.lower()
        attr = {key.lower(): value or "" for key, value in attrs}
        if lowered_tag in {"li", "article", "section", "div", "tr", "p"}:
            self._containers.append({"tag": lowered_tag, "text_parts": [], "link_indices": []})
        if lowered_tag in {"time", "meta"}:
            dated_attr = attr.get("datetime") or attr.get("content") or attr.get("date")
            if dated_attr and extract_date(dated_attr):
                self._append_text(dated_attr)
        if lowered_tag == "a":
            self._stack.append(
                {
                    "href": attr.get("href", ""),
                    "text": attr.get("title", "") or attr.get("aria-label", ""),
                }
            )
        if lowered_tag == "img" and self._stack:
            image_text = clean_text(attr.get("alt", "") or attr.get("title", "") or attr.get("aria-label", ""))
            if image_text:
                self._stack[-1]["text"] += " " + image_text

    def handle_data(self, data: str) -> None:
        cleaned = clean_text(data)
        if not cleaned:
            return
        self._append_text(cleaned)

    def _append_text(self, cleaned: str) -> None:
        self.text_parts.append(cleaned)
        for container in self._containers:
            text_parts = container["text_parts"]
            if isinstance(text_parts, list):
                text_parts.append(cleaned)
        if self._stack:
            self._stack[-1]["text"] += " " + cleaned

    def handle_endtag(self, tag: str) -> None:
        lowered_tag = tag.lower()
        if lowered_tag == "a" and self._stack:
            raw = self._stack.pop()
            title = clean_text(raw["text"])
            href = raw["href"].strip()
            if title and href and not href.startswith("#"):
                index = len(self.links)
                self.links.append(
                    LinkCandidate(
                        title=title,
                        url=urljoin(self.base_url, href),
                        text=title,
                        published_at=extract_date(f"{title} {href}"),
                    )
                )
                for container in self._containers:
                    link_indices = container["link_indices"]
                    if isinstance(link_indices, list):
                        link_indices.append(index)
            return
        if lowered_tag in {"li", "article", "section", "div", "tr", "p"}:
            for index in range(len(self._containers) - 1, -1, -1):
                container = self._containers[index]
                if container.get("tag") != lowered_tag:
                    continue
                self._containers.pop(index)
                context = clean_text(" ".join(str(part) for part in container.get("text_parts", [])))
                for link_index in container.get("link_indices", []):
                    if not isinstance(link_index, int) or link_index >= len(self.links):
                        continue
                    link = self.links[link_index]
                    link_count = len(container.get("link_indices", []))
                    if not should_replace_context(link.context, context, link_count):
                        continue
                    context_date = extract_date(context)
                    published_at = context_date if context_date and link_count == 1 else link.published_at or context_date
                    full_text = clean_text(f"{context} {link.title}")
                    self.links[link_index] = LinkCandidate(
                        title=link.title,
                        url=link.url,
                        text=full_text,
                        published_at=published_at,
                        context=context,
                    )
                return


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


def parse_angelic_pretty_news(html_text: str, base_url: str, source: str = "angelic_pretty") -> list[RadarItem]:
    items = []
    for link in parse_links(html_text, base_url):
        if is_navigation_link(link):
            continue
        category = angelic_pretty_category(link)
        if not category:
            continue
        title = angelic_pretty_title(link, category)
        if not title:
            continue
        items.append(
            RadarItem(
                source=source,
                title=title,
                url=link.url,
                published_at=link.published_at or extract_date(link.text),
                status=classify_title(f"{category} {title} {link.text}"),
                content=link.text,
                metadata=brand_metadata("Angelic Pretty", source, category, link),
            )
        )
    return dedupe_items(items)


def parse_baby_ssb_news(html_text: str, base_url: str, source: str = "baby_ssb") -> list[RadarItem]:
    items = []
    base_key = base_url.rstrip("/")
    for link in parse_links(html_text, base_url):
        if is_navigation_link(link) or link.url.rstrip("/") == base_key or re.fullmatch(r"\d+", link.title.strip()):
            continue
        category = baby_category(link)
        release_like = is_probable_brand_release_link(link)
        if not category and not release_like:
            continue
        if category == "news" and not link.published_at and not release_like:
            continue
        title = strip_date(link.title)
        if not title:
            continue
        brand = "ALICE and the PIRATES" if is_aatp_link(link) else "BABY, THE STARS SHINE BRIGHT"
        items.append(
            RadarItem(
                source=source,
                title=title,
                url=link.url,
                published_at=link.published_at or extract_date(link.text),
                status=classify_title(f"{category} {title} {link.text}"),
                content=link.text,
                metadata=brand_metadata(brand, source, category or "news", link),
            )
        )
    return dedupe_items(items)


def parse_alice_and_the_pirates_news(
    html_text: str,
    base_url: str,
    source: str = "alice_and_the_pirates",
) -> list[RadarItem]:
    items = []
    for link in parse_links(html_text, base_url):
        if is_navigation_link(link) or not is_aatp_link(link):
            continue
        category = baby_category(link) or "news"
        title = strip_date(link.title)
        if not title:
            continue
        items.append(
            RadarItem(
                source=source,
                title=title,
                url=link.url,
                published_at=link.published_at or extract_date(link.text),
                status=classify_title(f"{category} {title} {link.text}"),
                content=link.text,
                metadata=brand_metadata("ALICE and the PIRATES", source, category, link),
            )
        )
    return dedupe_items(items)


def parse_moitie_news(html_text: str, base_url: str, source: str = "moitie") -> list[RadarItem]:
    items = []
    for link in parse_links(html_text, base_url):
        if is_navigation_link(link):
            continue
        category = moitie_category(link)
        if not category and not is_probable_brand_release_link(link):
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
                status=classify_title(f"{category} {title} {link.text}"),
                content=link.text,
                metadata=brand_metadata("Moi-meme-Moitie", source, category or "news", link),
            )
        )
    return dedupe_items(items)


def parse_innocent_world_news(html_text: str, base_url: str, source: str = "innocent_world") -> list[RadarItem]:
    return parse_brand_news(html_text, base_url, source=source, brand="Innocent World")


def parse_brand_news(html_text: str, base_url: str, source: str, brand: str) -> list[RadarItem]:
    links = parse_links(html_text, base_url)
    items = []
    for link in links:
        title = strip_date(link.title)
        if not title or not is_probable_brand_release_link(link):
            continue
        items.append(
            RadarItem(
                source=source,
                title=title,
                url=link.url,
                published_at=link.published_at or extract_date(link.text),
                status=classify_title(title + " " + link.text),
                content=link.text,
                metadata={"brand": brand, "parser": source},
            )
        )
    return dedupe_items(items)


def is_probable_brand_release_link(link: LinkCandidate) -> bool:
    lowered = f"{link.title} {link.url}".lower()
    if any(token in lowered for token in NAVIGATION_TOKENS):
        return False
    return any(
        token in lowered
        for token in (
            "news",
            "new arrival",
            "new item",
            "new release",
            "release",
            "pre-order",
            "preorder",
            "reservation",
            "restock",
            "再入荷",
            "再販",
            "再贩",
            "予約",
            "受注",
            "ご予約",
            "新作",
            "入荷",
            "販売開始",
        )
    )


def is_navigation_link(link: LinkCandidate) -> bool:
    lowered = f"{link.title} {link.url}".lower()
    if any(token in lowered for token in NAVIGATION_TOKENS):
        return True
    normalized_title = clean_text(link.title).lower()
    return normalized_title in {"cart", "shopping cart", "カート", "ショッピングカート"}


def angelic_pretty_category(link: LinkCandidate) -> str:
    lowered = f"{link.title} {link.context} {link.url}".lower()
    categories = (
        ("preorder", ("pre order", "pre-order", "preorder", "予約", "受注", "ご予約")),
        ("restock", ("restock", "再入荷", "再販")),
        ("new_arrival", ("new arrival", "newitem", "new item", "new release", "販売開始", "新作", "入荷")),
        ("topics", ("topics", "topic", "news")),
    )
    for category, tokens in categories:
        if any(token in lowered for token in tokens):
            return category
    return ""


def angelic_pretty_title(link: LinkCandidate, category: str) -> str:
    title = strip_date(link.title)
    if title and not IMAGE_TITLE_RE.search(title):
        return title
    date = link.published_at or extract_date(link.text) or extract_date(link.url)
    category_label = {
        "preorder": "予約特集 / 预约特集",
        "restock": "再入荷 / 再贩",
        "new_arrival": "新作入荷 / 新作到货",
        "topics": "お知らせ / 通知",
    }.get(category, "Feature")
    parts = ["Angelic Pretty"]
    if date:
        parts.append(date)
    parts.append(category_label)
    return " ".join(parts)


def baby_category(link: LinkCandidate) -> str:
    lowered = f"{link.title} {link.context} {link.url}".lower()
    categories = (
        ("preorder", ("pre order", "pre-order", "preorder", "reservation", "予約", "受注", "ご予約")),
        ("restock", ("restock", "再入荷", "再販")),
        ("new_arrival", ("new arrival", "new item", "new release", "新作", "入荷", "販売開始")),
        ("event", ("event", "fair", "イベント")),
        ("news", ("news", "お知らせ")),
    )
    for category, tokens in categories:
        if any(token in lowered for token in tokens):
            return category
    return ""


def moitie_category(link: LinkCandidate) -> str:
    lowered = f"{link.title} {link.context} {link.url}".lower()
    categories = (
        ("sale", ("sale", "セール")),
        ("event", ("event", "イベント")),
        ("preorder", ("pre order", "pre-order", "preorder", "予約", "受注", "ご予約")),
        ("restock", ("restock", "再入荷", "再販")),
        ("new_arrival", ("new item", "new arrival", "新作", "入荷", "販売開始")),
        ("news", ("news", "information", "お知らせ", "blogs/news")),
    )
    for category, tokens in categories:
        if any(token in lowered for token in tokens):
            return category
    return ""


def is_aatp_link(link: LinkCandidate) -> bool:
    lowered = f"{link.title} {link.context} {link.url}".lower()
    return any(token in lowered for token in ("alice and the pirates", "aatp", "pirates", "パイレーツ"))


def brand_metadata(brand: str, source: str, category: str, link: LinkCandidate) -> dict[str, str]:
    price = extract_price(link.text)
    metadata = {
        "brand": brand,
        "parser": source,
        "category": category,
        "section": infer_section(link),
    }
    if price:
        metadata["price"] = price
    return metadata


def infer_section(link: LinkCandidate) -> str:
    lowered = f"{link.title} {link.context} {link.url}".lower()
    sections = (
        ("dress", ("jsk", "onepiece", "one-piece", "op", "dress", "ジャンパースカート", "ワンピース")),
        ("blouse", ("blouse", "ブラウス")),
        ("headwear", ("head bow", "headbow", "カチューシャ", "ヘッドドレス")),
        ("accessory", ("accessory", "アクセサリー", "pochette", "bag", "バッグ")),
        ("outer", ("coat", "jacket", "ケープ", "コート")),
    )
    for section, tokens in sections:
        if any(token in lowered for token in tokens):
            return section
    return "news"


def extract_price(text: str) -> str:
    match = PRICE_RE.search(text)
    return match.group(0).strip() if match else ""


def is_probable_news_link(link: LinkCandidate) -> bool:
    lowered = link.url.lower()
    title = link.title.lower()
    if any(token in lowered for token in ("/news?page", "?page=")):
        return False
    if any(token in title for token in ("current page", "go to page", "last page", "latest information")):
        return False
    if "/metamornews/" in lowered:
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


def should_replace_context(current: str, candidate: str, link_count: int) -> bool:
    if not candidate:
        return False
    if not current:
        return True
    current_date = bool(extract_date(current))
    candidate_date = bool(extract_date(candidate))
    if not current_date and candidate_date:
        return True
    if link_count == 1 and context_score(candidate) > context_score(current):
        return True
    return False


def context_score(text: str) -> int:
    lowered = text.lower()
    score = 0
    if extract_date(text):
        score += 20
    if extract_price(text):
        score += 5
    if any(token in lowered for token in ("new arrival", "new item", "preorder", "pre-order", "restock")):
        score += 4
    if any(token in lowered for token in ("新作", "入荷", "予約", "ご予約", "再入荷", "再販")):
        score += 4
    return score


def extract_date(text: str) -> str:
    match = DATE_RE.search(text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return normalized_date(year, month, day)
    compact_match = COMPACT_DATE_RE.search(text)
    if compact_match:
        year, month, day = (int(part) for part in compact_match.groups())
        return normalized_date(year, month, day)
    return ""


def normalized_date(year: int, month: int, day: int) -> str:
    if month < 1 or month > 12 or day < 1 or day > 31:
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def strip_date(text: str) -> str:
    return clean_text(DATE_RE.sub(" ", text).strip(" -|:："))


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
