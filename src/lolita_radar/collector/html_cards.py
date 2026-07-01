from __future__ import annotations

from html.parser import HTMLParser
from typing import Any


class CardParser(HTMLParser):
    def __init__(self, class_token: str) -> None:
        super().__init__(convert_charrefs=True)
        self.class_token = class_token
        self.cards: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        classes = set(attr.get("class", "").split())
        if tag in {"article", "li", "div"} and self.class_token in classes:
            self.cards.append({key.removeprefix("data-"): value for key, value in attr.items() if key.startswith("data-")})
