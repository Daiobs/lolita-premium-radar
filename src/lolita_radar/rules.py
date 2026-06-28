from __future__ import annotations

import re

from .models import RadarItem


def keyword_matches(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in keywords if keyword and keyword_matches_text(lowered, keyword)]


def keyword_matches_text(lowered_text: str, keyword: str) -> bool:
    lowered_keyword = keyword.lower().strip()
    if not lowered_keyword:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 _.-]*", lowered_keyword):
        pattern = r"(?<![a-z0-9])" + re.escape(lowered_keyword).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        return re.search(pattern, lowered_text) is not None
    return lowered_keyword in lowered_text


def item_matches_keywords(item: RadarItem, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = " ".join([item.title, item.content, item.url])
    return bool(keyword_matches(haystack, keywords))
