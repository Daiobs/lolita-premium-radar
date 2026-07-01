from __future__ import annotations

from typing import Any

from ..shop import build_drop_signal
from ..source_dates import CURRENT_SOURCE_WINDOW_DAYS, is_current_source_date
from ..trend import build_market_sample_trends, build_trend_feed


RELEASE_SOURCES = {"angelic_pretty", "baby_ssb", "alice_and_the_pirates", "metamorphose", "moitie"}
RELEASE_STATUSES = {"new_arrival", "preorder", "restock"}
MARKET_ALERT_KINDS = {"high_premium"}
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
    shop_events: list[dict[str, Any]] | None = None,
    market_samples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    release = release_feed(events, items)
    drop = drop_feed(events, items, shop_events=shop_events or [])
    trend = (
        build_market_sample_trends(market_samples)
        if market_samples
        else build_trend_feed(market_summary, momentum, events, brand_weights=brand_weights or [])
    )
    alert = alert_feed(
        events,
        market_alerts,
        source_runs,
        source_urls=source_urls or {},
        shop_events=shop_events or [],
        trends=trend,
    )
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
    ] + [
        feed_card("release", row)
        for row in items
        if is_current_release_row(row)
    ]
    return unique_cards(sort_cards(rows))[:HOME_LINK_LIMIT]


def drop_feed(
    events: list[dict[str, Any]],
    items: list[dict[str, Any]],
    shop_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if shop_events:
        return unique_cards(sort_cards([shop_event_card(row) for row in shop_events]))[:HOME_LINK_LIMIT]
    rows = [drop_card(row) for row in events if is_current_drop_row(row)]
    rows.extend(drop_card(row) for row in items if is_current_drop_row(row))
    return unique_cards(sort_cards(rows))[:HOME_LINK_LIMIT]


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
    shop_events: list[dict[str, Any]] | None = None,
    trends: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    alerts = []
    urls = source_urls or {}
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
                "title_zh": market_alert_title(alert, kind, language="zh"),
                "title_ja": market_alert_title(alert, kind, language="ja"),
                "use_localized_title": True,
                "meta": market_alert_meta(alert),
                "premium_rate": alert.get("premium_rate", ""),
                "time": "",
                "url": str(alert.get("url") or ""),
                "reason_codes": market_alert_reasons(alert, kind),
                "visual": visual_token("alert", str(alert.get("alias") or "Market"), kind),
            }
        )
        market_count += 1
    for event in shop_events or []:
        assist = purchase_assist_alert(event)
        if assist:
            alerts.append(assist)
    for trend in trends or []:
        if trend.get("trend") == "rising" and int(trend.get("confidence") or 0) >= 70:
            alerts.append(
                {
                    "id": f"alert:trend:{trend.get('id')}",
                    "feed_type": "alert",
                    "kind": "high_premium",
                    "brand": str(trend.get("brand") or ""),
                    "title": str(trend.get("title") or "Market trend rising"),
                    "title_zh": f"高溢价: {trend.get('title')}",
                    "title_ja": f"高プレミア: {trend.get('title')}",
                    "use_localized_title": True,
                    "meta": str(trend.get("meta") or ""),
                    "premium_rate": trend.get("price_delta", ""),
                    "time": str(trend.get("time") or ""),
                    "url": str(trend.get("url") or ""),
                    "reason_codes": ["high_premium", "market_trend"],
                    "visual": visual_token("alert", str(trend.get("brand") or "Market"), "high_premium"),
                    "cta": "Open shop manually",
                }
            )
    for run in latest_source_runs_by_source(source_runs):
        if str(run.get("status") or "") in {"failed", "degraded"}:
            source = str(run.get("source") or "")
            label = source_label(source)
            status = str(run.get("status") or "source")
            alerts.append(
                {
                    "id": f"alert:source:{source}",
                    "feed_type": "alert",
                    "kind": status,
                    "brand": label,
                    "title": source_health_title(source, status),
                    "title_zh": source_health_title(source, status, language="zh"),
                    "title_ja": source_health_title(source, status, language="ja"),
                    "use_localized_title": True,
                    "meta": str(run.get("error_message") or ""),
                    "time": str(run.get("checked_at") or ""),
                    "url": urls.get(source, ""),
                    "error_rate": run.get("error_rate", 0),
                    "latency_ms": run.get("latency_ms", 0),
                    "item_count": run.get("item_count", 0),
                    "reason_codes": ["source_health"],
                    "visual": visual_token("alert", label or "Source", status),
                }
            )
    return unique_cards(sort_cards(alerts))[:HOME_LINK_LIMIT]


