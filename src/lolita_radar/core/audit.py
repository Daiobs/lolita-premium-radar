from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from ..adapters import SourceConfig
from ..adapters.generic_page import apply_ignore_patterns, linked_shop_items, strip_navigation_tokens, suppress_duplicate_segments
from ..crawler import enrich_source_runs
from ..feed import build_home_feed
from ..runner import verify_check_loop
from ..shop import build_drop_signal
from ..storage import connect
from ..trend import build_trend_feed
from ..web import FEED_INDEX_HTML, get_feed_payload, get_feed_state


AUDIT_STATUSES = {"pass", "fail", "missing"}


@dataclass(frozen=True)
class FeedOsAuditCheck:
    name: str
    status: str
    detail: str
    evidence: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in AUDIT_STATUSES:
            raise ValueError(f"unknown audit status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "status": self.status, "detail": self.detail}
        if self.evidence is not None:
            payload["evidence"] = self.evidence
        return payload


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
        audit_generic_shop_item_extraction(),
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
        project_root / ".env.example",
        project_root / ".github",
    ]
    tokens = forbidden_product_tokens()
    findings = []
    for path in audited_product_constraint_paths(roots):
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        for token in tokens:
            if token.casefold() in text:
                findings.append(f"{path.relative_to(project_root)} contains {token}")
    return findings


def audited_product_constraint_paths(roots: list[Path]) -> list[Path]:
    suffixes = {".py", ".toml", ".yml", ".yaml", ".example"}
    paths = []
    for root in roots:
        if root.is_file():
            paths.append(root)
        elif root.exists():
            paths.extend(path for path in sorted(root.rglob("*")) if path.is_file() and path.suffix in suffixes)
    return paths


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
        "tele" + "gram",
        "dis" + "cord",
        "web" + "hook",
    )


