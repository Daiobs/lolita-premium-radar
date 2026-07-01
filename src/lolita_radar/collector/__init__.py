from __future__ import annotations

from .base import BaseCollector, CollectorJob, CollectorResult, CollectorRun
from .closet_child import ClosetChildMarketCollector
from .defaults import DEFAULT_COLLECTOR_JOBS
from .official_shop import OfficialShopCollector
from .market import FixtureMarketCollector
from .placeholders import (
    GoofishMarketCollector,
    LaceMarketCollector,
    MercariMarketCollector,
    TaobaoPublicShopCollector,
    WunderweltMarketCollector,
    YahooAuctionMarketCollector,
)
from .runner import collector_for_type, run_collector_job

__all__ = [
    "BaseCollector",
    "CollectorJob",
    "CollectorResult",
    "CollectorRun",
    "OfficialShopCollector",
    "FixtureMarketCollector",
    "DEFAULT_COLLECTOR_JOBS",
    "MercariMarketCollector",
    "YahooAuctionMarketCollector",
    "LaceMarketCollector",
    "WunderweltMarketCollector",
    "ClosetChildMarketCollector",
    "TaobaoPublicShopCollector",
    "GoofishMarketCollector",
    "run_collector_job",
    "collector_for_type",
]
