from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from ..adapters.generic_page import apply_ignore_patterns, strip_navigation_tokens, suppress_duplicate_segments
from ..crawler import enrich_source_runs
from ..feed import build_home_feed
from ..runner import verify_check_loop
from ..shop import build_drop_signal
from ..storage import connect
from ..trend import build_trend_feed
from ..web import FEED_INDEX_HTML, get_feed_state


AUDIT_STATUSES = {"pass", "fail", "missing"}


@dataclass(frozen=True)
class FeedOsAuditCheck:
    name: str
    status: str
    detail: str

    def __post_init__(self) -> None:
        if self.status not in AUDIT_STATUSES:
            raise ValueError(f"unknown audit status: {self.status}")

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "complete": self.complete,
            "counts": self.counts(),
            "checks": [check.to_dict() for check in self.checks],
        }


def audit_feed_os(
    config_path: Path,
    db_path: Path,
    brands_path: Path | None = None,
    market_path: Path | None = None,
    loop_log_path: Path | None = None,
    loop_exit_path: Path | None = None,
    expected_cycles: int = 288,
    min_duration_seconds: int = 24 * 60 * 60,
    project_root: Path | None = None,
) -> FeedOsAudit:
    root = project_root or Path.cwd()
    checks = [
        audit_required_modules(root),
        audit_product_constraints(root),
        audit_frontend_feed_os(),
        audit_feed_contract(),
        audit_runtime_feed_state(config_path, db_path, brands_path, market_path),
        audit_trend_engine(),
        audit_shop_drop_model(),
        audit_crawler_health_contract(db_path),
        audit_generic_noise_controls(),
        audit_stable_loop_evidence(
            config_path,
            db_path,
            loop_log_path,
            loop_exit_path,
            expected_cycles,
            min_duration_seconds,
        ),
    ]
    return FeedOsAudit(tuple(checks))


def audit_required_modules(project_root: Path) -> FeedOsAuditCheck:
    root = project_root / "src" / "lolita_radar"
    required = ("feed", "trend", "shop", "crawler", "core")
    missing = [name for name in required if not (root / name).is_dir()]
    if missing:
        return FeedOsAuditCheck("structure", "fail", "missing product modules: " + ", ".join(missing))
    return FeedOsAuditCheck("structure", "pass", "feed/trend/shop/crawler/core modules exist")


def audit_product_constraints(project_root: Path) -> FeedOsAuditCheck:
    findings = forbidden_product_findings(project_root)
    if findings:
        return FeedOsAuditCheck("product_constraints", "fail", "forbidden product direction found: " + findings[0])
    return FeedOsAuditCheck("product_constraints", "pass", "no blocked product-direction or purchase-automation tokens found")


def forbidden_product_findings(project_root: Path) -> list[str]:
    roots = [
        project_root / "src" / "lolita_radar",
        project_root / "pyproject.toml",
    ]
    tokens = forbidden_product_tokens()
    findings = []
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*.py")) if root.exists() else []
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="ignore").casefold()
            for token in tokens:
                if token.casefold() in text:
                    findings.append(f"{path.relative_to(project_root)} contains {token}")
    return findings


def forbidden_product_tokens() -> tuple[str, ...]:
    return (
        "dash" + "board",
        "north" + "star",
        "north" + " " + "star",
        "brand" + "crown",
        "mat" + "rix",
        "sa" + "lon",
        "cap" + "tcha",
        "checkout" + "_submit",
        "payment" + "_submit",
        "open" + "ai",
        "anth" + "ropic",
        "tensor" + "flow",
        "scikit" + "-learn",
        "sk" + "learn",
        "selen" + "ium",
        "play" + "wright",
        "pupp" + "eteer",
    )


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


def audit_runtime_feed_state(
    config_path: Path,
    db_path: Path,
    brands_path: Path | None = None,
    market_path: Path | None = None,
) -> FeedOsAuditCheck:
    try:
        state = get_feed_state(config_path=config_path, db_path=db_path, brands_path=brands_path, market_path=market_path)
    except Exception as exc:
        return FeedOsAuditCheck("runtime_feed_state", "fail", f"get_feed_state failed: {exc}")
    feed = state.get("feed")
    if not isinstance(feed, dict):
        return FeedOsAuditCheck("runtime_feed_state", "fail", "state.feed is missing")
    streams = feed.get("streams")
    if not isinstance(streams, dict):
        return FeedOsAuditCheck("runtime_feed_state", "fail", "state.feed.streams is missing")
    expected_streams = ("release", "drop", "trend", "alert")
    missing_streams = [name for name in expected_streams if name not in streams]
    if missing_streams:
        return FeedOsAuditCheck("runtime_feed_state", "fail", "missing streams: " + ", ".join(missing_streams))
    summary = feed.get("summary")
    if not isinstance(summary, dict):
        return FeedOsAuditCheck("runtime_feed_state", "fail", "state.feed.summary is missing")
    missing_summary = [name for name in ("drops", "shops", "trends", "alerts") if name not in summary]
    if missing_summary:
        return FeedOsAuditCheck("runtime_feed_state", "fail", "missing summary fields: " + ", ".join(missing_summary))
    all_rows = feed.get("all")
    if not isinstance(all_rows, list):
        return FeedOsAuditCheck("runtime_feed_state", "fail", "state.feed.all is not a list")
    if len(all_rows) > 30:
        return FeedOsAuditCheck("runtime_feed_state", "fail", f"state.feed.all has {len(all_rows)} rows")
    ordering_problem = feed_ordering_problem(all_rows)
    if ordering_problem:
        return FeedOsAuditCheck("runtime_feed_state", "fail", ordering_problem)
    field_problem = runtime_feed_field_problem(streams)
    if field_problem:
        return FeedOsAuditCheck("runtime_feed_state", "fail", field_problem)
    value_problem = runtime_feed_value_problem(streams)
    if value_problem:
        return FeedOsAuditCheck("runtime_feed_state", "fail", value_problem)
    noise_problem = runtime_feed_noise_problem(streams)
    if noise_problem:
        return FeedOsAuditCheck("runtime_feed_state", "fail", noise_problem)
    counts = ", ".join(f"{name}={len(streams.get(name, []))}" for name in expected_streams)
    return FeedOsAuditCheck("runtime_feed_state", "pass", f"current config/db builds Feed OS streams ({counts})")


