from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..models import RadarItem


@dataclass(frozen=True)
class SourceConfig:
    name: str
    type: str
    url: str
    enabled: bool = True
    keywords: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(ABC):
    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    @abstractmethod
    def fetch_items(self) -> list[RadarItem]:
        raise NotImplementedError
