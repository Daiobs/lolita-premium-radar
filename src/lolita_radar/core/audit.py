from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..adapters.generic_page import apply_ignore_patterns, strip_navigation_tokens, suppress_duplicate_segments
from ..crawler import enrich_source_runs
from ..feed import build_home_feed
from ..runner import verify_check_loop
from ..shop import build_drop_signal
from ..storage import connect
from ..trend import build_trend_feed
from ..web import FEED_INDEX_HTML


AUDIT_STATUSES = {"pass", "fail", "missing"}


@dataclass(frozen=True)
class FeedOsAuditCheck:
    name: str
    status: str
    detail: str

    def __post_init__(self) -> None:
        if self.status not in AUDIT_STATUSES:
            raise ValueError(f"unknown audit status: {self.status}")


@dataclass(frozen=True)
class FeedOsAudit:
    checks: tuple[FeedOsAuditCheck, ...]

    @property
    def complete(self) -> bool:
        return all(check.status == "pass" for check in self.checks)

    @property
    def status(self) -> str:
        if self.complete:
            return "complete"
        if any(check.status == "fail" for check in self.checks):
            return "failed"
        return "incomplete"

    def counts(self) -> dict[str, int]:
        return {status: sum(1 for check in self.checks if check.status == status) for status in sorted(AUDIT_STATUSES)}


def audit_feed_os(
    config_path: Path,
    db_path: Path,
    loop_log_path: Path | None = None,
    loop_exit_path: Path | None = None,
    expected_cycles: int = 288,
    project_root: Path | None = None,
) -> FeedOsAudit:
    root = project_root or Path.cwd()
    checks = [
        audit_required_modules(root),
        audit_frontend_feed_os(),
        audit_feed_contract(),
        audit_trend_engine(),
        audit_shop_drop_model(),
        audit_crawler_health_contract(db_path),
        audit_generic_noise_controls(),
        audit_stable_loop_evidence(config_path, db_path, loop_log_path, loop_exit_path, expected_cycles),
    ]
    return FeedOsAudit(tuple(checks))


def audit_required_modules(project_root: Path) -> FeedOsAuditCheck:
    root = project_root / "src" / "lolita_radar"
    required = ("feed", "trend", "shop", "crawler", "core")
    missing = [name for name in required if not (root / name).is_dir()]
    if missing:
        return FeedOsAuditCheck("structure", "fail", "missing product modules: " + ", ".join(missing))
    return FeedOsAuditCheck("structure", "pass", "feed/trend/shop/crawler/core modules exist")


def audit_frontend_feed_os() -> FeedOsAuditCheck:
    required = (
        "feed-card",
        "badge",
        "summary",
        'data-filter="release"',
        'data-filter="drop"',
        'data-filter="trend"',
        'data-filter="alert"',
    )
    missing = [token for token in required if token not in FEED_INDEX_HTML]
    lowered = FEED_INDEX_HTML.lower()
    legacy_tokens = (
        "dash" + "board",
        "north" + "star",
        "north" + " " + "star",
        "mat" + "rix",
        "sa" + "lon",
        "brand" + "crown",
    )
    forbidden = [token for token in legacy_tokens if token in lowered]
    if missing or forbidden:
        detail = []
        if missing:
            detail.append("missing UI tokens: " + ", ".join(missing))
        if forbidden:
            detail.append("legacy product tokens present: " + ", ".join(forbidden))
        return FeedOsAuditCheck("home_feed_ui", "fail", "; ".join(detail))
    return FeedOsAuditCheck("home_feed_ui", "pass", "card UI, badges, summary bar, and 4 filters are present")


def audit_feed_contract() -> FeedOsAuditCheck:
    feed = sample_home_feed()
    streams = feed.get("streams", {})
    expected_streams = {"release", "drop", "trend", "alert"}
    if set(streams) != expected_streams:
        return FeedOsAuditCheck("feed_contract", "fail", f"streams={sorted(streams)}")
    checks = [
        required_keys(streams["release"][0], ("brand", "title", "type", "time", "price", "url")),
        required_keys(streams["drop"][0], ("shop", "item", "keywords", "urgency", "url")),
        required_keys(streams["trend"][0], ("brand", "trend", "confidence", "price_delta", "reason_codes")),
        required_keys(streams["alert"][0], ("feed_type", "kind", "title", "reason_codes", "url")),
    ]
    missing = [item for item in checks if item]
    ordering = [row.get("feed_type") for row in feed.get("all", [])[:4]]
    if missing or ordering != ["release", "drop", "alert", "trend"]:
        detail = []
        if missing:
            detail.append("missing fields: " + "; ".join(missing))
        if ordering != ["release", "drop", "alert", "trend"]:
            detail.append(f"ordering={ordering}")
        return FeedOsAuditCheck("feed_contract", "fail", "; ".join(detail))
    return FeedOsAuditCheck("feed_contract", "pass", "4 streams expose required fields and priority ordering")


