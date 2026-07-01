from __future__ import annotations

from time import monotonic

from ..storage import diff_and_store_shop_items, insert_market_samples, record_collector_run
from .base import BaseCollector, CollectorJob, CollectorRun
from .closet_child import ClosetChildMarketCollector
from .market import FixtureMarketCollector
from .official_shop import OfficialShopCollector
from .placeholders import (
    GoofishMarketCollector,
    LaceMarketCollector,
    MercariMarketCollector,
    TaobaoPublicShopCollector,
    WunderweltMarketCollector,
    YahooAuctionMarketCollector,
)


def run_collector_job(connection, job: CollectorJob, collector: BaseCollector) -> CollectorRun:
    started = monotonic()
    try:
        result = collector.collect(job)
        if result.warnings and result.item_count == 0:
            status = "degraded"
            ok = True
            error_message = "; ".join(result.warnings)
        else:
            status = "ok"
            ok = True
            error_message = "; ".join(result.warnings)
        shop_events = diff_and_store_shop_items(connection, result.shop_items)
        market_count = insert_market_samples(connection, result.market_samples) if result.market_samples else 0
        item_count = len(result.shop_items) + market_count
    except Exception as exc:
        ok = False
        status = "failed"
        item_count = 0
        error_message = str(exc)
    latency_ms = round((monotonic() - started) * 1000)
    record_collector_run(
        connection,
        job_name=job.name,
        collector_type=job.collector_type,
        ok=ok,
        status=status,
        latency_ms=latency_ms,
        item_count=item_count,
        error_message=error_message,
    )
    return CollectorRun(
        job_name=job.name,
        collector_type=job.collector_type,
        ok=ok,
        status=status,
        latency_ms=latency_ms,
        item_count=item_count,
        error_message=error_message,
    )


def collector_for_type(collector_type: str) -> BaseCollector:
    collectors: dict[str, BaseCollector] = {
        OfficialShopCollector.collector_type: OfficialShopCollector(),
        FixtureMarketCollector.collector_type: FixtureMarketCollector(),
        MercariMarketCollector.collector_type: MercariMarketCollector(),
        YahooAuctionMarketCollector.collector_type: YahooAuctionMarketCollector(),
        LaceMarketCollector.collector_type: LaceMarketCollector(),
        WunderweltMarketCollector.collector_type: WunderweltMarketCollector(),
        ClosetChildMarketCollector.collector_type: ClosetChildMarketCollector(),
        TaobaoPublicShopCollector.collector_type: TaobaoPublicShopCollector(),
        GoofishMarketCollector.collector_type: GoofishMarketCollector(),
    }
    if collector_type not in collectors:
        raise ValueError(f"unknown collector type: {collector_type}")
    return collectors[collector_type]
