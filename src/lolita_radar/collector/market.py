from __future__ import annotations

from ..models import MarketSample, utc_now_iso
from .base import CollectorJob, CollectorResult
from .html_cards import CardParser
from .official_shop import load_html


class FixtureMarketCollector:
    collector_type = "fixture_market"

    def collect(self, job: CollectorJob) -> CollectorResult:
        html = load_html(job.url)
        parser = CardParser("market-card")
        parser.feed(html)
        observed_at = str(job.options.get("observed_at") or utc_now_iso()[:10])
        samples = []
        for card in parser.cards:
            price = float_value(card.get("asking-price"))
            if price <= 0:
                continue
            samples.append(
                MarketSample(
                    platform=str(card.get("platform") or job.options.get("platform") or "fixture_market"),
                    brand_alias=str(card.get("brand") or card.get("brand-alias") or job.options.get("brand_alias") or "").upper(),
                    pattern=str(card.get("pattern") or job.options.get("pattern") or ""),
                    title=str(card.get("title") or ""),
                    asking_price=price,
                    currency=str(card.get("currency") or job.options.get("currency") or "JPY"),
                    condition=str(card.get("condition") or ""),
                    url=str(card.get("url") or ""),
                    image_url=str(card.get("image") or ""),
                    observed_at=str(card.get("observed-at") or observed_at),
                )
            )
        return CollectorResult(market_samples=[sample for sample in samples if sample.brand_alias and sample.pattern])


def float_value(value: object) -> float:
    try:
        return float(str(value or "").replace(",", ""))
    except ValueError:
        return 0.0