def feed_ordering_problem(rows: list[dict[str, Any]]) -> str:
    priority = {"release": 0, "drop": 1, "alert": 2, "trend": 3}
    previous = -1
    for row in rows:
        feed_type = str(row.get("feed_type") or "")
        current = priority.get(feed_type)
        if current is None:
            return f"unknown feed_type in state.feed.all: {feed_type}"
        if current < previous:
            return "state.feed.all violates RELEASE > DROP > ALERT > TREND ordering"
        previous = current
        if not row.get("url"):
            return "state.feed.all contains a row without url"
    return ""


def runtime_feed_field_problem(streams: dict[str, Any]) -> str:
    required_by_stream = {
        "release": ("brand", "title", "type", "time", "price", "url"),
        "drop": ("shop", "item", "keywords", "urgency", "url"),
        "trend": ("brand", "trend", "confidence", "price_delta", "reason_codes"),
        "alert": ("feed_type", "kind", "title", "reason_codes", "url"),
    }
    for name, required in required_by_stream.items():
        rows = streams.get(name, [])
        if not isinstance(rows, list):
            return f"stream {name} is not a list"
        for row in rows:
            if not isinstance(row, dict):
                return f"stream {name} contains non-object row"
            missing = required_keys(row, required)
            if missing:
                return f"stream {name} row missing fields: {missing}"
    return ""


def runtime_feed_value_problem(streams: dict[str, Any]) -> str:
    for row in feed_rows(streams, "drop"):
        urgency = str(row.get("urgency") or "")
        if urgency not in {"high", "medium", "low"}:
            return f"stream drop row has invalid urgency: {urgency}"
        keywords = row.get("keywords")
        if not isinstance(keywords, list):
            return "stream drop row keywords is not a list"
    for row in feed_rows(streams, "trend"):
        trend = str(row.get("trend") or "")
        if trend not in {"rising", "cooling", "stable"}:
            return f"stream trend row has invalid trend: {trend}"
        confidence = row.get("confidence")
        if not isinstance(confidence, int) or not 0 <= confidence <= 100:
            return f"stream trend row has invalid confidence: {confidence}"
        if not is_number(row.get("price_delta")):
            return f"stream trend row has invalid price_delta: {row.get('price_delta')}"
        if not non_empty_list(row.get("reason_codes")):
            return "stream trend row reason_codes must be a non-empty list"
    for row in feed_rows(streams, "alert"):
        if not non_empty_list(row.get("reason_codes")):
            return "stream alert row reason_codes must be a non-empty list"
    return ""


def feed_rows(streams: dict[str, Any], name: str) -> list[dict[str, Any]]:
    rows = streams.get(name, [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def non_empty_list(value: object) -> bool:
    return isinstance(value, list) and bool(value)


def runtime_feed_noise_problem(streams: dict[str, Any]) -> str:
    for name, rows in streams.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            token = navigation_noise_token(row)
            if token:
                return f"stream {name} row contains navigation noise: {token}"
            if name == "release" and stale_release_time(row):
                return f"stream release row has stale source time: {row.get('time')}"
    return ""


NAVIGATION_NOISE_TOKENS = {
    "account",
    "cart",
    "company",
    "contact",
    "login",
    "privacy",
    "ログイン",
    "お問い合わせ",
    "カート",
    "会社概要",
    "登录",
    "登入",
    "购物车",
    "联系",
    "隐私",
}


def navigation_noise_token(row: dict[str, Any]) -> str:
    haystack = " ".join(str(row.get(key) or "") for key in ("title", "url"))
    tokens = {token.casefold() for token in re.split(r"[\s\W_]+", haystack) if token}
    for token in NAVIGATION_NOISE_TOKENS:
        if token.casefold() in tokens:
            return token
    return ""


def stale_release_time(row: dict[str, Any]) -> bool:
    value = str(row.get("time") or "")
    if len(value) < 4 or not value[:4].isdigit():
        return True
    return int(value[:4]) < datetime.now(timezone.utc).year


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
    min_duration_seconds: int,
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
        min_duration_seconds=min_duration_seconds,
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
            f"failed={list(verification.failed_cycles)}, unhealthy={verification.unhealthy_source_runs}, "
            f"duration={verification.duration_seconds}/{verification.min_duration_seconds}"
        ),
    )
