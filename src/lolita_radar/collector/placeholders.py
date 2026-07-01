from __future__ import annotations

from .base import CollectorJob, CollectorResult


class DisabledPlaceholderCollector:
    collector_type = "placeholder"

    def collect(self, job: CollectorJob) -> CollectorResult:
        return CollectorResult(warnings=[f"{job.collector_type} is a disabled placeholder"])


class MercariMarketCollector(DisabledPlaceholderCollector):
    collector_type = "mercari_market"


class YahooAuctionMarketCollector(DisabledPlaceholderCollector):
    collector_type = "yahoo_auction_market"


class LaceMarketCollector(DisabledPlaceholderCollector):
    collector_type = "lace_market"


class WunderweltMarketCollector(DisabledPlaceholderCollector):
    collector_type = "wunderwelt_market"


class ClosetChildMarketCollector(DisabledPlaceholderCollector):
    collector_type = "closet_child_market"


class TaobaoPublicShopCollector(DisabledPlaceholderCollector):
    collector_type = "taobao_public_shop"


class GoofishMarketCollector(DisabledPlaceholderCollector):
    collector_type = "goofish_market"
