from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..shop import build_drop_signal
from ..trend import build_trend_feed


RELEASE_SOURCES = {"angelic_pretty", "baby_ssb", "alice_and_the_pirates", "metamorphose", "moitie"}
RELEASE_STATUSES = {"new_arrival", "preorder", "restock"}
MARKET_ALERT_KINDS = {"high_premium", "sample_gap"}
HOME_LINK_LIMIT = 30


def build_home_feed(
    events: list[dict[str, Any]],
    items: list[dict[str, Any]],
    market_summary: dict[str, Any],
    market_alerts: dict[str, Any],
    momentum: list[dict[str, Any]],
    source_runs: list[dict[str, Any]],
    brand_weights: list[dict[str, Any]] | None = None,
    source_urls: dict[str, str] | None = None,
) -> dict[str, Any]:
    release = release_feed(events, items)
    drop = drop_feed(events, items)
    trend = build_trend_feed(market_summary, momentum, events, brand_weights=brand_weights or [])
    alert = alert_feed(events, market_alerts, source_runs, source_urls=source_urls or {})
    streams = {
        "release": release,
        "drop": drop,
        "trend": trend,
        "alert": alert,
    }
    return {
        "summary": {
            "releases": len(release),
            "drops": len(drop),
            "trends": len(trend),
            "alerts": len(alert),
            "shops": unique_shop_count(drop),
        },
        "streams": streams,
        "all": merge_streams(streams),
    }


def release_feed(events: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        feed_card("release", row)
        for row in events
        if is_current_release_row(row)
    ]
    if rows:
        return unique_cards(sort_cards(rows))[:HOME_LINK_LIMIT]
    return unique_cards(sort_cards([
        feed_card("release", row)
        for row in items
        if is_current_release_row(row)
    ]))[:HOME_LINK_LIMIT]


