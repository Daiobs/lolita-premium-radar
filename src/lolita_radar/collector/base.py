from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import MarketSample, ShopItem


@dataclass(frozen=True)
class CollectorJob:
    name: str
    collector_type: str
    url: str = ""
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectorResult:
    shop_items: list[ShopItem] = field(default_factory=list)
    market_samples: list[MarketSample] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def item_count(self) -> int:
        return len(self.shop_items) + len(self.market_samples)


@dataclass(frozen=True)
class CollectorRun:
    job_name: str
    collector_type: str
    ok: bool
    status: str
    latency_ms: int
    item_count: int
    error_message: str = ""


class BaseCollector(Protocol):
    collector_type: str

    def collect(self, job: CollectorJob) -> CollectorResult:
        ...
