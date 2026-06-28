from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Item:
    item_id: str
    title: str
    url: str
    price: Optional[float] = None
    source_text: str = ""


@dataclass(frozen=True)
class Target:
    name: str
    url: str
    enabled: bool
    include_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    price_min: Optional[float]
    price_max: Optional[float]