def shop_event_card(row: dict[str, Any]) -> dict[str, Any]:
    title = str(row.get("title") or "")
    keywords = [str(keyword) for keyword in row.get("matched_keywords", [])] if isinstance(row.get("matched_keywords"), list) else []
    shop = str(row.get("shop_name") or "")
    platform = str(row.get("platform") or "")
    event_type = str(row.get("event_type") or "DROP")
    availability = str(row.get("availability") or "")
    price = str(row.get("price") or "")
    currency = str(row.get("currency") or "")
    url = str(row.get("purchase_url") or row.get("item_url") or "")
    return {
        "id": f"drop:{row.get('identity_key') or row.get('title_hash') or url or title}",
        "feed_type": "drop",
        "kind": event_type.lower(),
        "type": event_type,
        "brand": shop,
        "shop": shop,
        "platform": platform,
        "item": title,
        "title": title,
        "title_zh": title_hint(title, "shop_news", " ".join(keywords), language="zh"),
        "title_ja": title_hint(title, "shop_news", " ".join(keywords), language="ja"),
        "meta": " · ".join(part for part in (shop, platform) if part),
        "time": str(row.get("observed_at") or row.get("created_at") or ""),
        "time_kind": "published",
        "url": url,
        "price": " ".join(part for part in (price, currency) if part),
        "availability": availability,
        "keywords": keywords,
        "urgency": str(row.get("priority") or priority_for_drop(keywords, availability)),
        "reason_codes": shop_event_reasons(event_type, keywords, availability),
        "source_context": " · ".join(part for part in (availability, price, currency) if part),
        "source_label": platform or shop,
        "sale_at": str(row.get("sale_at") or ""),
        "remind_at": str(row.get("remind_at") or ""),
        "purchase_url": url,
        "priority": str(row.get("priority") or ""),
        "visual": visual_token("drop", shop or "Shop", "shop_news", image_url=str(row.get("image_url") or "")),
        "cta": "Open shop manually",
    }


def shop_event_reasons(event_type: str, keywords: list[str], availability: str) -> list[str]:
    normalized_event = event_type.lower()
    reasons = ["new_shop_item" if normalized_event == "drop" else normalized_event]
    if keywords:
        reasons.append("keyword_match")
    if availability in {"in_stock", "available"}:
        reasons.append("stock_available")
    return reasons


def priority_for_drop(keywords: list[str], availability: str) -> str:
    lowered = {keyword.casefold() for keyword in keywords}
    if availability in {"in_stock", "available"} and lowered & {"jsk", "op", "予約", "preorder"}:
        return "high"
    return "medium"


def purchase_assist_alert(row: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(row.get("event_type") or "")
    availability = str(row.get("availability") or "")
    priority = str(row.get("priority") or "")
    title = str(row.get("title") or "")
    url = str(row.get("purchase_url") or row.get("item_url") or "")
    if row.get("sale_at") and row.get("remind_at"):
        kind = "sale_window"
        title_zh = f"发售窗口即将开始: {title}"
    elif event_type == "DROP" and priority == "high":
        kind = "high_priority_drop"
        title_zh = f"高优先级到货: {title}"
    elif event_type == "STOCK_CHANGED" and availability in {"in_stock", "available"}:
        kind = "stock_available"
        title_zh = f"库存可用: {title}"
    else:
        return None
    return {
        "id": f"alert:{kind}:{row.get('identity_key') or url or title}",
        "feed_type": "alert",
        "kind": kind,
        "brand": str(row.get("shop_name") or ""),
        "title": title,
        "title_zh": title_zh,
        "title_ja": title,
        "use_localized_title": True,
        "meta": str(row.get("platform") or ""),
        "time": str(row.get("remind_at") or row.get("observed_at") or row.get("created_at") or ""),
        "url": url,
        "reason_codes": [kind],
        "visual": visual_token("alert", str(row.get("shop_name") or "Shop"), kind),
        "cta": "Open shop manually",
    }


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


def market_alert_kind(alert: dict[str, Any]) -> str:
    raw_kind = str(alert.get("kind") or "market_alert")
    if raw_kind in {"sample_spike", "brand_heat"}:
        return "high_premium"
    return raw_kind


def market_alert_meta(alert: dict[str, Any]) -> str:
    return ""


def market_alert_title(alert: dict[str, Any], kind: str, language: str) -> str:
    raw_title = str(alert.get("item_name") or alert.get("title") or alert.get("alias") or "").strip()
    label = {
        "zh": {
            "high_premium": "高溢价",
            "sample_gap": "样本不足",
        },
        "ja": {
            "high_premium": "高プレミア",
            "sample_gap": "サンプル不足",
        },
    }.get(language, {}).get(kind, kind)
    if raw_title:
        return f"{label}: {raw_title}"
    return label


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
    time_value, time_kind = feed_time(row)
    brand = brand_label(source, str(row.get("title") or ""))
    resolved_kind = kind or str(row.get("event_type") or status or feed_type)
    price = metadata_text(row, "price")
    image_url = metadata_text(row, "image_url")
    source_context = metadata_text(row, "context")
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
        "title_zh": title_hint(str(row.get("title") or ""), status, source_context, language="zh"),
        "title_ja": title_hint(str(row.get("title") or ""), status, source_context, language="ja"),
        "meta": source_label(source),
        "time": time_value,
        "time_kind": time_kind,
        "url": str(row.get("url") or ""),
        "status": status,
        "status_label": localized_status_label(status),
        "price": price,
        "source_context": source_context,
        "source_label": source_label(source),
        "visual": visual_token(feed_type, brand, status, image_url=image_url),
    }


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
    context = " ".join(part for part in (str(card.get("source_context") or ""), " ".join(keywords)) if part)
    card["title_zh"] = title_hint(signal.item.title, str(card.get("status") or ""), context, language="zh")
    card["title_ja"] = title_hint(signal.item.title, str(card.get("status") or ""), context, language="ja")
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