def sample_home_feed() -> dict[str, Any]:
    events = [
        {
            "source": "angelic_pretty",
            "event_type": "new_item",
            "status": "new_arrival",
            "title": "Shell Garden JSK",
            "url": "https://example.com/ap/shell",
            "published_at": "2026-06-30",
            "created_at": "2026-06-30T10:00:00+00:00",
            "metadata": {"price": "¥38,280"},
        },
        {
            "source": "generic_page",
            "event_type": "content_changed",
            "status": "shop_news",
            "title": "Proxy JSK 预约",
            "url": "https://example.com/shop",
            "created_at": "2026-06-30T10:01:00+00:00",
            "metadata": {
                "shop": {"name": "Tokyo Proxy", "url": "https://example.com/shop"},
                "item": {"title": "Shell Garden JSK", "url": "https://example.com/shop/shell"},
                "matched_keywords": ["JSK", "预约"],
            },
        },
    ]
    market_summary = {"brands": [{"brand_alias": "AP", "sample_count": 3, "avg_premium_rate": 0.45}]}
    source_runs = [
        {
            "source": "angelic_pretty",
            "status": "failed",
            "ok": False,
            "checked_at": "2026-06-30T10:00:00+00:00",
            "error_rate": 1.0,
            "latency_ms": 1200,
            "item_count": 0,
            "error_message": "timeout",
        }
    ]
    return build_home_feed(
        events,
        [],
        market_summary,
        {"alerts": []},
        [],
        source_runs,
        brand_weights=[{"alias": "AP", "watch_urls": [{"label": "market", "url": "https://example.com/market/ap"}]}],
        source_urls={"angelic_pretty": "https://example.com/ap"},
    )


