from __future__ import annotations

from typing import Any

from ..trend import build_trend_feed


RELEASE_SOURCES = {"angelic_pretty", "baby_ssb", "alice_and_the_pirates", "metamorphose", "moitie"}
RELEASE_STATUSES = {"new_arrival", "preorder", "restock"}


def build_home_feed(
    events: list[dict[str, Any]],
    items: list[dict[str, Any]],
    market_summary: dict[str, Any],
    market_alerts: dict[str, Any],
    momentum: list[dict[str, Any]],
    source_runs: list[dict[str, Any]],
    brand_weights: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    release = release_feed(events, items)
    drop = drop_feed(events, items)
    trend = build_trend_feed(market_summary, momentum, events, brand_weights=brand_weights or [])
    alert = alert_feed(events, market_alerts, source_runs)
    streams = {
        "release": release,
        "drop": drop,
        "trend": trend,
        "alert": alert,
    }
    return {
        "summary": {
            "drops": len(release),
            "shops": len(drop),
            "trends": len(trend),
            "alerts": len(alert),
        },
        "streams": streams,
        "all": merge_streams(streams),
    }


def release_feed(events: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        feed_card("release", row)
        for row in events
        if row.get("source") in RELEASE_SOURCES and row.get("status") in RELEASE_STATUSES
    ]
    if rows:
        return rows[:30]
    return [
        feed_card("release", row)
        for row in items
        if row.get("source") in RELEASE_SOURCES and row.get("status") in RELEASE_STATUSES
    ][:30]


def drop_feed(events: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [drop_card(row) for row in events if row.get("source") == "generic_page"]
    if rows:
        return rows[:30]
    return [drop_card(row) for row in items if row.get("source") == "generic_page"][:30]


def alert_feed(
    events: list[dict[str, Any]],
    market_alerts: dict[str, Any],
    source_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    alerts = []
    for event in events[:20]:
        if is_release_event(event):
            alerts.append(feed_card("alert", event, kind="new_release"))
    for alert in market_alerts.get("alerts", [])[:20]:
        kind = market_alert_kind(alert)
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
            }
        )
    for run in source_runs:
        if str(run.get("status") or "") in {"failed", "degraded"}:
            alerts.append(
                {
                    "id": f"alert:source:{run.get('source')}",
                    "feed_type": "alert",
                    "kind": str(run.get("status") or "source"),
                    "brand": str(run.get("source") or ""),
                    "title": f"{run.get('source')} {run.get('status')}",
                    "meta": str(run.get("error_message") or f"error_rate={run.get('error_rate', 0)}"),
                    "time": str(run.get("checked_at") or ""),
                    "url": "",
                    "reason_codes": ["source_health"],
                }
            )
    return alerts[:40]


def is_release_event(event: dict[str, Any]) -> bool:
    return (
        event.get("event_type") in {"new_item", "content_changed"}
        and event.get("source") in RELEASE_SOURCES
        and event.get("status") in RELEASE_STATUSES
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


def feed_card(feed_type: str, row: dict[str, Any], kind: str | None = None) -> dict[str, Any]:
    source = str(row.get("source") or "")
    status = str(row.get("status") or "")
    return {
        "id": f"{feed_type}:{source}:{row.get('item_hash') or row.get('url') or row.get('title')}",
        "feed_type": feed_type,
        "kind": kind or str(row.get("event_type") or status or feed_type),
        "brand": brand_label(source, str(row.get("title") or "")),
        "title": str(row.get("title") or ""),
        "meta": " · ".join(part for part in [source, status, str(row.get("event_type") or "")] if part),
        "time": str(row.get("created_at") or row.get("last_seen_at") or row.get("published_at") or ""),
        "url": str(row.get("url") or ""),
        "status": status,
    }


def drop_card(row: dict[str, Any]) -> dict[str, Any]:
    card = feed_card("drop", row)
    matches = matched_keywords(row)
    if matches:
        card["meta"] = " · ".join(
            part for part in [card.get("meta", ""), "keywords: " + ", ".join(matches[:6])] if part
        )
        card["reason_codes"] = ["keyword_match", *[f"kw:{match}" for match in matches[:6]]]
    else:
        card["reason_codes"] = ["generic_page"]
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


def merge_streams(streams: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for key in ("release", "drop", "trend", "alert"):
        rows.extend(streams.get(key, []))
    return sorted(rows, key=lambda row: str(row.get("time") or ""), reverse=True)[:80]