def source_health_title(source: str, status: str, language: str = "en") -> str:
    label = source_label(source)
    localized = {
        "en": {
            "failed": "source unavailable",
            "degraded": "source degraded",
        },
        "zh": {
            "failed": "来源不可用",
            "degraded": "来源状态下降",
        },
        "ja": {
            "failed": "取得不可",
            "degraded": "取得状態低下",
        },
    }
    status_text = localized.get(language, localized["en"]).get(status)
    if status_text is None:
        status_text = f"source {status}".strip() if language == "en" else status
    return " ".join(part for part in (label, status_text) if part)


def feed_time(row: dict[str, Any]) -> tuple[str, str]:
    published_at = str(row.get("published_at") or "")
    if published_at:
        return published_at, "published"
    return "", ""


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
        "high_premium": "高騰 / 高溢价",
        "sample_gap": "サンプル不足 / 样本不足",
        "rising": "上昇 / 上升",
        "stable": "安定 / 稳定",
        "cooling": "下落 / 降温",
    }
    return labels.get(kind, kind)


def title_hint(title: str, status: str, context: str = "", language: str = "zh") -> str:
    haystack = f"{title} {context}".lower()
    labels = {
        "zh": {
            "new_arrival": "新作",
            "preorder": "预约",
            "restock": "再贩",
            "shop_news": "店铺上新",
            "notice": "通知",
            "sale_start": "开始贩售",
            "arrival": "到货",
            "jsk": "JSK 吊带裙",
            "op": "OP 连衣裙",
            "blouse": "衬衫",
            "headwear": "发饰",
            "bag": "包袋",
            "accessory": "配饰",
            "skirt": "半裙",
            "outer": "外套",
        },
        "ja": {
            "new_arrival": "新作",
            "preorder": "予約",
            "restock": "再入荷",
            "shop_news": "ショップ入荷",
            "notice": "お知らせ",
            "sale_start": "販売開始",
            "arrival": "入荷",
            "jsk": "JSK",
            "op": "OP",
            "blouse": "ブラウス",
            "headwear": "ヘアアクセ",
            "bag": "バッグ",
            "accessory": "アクセサリー",
            "skirt": "スカート",
            "outer": "アウター",
        },
    }
    lang_labels = labels.get(language, labels["zh"])
    hints: list[str] = []
    status_key = status if status in {"new_arrival", "preorder", "restock", "shop_news"} else ""
    if status_key:
        hints.append(lang_labels[status_key])
    token_groups = (
        ("preorder", ("preorder", "pre-order", "reservation", "予約", "ご予約", "受注", "预订", "预约")),
        ("restock", ("restock", "再入荷", "再販", "再贩")),
        ("new_arrival", ("new arrival", "new item", "新作")),
        ("sale_start", ("販売開始", "発売", "发售")),
        ("arrival", ("入荷", "到货")),
        ("notice", ("お知らせ", "news", "information", "通知")),
        ("jsk", ("jsk", "ジャンパースカート", "吊带裙")),
        ("op", (" op", "op ", "onepiece", "one-piece", "ワンピース", "连衣裙")),
        ("blouse", ("blouse", "ブラウス", "衬衫")),
        ("headwear", ("カチューシャ", "ヘッドドレス", "head bow", "headbow", "发箍", "发饰")),
        ("bag", ("pochette", "bag", "バッグ", "包")),
        ("accessory", ("accessory", "アクセサリー", "配饰")),
        ("skirt", ("skirt", "スカート", "半裙")),
        ("outer", ("coat", "jacket", "ケープ", "コート", "外套")),
    )
    padded_haystack = f" {haystack} "
    for key, tokens in token_groups:
        if key == "skirt" and lang_labels["jsk"] in hints:
            continue
        if any(token.lower() in padded_haystack for token in tokens):
            hints.append(lang_labels[key])
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