def required_keys(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    missing = [key for key in keys if key not in row or row[key] in ("", None)]
    return ", ".join(missing)


def audit_trend_engine() -> FeedOsAuditCheck:
    trends = build_trend_feed(
        {"brands": [{"brand_alias": "AP", "sample_count": 4, "avg_premium_rate": 0.5}]},
        [{"brand_alias": "AP", "direction": "rising", "observed_at": "2026-06-30"}],
        [{"source": "angelic_pretty", "status": "new_arrival"}],
    )
    if not trends:
        return FeedOsAuditCheck("trend_engine", "fail", "no trend cards produced")
    trend = trends[0]
    confidence = int(trend.get("confidence") or -1)
    if trend.get("trend") not in {"rising", "cooling", "stable"} or not (0 <= confidence <= 100):
        return FeedOsAuditCheck("trend_engine", "fail", f"invalid trend output: {trend}")
    if not trend.get("reason_codes"):
        return FeedOsAuditCheck("trend_engine", "fail", "missing reason_codes")
    return FeedOsAuditCheck("trend_engine", "pass", "rule-based rising/cooling/stable output with confidence and reasons")


def audit_shop_drop_model() -> FeedOsAuditCheck:
    signal = build_drop_signal(
        {
            "source": "generic_page",
            "event_type": "new_item",
            "status": "shop_news",
            "title": "Proxy page",
            "url": "https://example.com/shop",
            "metadata": {
                "shop": {"name": "Tokyo Proxy", "url": "https://example.com/shop"},
                "item": {"title": "Shell Garden JSK", "url": "https://example.com/shop/shell"},
                "matched_keywords": ["JSK", "预约"],
            },
        }
    )
    if signal is None:
        return FeedOsAuditCheck("shop_drop_model", "fail", "generic_page new item did not produce DROP")
    if signal.shop.name != "Tokyo Proxy" or signal.item.title != "Shell Garden JSK":
        return FeedOsAuditCheck("shop_drop_model", "fail", "shop/item mapping is incorrect")
    if signal.urgency != "high" or "keyword_match" not in signal.reason_codes:
        return FeedOsAuditCheck("shop_drop_model", "fail", f"unexpected urgency/reasons: {signal}")
    return FeedOsAuditCheck("shop_drop_model", "pass", "Shop -> Item DROP triggers on new item and watched keywords")


def audit_crawler_health_contract(db_path: Path) -> FeedOsAuditCheck:
    missing_columns = source_run_missing_columns(db_path)
    if missing_columns:
        return FeedOsAuditCheck("crawler_health", "fail", "source_runs missing columns: " + ", ".join(missing_columns))
    rows = enrich_source_runs(
        [
            {"source": "ok", "ok": True, "item_count": 1, "latency_ms": 20, "error_rate": 0.0},
            {"source": "empty", "ok": True, "item_count": 0, "latency_ms": 20, "error_rate": 0.0},
            {"source": "bad", "ok": False, "item_count": 0, "latency_ms": 20, "error_rate": 1.0},
        ]
    )
    statuses = {str(row["source"]): str(row["status"]) for row in rows}
    if statuses != {"ok": "ok", "empty": "degraded", "bad": "failed"}:
        return FeedOsAuditCheck("crawler_health", "fail", f"unexpected statuses: {statuses}")
    return FeedOsAuditCheck("crawler_health", "pass", "source_runs supports ok/degraded/failed with error_rate, latency, item_count")


def source_run_missing_columns(db_path: Path) -> list[str]:
    required = {"source", "checked_at", "ok", "status", "error_rate", "latency_ms", "item_count", "event_count", "error_message"}
    connection = connect(db_path)
    try:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(source_runs)").fetchall()}
    finally:
        connection.close()
    return sorted(required - columns)


def audit_generic_noise_controls() -> FeedOsAuditCheck:
    text = (
        "Login Cart updated at: 2026-06-30 10:00 view count: 5 "
        "JSK Shell Garden JSK. JSK Shell Garden JSK. カート ログイン 新作ジャンパースカート"
    )
    cleaned = suppress_duplicate_segments(strip_navigation_tokens(apply_ignore_patterns(text, [
        r"updated at[:：]?\s*[0-9: /.-]+",
        r"\b(?:view count|views|page views)[:：]?\s*[\d,]+",
        r"\b(login|account|cart|privacy|contact|company|shop list)\b",
    ])))
    lowered = cleaned.lower()
    cleaned_tokens = {token.strip(" |/\\-_:：[]()（）・,，.。!！?？").casefold() for token in cleaned.split()}
    blocked = []
    for token in ("updated at", "view count"):
        if token in lowered:
            blocked.append(token)
    for token in ("login", "cart", "ログイン", "カート"):
        if token.casefold() in cleaned_tokens:
            blocked.append(token)
    if blocked:
        return FeedOsAuditCheck("generic_noise_control", "fail", "noise tokens survived: " + ", ".join(blocked))
    if "新作ジャンパースカート" not in cleaned:
        return FeedOsAuditCheck("generic_noise_control", "fail", "navigation filter stripped jumper skirt content")
    return FeedOsAuditCheck("generic_noise_control", "pass", "timestamp/view-count/navigation noise is ignored without stripping item text")


def audit_stable_loop_evidence(
    config_path: Path,
    db_path: Path,
    loop_log_path: Path | None,
    loop_exit_path: Path | None,
    expected_cycles: int,
) -> FeedOsAuditCheck:
    if loop_log_path is None:
        return FeedOsAuditCheck(
            "stable_loop_evidence",
            "missing",
            "provide --loop-log and --loop-exit-file after run-loop to prove crawler stability",
        )
    verification = verify_check_loop(
        config_path=config_path,
        db_path=db_path,
        log_path=loop_log_path,
        expected_cycles=expected_cycles,
        exit_path=loop_exit_path,
    )
    if verification.complete:
        return FeedOsAuditCheck(
            "stable_loop_evidence",
            "pass",
            f"verify-loop complete for {verification.observed_cycles}/{verification.expected_cycles} cycles",
        )
    status = "fail" if verification.status == "failed" else "missing"
    return FeedOsAuditCheck(
        "stable_loop_evidence",
        status,
        (
            f"verify-loop {verification.status}: observed={verification.observed_cycles}/"
            f"{verification.expected_cycles}, missing={list(verification.missing_cycles)}, "
            f"failed={list(verification.failed_cycles)}, unhealthy={verification.unhealthy_source_runs}"
        ),
    )