def audit_frontend_feed_os() -> FeedOsAuditCheck:
    required = (
        "feed-card",
        "badge",
        "summary",
        "/api/feed",
        'data-filter="release"',
        'data-filter="drop"',
        'data-filter="trend"',
        'data-filter="alert"',
        "releasesCount",
        "dropsCount",
        "STATUS_TEXT",
        "statusLabel",
        "KIND_TEXT",
        "kindLabel",
        "REASON_TEXT",
        "reasonLabel",
        "metaHtml",
        "row.keywords",
        "keywords",
        "sampleCount",
        "premiumRate",
        "errorRate",
        "latency",
        "itemCount",
        "visual.image_url",
        'loading="lazy"',
        "has-image",
        "源头发布时间",
        "掲載元日",
        "`${localized} · ${row.title}`",
        'activeFilter === "all"',
        "feed.streams?.[activeFilter]",
        'button.addEventListener("click"',
        'item.classList.toggle("active"',
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
    summary = feed.get("summary", {})
    checks = [
        required_keys(summary, ("releases", "drops", "trends", "alerts", "shops")),
        required_keys(streams["release"][0], ("brand", "title", "type", "time", "price", "url")),
        required_keys(streams["drop"][0], ("shop", "item", "keywords", "urgency", "url")),
        required_keys(streams["trend"][0], ("brand", "trend", "confidence", "price_delta", "reason_codes")),
        required_keys(streams["alert"][0], ("feed_type", "kind", "title", "reason_codes", "url")),
        visual_image_problem(streams["release"][0]),
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
    return FeedOsAuditCheck("feed_contract", "pass", "4 streams expose required fields, visuals, and priority ordering")


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
    missing_summary = [name for name in ("releases", "drops", "shops", "trends", "alerts") if name not in summary]
    if missing_summary:
        return FeedOsAuditCheck("runtime_feed_state", "fail", "missing summary fields: " + ", ".join(missing_summary))
    summary_problem = summary_count_problem(summary, streams)
    if summary_problem:
        return FeedOsAuditCheck("runtime_feed_state", "fail", summary_problem)
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
    payload_problem = runtime_feed_payload_problem(config_path, db_path, brands_path, market_path, feed)
    if payload_problem:
        return FeedOsAuditCheck("runtime_feed_state", "fail", payload_problem)
    counts = ", ".join(f"{name}={len(streams.get(name, []))}" for name in expected_streams)
    return FeedOsAuditCheck("runtime_feed_state", "pass", f"current config/db builds Feed OS streams ({counts})")


def runtime_feed_payload_problem(
    config_path: Path,
    db_path: Path,
    brands_path: Path | None,
    market_path: Path | None,
    expected_feed: dict[str, Any],
) -> str:
    try:
        payload = get_feed_payload(
            config_path=config_path,
            db_path=db_path,
            brands_path=brands_path,
            market_path=market_path,
        )
    except Exception as exc:
        return f"get_feed_payload failed: {exc}"
    if payload.get("feed") != expected_feed:
        return "api feed payload does not match runtime Feed OS state"
    forbidden_payload_keys = ("items", "events", "market", "source_runs", "brand_weights")
    leaked = [key for key in forbidden_payload_keys if key in payload]
    if leaked:
        return "api feed payload leaks full state keys: " + ", ".join(leaked)
    if "counts" not in payload:
        return "api feed payload missing counts"
    return ""


def visual_image_problem(row: dict[str, Any]) -> str:
    visual = row.get("visual")
    if not isinstance(visual, dict):
        return "release.visual"
    image_url = str(visual.get("image_url") or "")
    if not image_url.startswith(("http://", "https://")):
        return "release.visual.image_url"
    return ""


def feed_ordering_problem(rows: list[dict[str, Any]]) -> str:
    priority = {"release": 0, "drop": 1, "alert": 2, "trend": 3}
    previous = -1
    seen_urls = set()
    for row in rows:
        feed_type = str(row.get("feed_type") or "")
        current = priority.get(feed_type)
        if current is None:
            return f"unknown feed_type in state.feed.all: {feed_type}"
        if current < previous:
            return "state.feed.all violates RELEASE > DROP > ALERT > TREND ordering"
        previous = current
        url = str(row.get("url") or "")
        if not url:
            return "state.feed.all contains a row without url"
        if url in seen_urls:
            return f"state.feed.all contains duplicate url: {url}"
        seen_urls.add(url)
    return ""


def summary_count_problem(summary: dict[str, Any], streams: dict[str, Any]) -> str:
    summary_to_stream = {
        "releases": "release",
        "drops": "drop",
        "trends": "trend",
        "alerts": "alert",
    }
    for summary_key, stream_name in summary_to_stream.items():
        expected = len(streams.get(stream_name, [])) if isinstance(streams.get(stream_name), list) else 0
        actual = summary.get(summary_key)
        if actual != expected:
            return f"summary {summary_key}={actual} does not match {stream_name} count={expected}"
    drop_rows = streams.get("drop", [])
    expected_shops = unique_drop_shop_count(drop_rows) if isinstance(drop_rows, list) else 0
    if summary.get("shops") != expected_shops:
        return f"summary shops={summary.get('shops')} does not match unique drop shops={expected_shops}"
    return ""


def unique_drop_shop_count(rows: list[Any]) -> int:
    shops = {
        str(row.get("shop") or row.get("brand") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("shop") or row.get("brand") or "").strip()
    }
    return len(shops)


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
            visual_problem = card_visual_problem(row)
            if visual_problem:
                return f"stream {name} row has invalid visual: {visual_problem}"
    return ""


def card_visual_problem(row: dict[str, Any]) -> str:
    visual = row.get("visual")
    if not isinstance(visual, dict):
        return "missing visual"
    missing = required_keys(visual, ("initials", "mark", "tone"))
    if missing:
        return missing
    if "image_url" in visual and not isinstance(visual.get("image_url"), str):
        return "image_url"
    return ""


def runtime_feed_value_problem(streams: dict[str, Any]) -> str:
    for row in feed_rows(streams, "release"):
        release_context_problem = release_card_context_problem(row)
        if release_context_problem:
            return release_context_problem
    for row in feed_rows(streams, "drop"):
        urgency = str(row.get("urgency") or "")
        if urgency not in {"high", "medium", "low"}:
            return f"stream drop row has invalid urgency: {urgency}"
        keywords = row.get("keywords")
        if not isinstance(keywords, list):
            return "stream drop row keywords is not a list"
        drop_context_problem = drop_card_context_problem(row)
        if drop_context_problem:
            return drop_context_problem
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
        release_alert_problem = release_alert_boundary_problem(row)
        if release_alert_problem:
            return release_alert_problem
        alert_kind_problem = alert_kind_boundary_problem(row)
        if alert_kind_problem:
            return alert_kind_problem
        source_health_problem = source_health_alert_problem(row)
        if source_health_problem:
            return source_health_problem
    return ""


def release_card_context_problem(row: dict[str, Any]) -> str:
    price = row.get("price")
    if price not in (None, "") and not isinstance(price, str):
        return f"stream release row has invalid price: {price}"
    time_value = str(row.get("time") or "")
    if not time_value:
        return "stream release row is missing source time"
    if row.get("time_kind") != "published":
        return f"stream release row has invalid time_kind: {row.get('time_kind')}"
    if stale_release_time(row):
        return f"stream release row has stale source time: {time_value}"
    return ""


def drop_card_context_problem(row: dict[str, Any]) -> str:
    price = row.get("price")
    if price not in (None, "") and not isinstance(price, str):
        return f"stream drop row has invalid price: {price}"
    time_value = str(row.get("time") or "")
    if not time_value:
        return "stream drop row is missing source time"
    if row.get("time_kind") != "published":
        return f"stream drop row has invalid time_kind: {row.get('time_kind')}"
    if stale_release_time(row):
        return f"stream drop row has stale source time: {time_value}"
    visual = row.get("visual")
    if isinstance(visual, dict):
        image_url = str(visual.get("image_url") or "")
        if image_url and not image_url.startswith(("http://", "https://")):
            return f"stream drop row has invalid image_url: {image_url}"
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


def source_health_alert_problem(row: dict[str, Any]) -> str:
    reason_codes = row.get("reason_codes")
    if not (isinstance(reason_codes, list) and "source_health" in reason_codes):
        return ""
    missing = [key for key in ("error_rate", "latency_ms", "item_count") if key not in row]
    if missing:
        return "stream alert source_health row missing metrics: " + ", ".join(missing)
    if not is_number(row.get("error_rate")):
        return f"stream alert source_health row has invalid error_rate: {row.get('error_rate')}"
    if not isinstance(row.get("latency_ms"), int) or isinstance(row.get("latency_ms"), bool):
        return f"stream alert source_health row has invalid latency_ms: {row.get('latency_ms')}"
    if not isinstance(row.get("item_count"), int) or isinstance(row.get("item_count"), bool):
        return f"stream alert source_health row has invalid item_count: {row.get('item_count')}"
    return ""


def release_alert_boundary_problem(row: dict[str, Any]) -> str:
    reason_codes = row.get("reason_codes")
    if str(row.get("kind") or "") == "new_release" or (
        isinstance(reason_codes, list) and "new_release" in reason_codes
    ):
        return "stream alert row must be system-level, not new_release"
    return ""


def alert_kind_boundary_problem(row: dict[str, Any]) -> str:
    reason_codes = row.get("reason_codes")
    if isinstance(reason_codes, list) and "source_health" in reason_codes:
        return ""
    kind = str(row.get("kind") or "")
    if kind in {"high_premium", "sample_gap"}:
        return ""
    return f"stream alert row has unsupported system alert kind: {kind}"


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
            if is_release_noise_candidate(name, row) and stale_release_time(row):
                return f"stream {name} row has stale source time: {row.get('time')}"
    return ""


def is_release_noise_candidate(stream_name: str, row: dict[str, Any]) -> bool:
    return stream_name == "release"


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
            "metadata": {"price": "¥38,280", "image_url": "https://example.com/images/shell.webp"},
        },
        {
            "source": "generic_page",
            "event_type": "content_changed",
            "status": "shop_news",
            "title": "Proxy JSK 预约",
            "url": "https://example.com/shop",
            "published_at": "2026-06-30",
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
    year = datetime.now(timezone.utc).year
    market_summary = {
        "brands": [
            {"brand_alias": "AP", "sample_count": 4, "avg_premium_rate": 0.5},
            {"brand_alias": "Meta", "sample_count": 3, "avg_premium_rate": -0.2},
        ]
    }
    momentum = [{"brand_alias": "AP", "direction": "rising", "observed_at": "2026-06-30"}]
    trends = build_trend_feed(
        market_summary,
        momentum,
        [{"source": "angelic_pretty", "status": "new_arrival", "published_at": f"{year}-06-30"}],
        brand_weights=[{"alias": "BABY"}],
    )
    if not trends:
        return FeedOsAuditCheck("trend_engine", "fail", "no trend cards produced")
    trends_by_brand = {str(trend.get("brand") or ""): trend for trend in trends}
    expected_directions = {"AP": "rising", "Meta": "cooling", "BABY": "stable"}
    missing_brands = sorted(set(expected_directions) - set(trends_by_brand))
    if missing_brands:
        return FeedOsAuditCheck("trend_engine", "fail", "missing trend brands: " + ", ".join(missing_brands))
    for brand, direction in expected_directions.items():
        trend = trends_by_brand[brand]
        raw_confidence = trend.get("confidence")
        confidence = int(raw_confidence) if raw_confidence is not None else -1
        if trend.get("trend") != direction or not (0 <= confidence <= 100):
            return FeedOsAuditCheck("trend_engine", "fail", f"invalid trend output for {brand}: {trend}")
        if not trend.get("reason_codes"):
            return FeedOsAuditCheck("trend_engine", "fail", f"missing reason_codes for {brand}")
    without_release = build_trend_feed(market_summary, momentum, [], brand_weights=[{"alias": "BABY"}])[0]
    ap_trend = trends_by_brand["AP"]
    if "release_activity" not in ap_trend.get("reason_codes", []) or int(ap_trend.get("confidence") or 0) <= int(without_release.get("confidence") or 0):
        return FeedOsAuditCheck("trend_engine", "fail", "release events did not affect trend reasons/confidence")
    stale_trends = build_trend_feed(
        market_summary,
        momentum,
        [{"source": "angelic_pretty", "status": "new_arrival", "published_at": f"{year - 1}-12-31"}],
        brand_weights=[{"alias": "BABY"}],
    )
    stale_by_brand = {str(trend.get("brand") or ""): trend for trend in stale_trends}
    stale_ap = stale_by_brand.get("AP")
    if not stale_ap:
        return FeedOsAuditCheck("trend_engine", "fail", "stale release check missing AP trend")
    if "release_activity" in stale_ap.get("reason_codes", []):
        return FeedOsAuditCheck("trend_engine", "fail", "stale release events affected trend reasons")
    if int(ap_trend.get("confidence") or 0) <= int(stale_ap.get("confidence") or 0):
        return FeedOsAuditCheck("trend_engine", "fail", "current release events did not outrank stale release confidence")
    missing_date_trends = build_trend_feed(
        market_summary,
        momentum,
        [{"source": "angelic_pretty", "status": "new_arrival"}],
        brand_weights=[{"alias": "BABY"}],
    )
    missing_date_by_brand = {str(trend.get("brand") or ""): trend for trend in missing_date_trends}
    missing_date_ap = missing_date_by_brand.get("AP")
    if not missing_date_ap:
        return FeedOsAuditCheck("trend_engine", "fail", "missing-date release check missing AP trend")
    if "release_activity" in missing_date_ap.get("reason_codes", []):
        return FeedOsAuditCheck("trend_engine", "fail", "release events without source publish time affected trend reasons")
    if int(ap_trend.get("confidence") or 0) <= int(missing_date_ap.get("confidence") or 0):
        return FeedOsAuditCheck("trend_engine", "fail", "current release events did not outrank missing-date release confidence")
    return FeedOsAuditCheck(
        "trend_engine",
        "pass",
        "rule-based rising/cooling/stable output with confidence, current release activity, stale release filtering, missing-date release filtering, and reasons",
    )


def audit_shop_drop_model() -> FeedOsAuditCheck:
    required_keywords = ("JSK", "OP", "再贩", "预约", "尾款")
    signal = build_drop_signal(
        {
            "source": "proxy_shop",
            "event_type": "new_item",
            "status": "shop_news",
            "title": "Proxy page",
            "url": "https://example.com/shop",
            "metadata": {
                "source_type": "generic_page",
                "shop": {"name": "Tokyo Proxy", "url": "https://example.com/shop"},
                "item": {"title": "Shell Garden JSK", "url": "https://example.com/shop/shell"},
                "matched_keywords": list(required_keywords),
            },
        }
    )
    if signal is None:
        return FeedOsAuditCheck("shop_drop_model", "fail", "generic_page new item did not produce DROP")
    if signal.shop.name != "Tokyo Proxy" or signal.item.title != "Shell Garden JSK":
        return FeedOsAuditCheck("shop_drop_model", "fail", "shop/item mapping is incorrect")
    if signal.urgency != "high" or "keyword_match" not in signal.reason_codes:
        return FeedOsAuditCheck("shop_drop_model", "fail", f"unexpected urgency/reasons: {signal}")
    missing_keywords = [keyword for keyword in required_keywords if f"kw:{keyword}" not in signal.reason_codes]
    if missing_keywords:
        return FeedOsAuditCheck("shop_drop_model", "fail", "missing DROP keywords: " + ", ".join(missing_keywords))
    page_level_signal = build_drop_signal(
        {
            "source": "generic_page",
            "event_type": "content_changed",
            "status": "shop_news",
            "title": "Whole shop page JSK 预约",
            "url": "https://example.com/shop",
            "metadata": {"matched_keywords": ["JSK", "预约"]},
        }
    )
    if page_level_signal is not None:
        return FeedOsAuditCheck("shop_drop_model", "fail", "page-level keyword match produced DROP without item")
    return FeedOsAuditCheck(
        "shop_drop_model",
        "pass",
        "Shop -> Item DROP triggers on new item and JSK/OP/再贩/预约/尾款 keywords without page-level keyword noise",
    )


def audit_generic_shop_item_extraction() -> FeedOsAuditCheck:
    config = SourceConfig(
        name="proxy_shop",
        type="generic_page",
        url="https://example.com/shop/",
        keywords=["JSK", "预约"],
        options={"shop_name": "Tokyo Proxy"},
    )
    items = linked_shop_items(
        """
        <article>
          <time datetime="2026-06-30"></time>
          <a href="/shop/shell-jsk">
            <img alt="" src="/images/shell.webp">
            Shell Garden JSK 预约
          </a>
          <span>¥12,800</span>
        </article>
        """,
        config,
        "Shell Garden JSK 预约 ¥12,800",
        ["JSK", "预约"],
    )
    if len(items) != 1:
        return FeedOsAuditCheck("generic_shop_item_extraction", "fail", f"expected 1 linked item, got {len(items)}")
    item = items[0]
    metadata = item.metadata
    if item.published_at != "2026-06-30" or metadata.get("image_url") != "https://example.com/images/shell.webp" or metadata.get("price") != "¥12,800":
        return FeedOsAuditCheck("generic_shop_item_extraction", "fail", f"linked item metadata incomplete: {metadata}")
    feed = build_home_feed(
        [
            {
                "source": item.source,
                "event_type": "new_item",
                "status": item.status.value,
                "title": item.title,
                "url": item.url,
                "published_at": item.published_at,
                "metadata": metadata,
            }
        ],
        [],
        {"brands": []},
        {"alerts": []},
        [],
        [],
    )
    drops = feed.get("streams", {}).get("drop", [])
    if len(drops) != 1:
        return FeedOsAuditCheck("generic_shop_item_extraction", "fail", f"expected 1 Drop card, got {len(drops)}")
    drop = drops[0]
    expected = {
        "shop": "Tokyo Proxy",
        "item": "Shell Garden JSK 预约",
        "time": "2026-06-30",
        "price": "¥12,800",
    }
    mismatches = [key for key, value in expected.items() if drop.get(key) != value]
    if mismatches or drop.get("visual", {}).get("image_url") != "https://example.com/images/shell.webp":
        return FeedOsAuditCheck("generic_shop_item_extraction", "fail", f"Drop card incomplete: {drop}")
    return FeedOsAuditCheck("generic_shop_item_extraction", "pass", "GenericPage public item links produce Drop cards with source time, image, price, and keywords")


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
            missing_loop_evidence_requirements(expected_cycles, min_duration_seconds),
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
            verification.to_dict(),
        )
    status = "fail" if verification.status == "failed" else "missing"
    return FeedOsAuditCheck(
        "stable_loop_evidence",
        status,
        (
            f"verify-loop {verification.status}: observed={verification.observed_cycles}/"
            f"{verification.expected_cycles}, missing={list(verification.missing_cycles)}, "
            f"duplicate={list(verification.duplicate_cycles)}, failed={list(verification.failed_cycles)}, "
            f"missing_cycle_timestamps={list(verification.missing_cycle_timestamps)}, "
            f"cycle_time_mismatches={list(verification.cycle_time_mismatches)}, "
            f"unhealthy={verification.unhealthy_source_runs}, "
            f"duration={verification.duration_seconds}/{verification.min_duration_seconds}"
        ),
        verification.to_dict(),
    )


def missing_loop_evidence_requirements(expected_cycles: int, min_duration_seconds: int) -> dict[str, Any]:
    return {
        "required": {
            "loop_log": True,
            "loop_exit_file": True,
            "source_runs": True,
        },
        "expected_cycles": max(1, int(expected_cycles)),
        "min_duration_seconds": max(0, int(min_duration_seconds)),
        "required_checks": [
            "loop log contains expected cycle coverage",
            "loop log duration meets min_duration_seconds",
            "exit file contains 0",
            "source_runs fall inside loop evidence window",
            "source_runs are healthy",
            "no duplicate cycles",
            "no missing cycle timestamps in checked_at logs",
            "no cycle timestamps outside loop evidence window",
        ],
    }