def drop_feed(events: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [drop_card(row) for row in events if is_current_drop_row(row)]
    if rows:
        return unique_cards(sort_cards(rows))[:HOME_LINK_LIMIT]
    return unique_cards(sort_cards([drop_card(row) for row in items if is_current_drop_row(row)]))[:HOME_LINK_LIMIT]


def is_drop_row(row: dict[str, Any]) -> bool:
    return build_drop_signal(row) is not None


def is_current_drop_row(row: dict[str, Any]) -> bool:
    return is_drop_row(row) and is_current_source_date(str(row.get("published_at") or ""))


def is_current_release_row(row: dict[str, Any]) -> bool:
    return (
        row.get("source") in RELEASE_SOURCES
        and row.get("status") in RELEASE_STATUSES
        and is_current_source_date(str(row.get("published_at") or ""))
    )


def alert_feed(
    events: list[dict[str, Any]],
    market_alerts: dict[str, Any],
    source_runs: list[dict[str, Any]],
    source_urls: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    alerts = []
    urls = source_urls or {}
    release_count = 0
    for event in events:
        if not is_release_event(event):
            continue
        if release_count >= 20:
            break
        card = feed_card("alert", event, kind="new_release")
        card["reason_codes"] = release_alert_reasons(event)
        alerts.append(card)
        release_count += 1
    market_count = 0
    for alert in market_alerts.get("alerts", []):
        kind = market_alert_kind(alert)
        if kind not in MARKET_ALERT_KINDS:
            continue
        if market_count >= 20:
            break
        alerts.append(
            {
                "id": f"alert:{alert.get('kind')}:{alert.get('alias')}:{alert.get('item_name', '')}",
                "feed_type": "alert",
                "kind": kind,
                "brand": str(alert.get("alias") or ""),
                "title": str(alert.get("item_name") or alert.get("title") or alert.get("reason") or "Market alert"),
                "meta": market_alert_meta(alert),
                "time": "",
                "url": str(alert.get("url") or ""),
                "reason_codes": market_alert_reasons(alert, kind),
                "visual": visual_token("alert", str(alert.get("alias") or "Market"), kind),
            }
        )
        market_count += 1
    for run in latest_source_runs_by_source(source_runs):
        if str(run.get("status") or "") in {"failed", "degraded"}:
            alerts.append(
                {
                    "id": f"alert:source:{run.get('source')}",
                    "feed_type": "alert",
                    "kind": str(run.get("status") or "source"),
                    "brand": str(run.get("source") or ""),
                    "title": f"{run.get('source')} {run.get('status')}",
                    "meta": source_health_meta(run),
                    "time": str(run.get("checked_at") or ""),
                    "url": urls.get(str(run.get("source") or ""), ""),
                    "reason_codes": ["source_health"],
                    "visual": visual_token("alert", str(run.get("source") or "Source"), str(run.get("status") or "source")),
                }
            )
    return unique_cards(sort_cards(alerts))[:40]


def latest_source_runs_by_source(source_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest = []
    seen = set()
    sorted_runs = sorted(source_runs, key=lambda run: str(run.get("checked_at") or ""), reverse=True)
    for run in sorted_runs:
        source = str(run.get("source") or "")
        if not source or source in seen:
            continue
        seen.add(source)
        latest.append(run)
    return latest


def is_release_event(event: dict[str, Any]) -> bool:
    return (
        event.get("event_type") in {"new_item", "content_changed"}
        and event.get("source") in RELEASE_SOURCES
        and event.get("status") in RELEASE_STATUSES
        and is_current_source_date(str(event.get("published_at") or ""))
    )


def market_alert_kind(alert: dict[str, Any]) -> str:
    raw_kind = str(alert.get("kind") or "market_alert")
    if raw_kind in {"sample_spike", "brand_heat"}:
        return "high_premium"
    return raw_kind


def market_alert_meta(alert: dict[str, Any]) -> str:
    parts = [str(alert.get("reason") or alert.get("severity") or "")]
    premium_rate = alert.get("premium_rate")
    if premium_rate not in (None, ""):
        try:
            parts.append(f"{round(float(premium_rate) * 100)}% premium")
        except (TypeError, ValueError):
            parts.append(f"{premium_rate} premium")
    return " · ".join(part for part in parts if part)


def market_alert_reasons(alert: dict[str, Any], kind: str) -> list[str]:
    reasons = [kind]
    raw_kind = str(alert.get("kind") or "")
    severity = str(alert.get("severity") or "")
    if raw_kind and raw_kind not in reasons:
        reasons.append(raw_kind)
    if severity and severity not in reasons:
        reasons.append(severity)
    return reasons


def source_health_meta(run: dict[str, Any]) -> str:
    parts = [
        str(run.get("error_message") or ""),
        f"error_rate={run.get('error_rate', 0)}",
        f"latency_ms={run.get('latency_ms', 0)}",
        f"item_count={run.get('item_count', 0)}",
    ]
    return " · ".join(part for part in parts if part)


def feed_card(feed_type: str, row: dict[str, Any], kind: str | None = None) -> dict[str, Any]:
    source = str(row.get("source") or "")
    status = str(row.get("status") or "")
    time_value, time_kind = feed_time(row)
    brand = brand_label(source, str(row.get("title") or ""))
    resolved_kind = kind or str(row.get("event_type") or status or feed_type)
    price = metadata_text(row, "price")
    image_url = metadata_text(row, "image_url")
    if feed_type == "release" and not price:
        price = "未取得"
    return {
        "id": f"{feed_type}:{source}:{row.get('item_hash') or row.get('url') or row.get('title')}",
        "feed_type": feed_type,
        "kind": resolved_kind,
        "kind_label": localized_kind_label(resolved_kind),
        "type": status or resolved_kind,
        "brand": brand,
        "title": str(row.get("title") or ""),
        "title_zh": title_hint(str(row.get("title") or ""), status),
        "meta": source_label(source),
        "time": time_value,
        "time_kind": time_kind,
        "url": str(row.get("url") or ""),
        "status": status,
        "status_label": localized_status_label(status),
        "price": price,
        "source_label": source_label(source),
        "visual": visual_token(feed_type, brand, status, image_url=image_url),
    }


def release_alert_reasons(event: dict[str, Any]) -> list[str]:
    reasons = ["new_release"]
    for key in ("event_type", "status"):
        value = str(event.get(key) or "")
        if value and value not in reasons:
            reasons.append(value)
    return reasons


def drop_card(row: dict[str, Any]) -> dict[str, Any]:
    card = feed_card("drop", row)
    signal = build_drop_signal(row)
    if signal is None:
        card["reason_codes"] = ["generic_page"]
        return card
    keywords = list(signal.item.keywords)
    card["brand"] = signal.shop.name
    card["shop"] = signal.shop.name
    card["item"] = signal.item.title
    card["title"] = signal.item.title
    card["url"] = signal.item.url or signal.shop.url or card.get("url", "")
    card["keywords"] = keywords
    card["urgency"] = signal.urgency
    card["meta"] = signal.shop.name
    card["reason_codes"] = list(signal.reason_codes)
    return card


def matched_keywords(row: dict[str, Any]) -> list[str]:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("matched_keywords") or []
    if isinstance(raw, list):
        return [str(keyword) for keyword in raw if str(keyword)]
    text = str(raw)
    return [text] if text else []


def metadata_text(row: dict[str, Any], key: str) -> str:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get(key)
    return str(value).strip() if value not in (None, "") else ""


def brand_label(source: str, title: str) -> str:
    labels = {
        "angelic_pretty": "AP",
        "baby_ssb": "BABY",
        "alice_and_the_pirates": "AATP",
        "metamorphose": "Meta",
        "moitie": "MMM",
        "generic_page": "Shop",
    }
    if source in labels:
        return labels[source]
    return title.split(" ", 1)[0] if title else source


def source_label(source: str) -> str:
    labels = {
        "angelic_pretty": "Angelic Pretty",
        "baby_ssb": "BABY",
        "alice_and_the_pirates": "AATP",
        "metamorphose": "Metamorphose",
        "moitie": "Moi-meme-Moitie",
        "generic_page": "Shop / Proxy",
    }
    return labels.get(source, source)


def feed_time(row: dict[str, Any]) -> tuple[str, str]:
    published_at = str(row.get("published_at") or "")
    if published_at:
        return published_at, "published"
    return "", ""


def is_current_source_date(value: str) -> bool:
    if len(value) < 4 or not value[:4].isdigit():
        return False
    return int(value[:4]) >= current_year()


def current_year() -> int:
    return datetime.now(timezone.utc).year


def localized_status_label(status: str) -> str:
    labels = {
        "new_arrival": "新作 / 新品",
        "preorder": "予約 / 预约",
        "restock": "再入荷 / 再贩",
        "shop_news": "ショップ情報 / 店铺资讯",
    }
    return labels.get(status, status)


def localized_kind_label(kind: str) -> str:
    labels = {
        "new_item": "新着 / 新发现",
        "content_changed": "更新 / 内容变化",
        "update": "更新 / 状态变化",
        "new_release": "新作 / 新发售",
        "high_premium": "高騰 / 高溢价",
        "sample_gap": "サンプル不足 / 样本不足",
        "rising": "上昇 / 上升",
        "stable": "安定 / 稳定",
        "cooling": "下落 / 降温",
    }
    return labels.get(kind, kind)


def title_hint(title: str, status: str) -> str:
    replacements = [
        ("新作", "新作"),
        ("予約", "预约"),
        ("ご予約", "预约"),
        ("受注", "受注预约"),
        ("再入荷", "再入荷 / 再贩"),
        ("再販", "再贩"),
        ("入荷", "到货"),
        ("販売開始", "开始贩售"),
        ("発売", "发售"),
        ("お知らせ", "通知"),
        ("ワンピース", "OP 连衣裙"),
        ("ジャンパースカート", "JSK 吊带裙"),
        ("ブラウス", "衬衫"),
        ("カチューシャ", "发箍"),
    ]
    hints = [zh for token, zh in replacements if token in title]
    if not hints and status:
        label = localized_status_label(status)
        hints.append(label.split(" / ")[-1])
    return " · ".join(dict.fromkeys(hints[:4]))


def visual_token(feed_type: str, brand: str, status: str, image_url: str = "") -> dict[str, str]:
    initials = {
        "AP": "AP",
        "BABY": "BB",
        "AATP": "AT",
        "Meta": "ME",
        "MMM": "MM",
        "Shop": "SH",
    }.get(brand, (brand[:2] or feed_type[:2]).upper())
    icons = {
        "release": "R",
        "drop": "D",
        "trend": "T",
        "alert": "!",
    }
    return {
        "initials": initials,
        "mark": icons.get(feed_type, "*"),
        "tone": status or feed_type,
        "image_url": image_url,
    }


def merge_streams(streams: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for key in ("release", "drop", "alert", "trend"):
        rows.extend(sort_cards(streams.get(key, [])))
    linked_rows = [row for row in rows if row.get("url")]
    return unique_cards(linked_rows)[:HOME_LINK_LIMIT]


def sort_cards(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: str(row.get("time") or ""), reverse=True)


def unique_cards(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    results = []
    for row in rows:
        key = str(row.get("url") or row.get("id") or row.get("title") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        results.append(row)
    return results


def unique_shop_count(rows: list[dict[str, Any]]) -> int:
    shops = {
        str(row.get("shop") or row.get("brand") or "").strip()
        for row in rows
        if str(row.get("shop") or row.get("brand") or "").strip()
    }
    return len(shops)
