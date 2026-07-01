from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .models import (
    EventType,
    MarketSample,
    RadarEvent,
    RadarItem,
    ShopEvent,
    ShopEventType,
    ShopItem,
    item_content_hash,
    shop_item_identity_key,
    title_hash,
    utc_now_iso,
)


SOURCE_RUN_STATUSES = {"ok", "failed", "degraded"}


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    initialize(connection)
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            item_hash TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            published_at TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            item_hash TEXT NOT NULL,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            previous_title TEXT NOT NULL DEFAULT '',
            previous_status TEXT NOT NULL DEFAULT '',
            content_hash TEXT,
            previous_content_hash TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            ok INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT '',
            error_rate REAL NOT NULL DEFAULT 0,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            item_count INTEGER NOT NULL DEFAULT 0,
            event_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS collector_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            collector_type TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            options_json TEXT NOT NULL DEFAULT '{}',
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            degraded INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS collector_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT NOT NULL,
            collector_type TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            ok INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT '',
            latency_ms INTEGER NOT NULL DEFAULT 0,
            item_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS shop_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_name TEXT NOT NULL,
            platform TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            options_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(shop_name, platform)
        );

        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity_key TEXT NOT NULL UNIQUE,
            title_hash TEXT NOT NULL,
            shop_name TEXT NOT NULL,
            platform TEXT NOT NULL,
            title TEXT NOT NULL,
            price TEXT NOT NULL DEFAULT '',
            currency TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            item_url TEXT NOT NULL DEFAULT '',
            availability TEXT NOT NULL DEFAULT '',
            matched_keywords_json TEXT NOT NULL DEFAULT '[]',
            observed_at TEXT NOT NULL DEFAULT '',
            sale_at TEXT NOT NULL DEFAULT '',
            remind_at TEXT NOT NULL DEFAULT '',
            purchase_url TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shop_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            identity_key TEXT NOT NULL,
            title_hash TEXT NOT NULL,
            shop_name TEXT NOT NULL,
            platform TEXT NOT NULL,
            title TEXT NOT NULL,
            price TEXT NOT NULL DEFAULT '',
            previous_price TEXT NOT NULL DEFAULT '',
            currency TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            item_url TEXT NOT NULL DEFAULT '',
            availability TEXT NOT NULL DEFAULT '',
            previous_availability TEXT NOT NULL DEFAULT '',
            matched_keywords_json TEXT NOT NULL DEFAULT '[]',
            observed_at TEXT NOT NULL DEFAULT '',
            sale_at TEXT NOT NULL DEFAULT '',
            remind_at TEXT NOT NULL DEFAULT '',
            purchase_url TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS market_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL,
            query TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 0,
            options_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS market_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_key TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL,
            brand_alias TEXT NOT NULL,
            pattern TEXT NOT NULL,
            title TEXT NOT NULL,
            asking_price REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT '',
            condition TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL
        );
        """
    )
    ensure_column(connection, "items", "content_hash", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "events", "content_hash", "TEXT")
    ensure_column(connection, "events", "previous_content_hash", "TEXT")
    ensure_column(connection, "source_runs", "status", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "source_runs", "error_rate", "REAL NOT NULL DEFAULT 0")
    ensure_column(connection, "source_runs", "latency_ms", "INTEGER NOT NULL DEFAULT 0")
    connection.commit()


def ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
        for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if column not in columns:
        try:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


def diff_and_store(
    connection: sqlite3.Connection,
    items: Iterable[RadarItem],
    write_events: bool = True,
) -> list[RadarEvent]:
    events: list[RadarEvent] = []
    for item in items:
        event = upsert_item(connection, item, write_events=write_events)
        if event is not None:
            events.append(event)
    connection.commit()
    return events


def diff_and_store_shop_items(connection: sqlite3.Connection, items: Iterable[ShopItem]) -> list[ShopEvent]:
    events: list[ShopEvent] = []
    for item in items:
        event = upsert_shop_item(connection, item)
        if event is not None:
            events.append(event)
    connection.commit()
    return events


def upsert_shop_item(connection: sqlite3.Connection, item: ShopItem) -> ShopEvent | None:
    now = utc_now_iso()
    identity_key = item.identity_key
    item_title_hash = title_hash(item.title)
    keywords_json = json.dumps(list(item.matched_keywords), ensure_ascii=False)
    row = connection.execute("SELECT * FROM shop_items WHERE identity_key = ?", (identity_key,)).fetchone()
    if row is None:
        connection.execute(
            """
            INSERT INTO shop_items (
                identity_key, title_hash, shop_name, platform, title, price, currency,
                image_url, item_url, availability, matched_keywords_json, observed_at,
                sale_at, remind_at, purchase_url, priority, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            shop_item_values(identity_key, item_title_hash, item, keywords_json, now, now),
        )
        event = ShopEvent(event_type=ShopEventType.DROP, item=item, created_at=now)
        insert_shop_event(connection, event, identity_key=identity_key, item_title_hash=item_title_hash)
        return event

    previous_price = str(row["price"] or "")
    previous_availability = str(row["availability"] or "")
    connection.execute(
        """
        UPDATE shop_items
        SET title_hash = ?, shop_name = ?, platform = ?, title = ?, price = ?,
            currency = ?, image_url = ?, item_url = ?, availability = ?,
            matched_keywords_json = ?, observed_at = ?, sale_at = ?, remind_at = ?,
            purchase_url = ?, priority = ?, last_seen_at = ?
        WHERE identity_key = ?
        """,
        (
            item_title_hash,
            item.shop_name,
            item.platform,
            item.title,
            item.price,
            item.currency,
            item.image_url,
            item.item_url,
            item.availability,
            keywords_json,
            item.observed_at,
            item.sale_at,
            item.remind_at,
            item.purchase_url,
            item.priority,
            now,
            identity_key,
        ),
    )
    if previous_price != item.price:
        event = ShopEvent(
            event_type=ShopEventType.PRICE_CHANGED,
            item=item,
            previous_price=previous_price,
            previous_availability=previous_availability,
            created_at=now,
        )
        insert_shop_event(connection, event, identity_key=identity_key, item_title_hash=item_title_hash)
        return event
    if previous_availability != item.availability:
        event = ShopEvent(
            event_type=ShopEventType.STOCK_CHANGED,
            item=item,
            previous_price=previous_price,
            previous_availability=previous_availability,
            created_at=now,
        )
        insert_shop_event(connection, event, identity_key=identity_key, item_title_hash=item_title_hash)
        return event
    return None


def shop_item_values(
    identity_key: str,
    item_title_hash: str,
    item: ShopItem,
    keywords_json: str,
    first_seen_at: str,
    last_seen_at: str,
) -> tuple[Any, ...]:
    return (
        identity_key,
        item_title_hash,
        item.shop_name,
        item.platform,
        item.title,
        item.price,
        item.currency,
        item.image_url,
        item.item_url,
        item.availability,
        keywords_json,
        item.observed_at,
        item.sale_at,
        item.remind_at,
        item.purchase_url,
        item.priority,
        first_seen_at,
        last_seen_at,
    )


def insert_shop_event(
    connection: sqlite3.Connection,
    event: ShopEvent,
    identity_key: str | None = None,
    item_title_hash: str | None = None,
) -> None:
    item = event.item
    connection.execute(
        """
        INSERT INTO shop_events (
            event_type, identity_key, title_hash, shop_name, platform, title,
            price, previous_price, currency, image_url, item_url, availability,
            previous_availability, matched_keywords_json, observed_at,
            sale_at, remind_at, purchase_url, priority, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_type.value,
            identity_key or item.identity_key,
            item_title_hash or title_hash(item.title),
            item.shop_name,
            item.platform,
            item.title,
            item.price,
            event.previous_price,
            item.currency,
            item.image_url,
            item.item_url,
            item.availability,
            event.previous_availability,
            json.dumps(list(item.matched_keywords), ensure_ascii=False),
            item.observed_at,
            item.sale_at,
            item.remind_at,
            item.purchase_url or item.item_url,
            item.priority,
            event.created_at or utc_now_iso(),
        ),
    )


def insert_market_samples(connection: sqlite3.Connection, samples: Iterable[MarketSample]) -> int:
    count = 0
    for sample in samples:
        sample_key = market_sample_key(sample)
        connection.execute(
            """
            INSERT OR REPLACE INTO market_samples (
                sample_key, platform, brand_alias, pattern, title, asking_price,
                currency, condition, url, image_url, observed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sample_key,
                sample.platform,
                sample.brand_alias.upper(),
                sample.pattern,
                sample.title,
                float(sample.asking_price),
                sample.currency,
                sample.condition,
                sample.url,
                sample.image_url,
                sample.observed_at,
            ),
        )
        count += 1
    connection.commit()
    return count


def market_sample_key(sample: MarketSample) -> str:
    key = "|".join(
        (
            sample.platform.strip().lower(),
            sample.brand_alias.strip().lower(),
            sample.pattern.strip().lower(),
            sample.url.strip().lower() or sample.title.strip().lower(),
            sample.observed_at.strip(),
        )
    )
    return item_content_hash(key)


def list_shop_events(connection: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM shop_events
        ORDER BY
            CASE WHEN observed_at = '' THEN 1 ELSE 0 END,
            observed_at DESC,
            created_at DESC,
            id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [with_keyword_list(dict(row)) for row in rows]


def list_shop_items(connection: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM shop_items
        ORDER BY
            CASE WHEN observed_at = '' THEN 1 ELSE 0 END,
            observed_at DESC,
            last_seen_at DESC,
            id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [with_keyword_list(dict(row)) for row in rows]


def list_market_samples(connection: sqlite3.Connection, limit: int = 500) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT platform, brand_alias, pattern, title, asking_price, currency,
               condition, url, image_url, observed_at
        FROM market_samples
        ORDER BY observed_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def with_keyword_list(row: dict[str, Any]) -> dict[str, Any]:
    raw = str(row.pop("matched_keywords_json", "") or "[]")
    try:
        keywords = json.loads(raw)
    except json.JSONDecodeError:
        keywords = []
    row["matched_keywords"] = [str(keyword) for keyword in keywords] if isinstance(keywords, list) else []
    return row


def upsert_collector_job(
    connection: sqlite3.Connection,
    name: str,
    collector_type: str,
    url: str = "",
    enabled: bool = True,
    options: dict[str, Any] | None = None,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO collector_jobs (name, collector_type, url, enabled, options_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            collector_type = excluded.collector_type,
            url = excluded.url,
            enabled = excluded.enabled,
            options_json = excluded.options_json,
            updated_at = excluded.updated_at
        """,
        (name, collector_type, url, 1 if enabled else 0, json.dumps(options or {}, ensure_ascii=False), now),
    )
    connection.commit()


def list_collector_jobs(connection: sqlite3.Connection, enabled_only: bool = True) -> list[dict[str, Any]]:
    where = "WHERE enabled = 1" if enabled_only else ""
    rows = connection.execute(
        f"""
        SELECT name, collector_type, url, enabled, options_json, consecutive_failures, degraded, updated_at
        FROM collector_jobs
        {where}
        ORDER BY name ASC
        """
    ).fetchall()
    return [with_options(dict(row)) for row in rows]


def with_options(row: dict[str, Any]) -> dict[str, Any]:
    raw = str(row.pop("options_json", "") or "{}")
    try:
        options = json.loads(raw)
    except json.JSONDecodeError:
        options = {}
    row["options"] = options if isinstance(options, dict) else {}
    row["enabled"] = bool(row.get("enabled"))
    row["degraded"] = bool(row.get("degraded"))
    return row


def record_collector_run(
    connection: sqlite3.Connection,
    job_name: str,
    collector_type: str,
    ok: bool,
    status: str = "",
    latency_ms: int | float = 0,
    item_count: int = 0,
    error_message: str = "",
    checked_at: str | None = None,
) -> None:
    normalized_status = normalize_source_run_status(ok, status)
    connection.execute(
        """
        INSERT INTO collector_runs (
            job_name, collector_type, checked_at, ok, status, latency_ms, item_count, error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_name,
            collector_type,
            checked_at or utc_now_iso(),
            1 if ok else 0,
            normalized_status,
            normalize_latency_ms(latency_ms),
            max(0, int(item_count)),
            str(error_message or ""),
        ),
    )
    row = connection.execute(
        "SELECT consecutive_failures FROM collector_jobs WHERE name = ?",
        (job_name,),
    ).fetchone()
    if row is not None:
        failures = 0 if ok else int(row["consecutive_failures"] or 0) + 1
        connection.execute(
            """
            UPDATE collector_jobs
            SET consecutive_failures = ?, degraded = ?, updated_at = ?
            WHERE name = ?
            """,
            (failures, 1 if failures >= 2 or normalized_status == "degraded" else 0, utc_now_iso(), job_name),
        )
    connection.commit()


def list_collector_runs(connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT job_name, collector_type, checked_at, ok, status, latency_ms, item_count, error_message
        FROM collector_runs
        ORDER BY checked_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [{**dict(row), "ok": bool(row["ok"])} for row in rows]


def upsert_item(connection: sqlite3.Connection, item: RadarItem, write_events: bool = True) -> RadarEvent | None:
    now = utc_now_iso()
    row = connection.execute(
        "SELECT * FROM items WHERE item_hash = ?",
        (item.identity_hash,),
    ).fetchone()
    metadata_json = json.dumps(item.metadata, ensure_ascii=False, sort_keys=True)
    content_hash = item.content_hash
    if row is None:
        connection.execute(
            """
            INSERT INTO items (
                source, item_hash, title, url, status, published_at,
                content, content_hash, metadata_json, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.source,
                item.identity_hash,
                item.title,
                item.url,
                item.status.value,
                item.published_at,
                item.content,
                content_hash,
                metadata_json,
                now,
                now,
            ),
        )
        if not write_events:
            return None
        event = RadarEvent(source=item.source, event_type=EventType.NEW_ITEM, item=item, created_at=now)
        insert_event(connection, event)
        return event

    previous_title = str(row["title"])
    previous_status = str(row["status"])
    previous_content_hash = stored_content_hash(row)
    title_or_status_changed = previous_title != item.title or previous_status != item.status.value
    content_changed = previous_content_hash != content_hash
    connection.execute(
        """
        UPDATE items
        SET title = ?, url = ?, status = ?, published_at = ?,
            content = ?, content_hash = ?, metadata_json = ?, last_seen_at = ?
        WHERE item_hash = ?
        """,
        (
            item.title,
            item.url,
            item.status.value,
            item.published_at,
            item.content,
            content_hash,
            metadata_json,
            now,
            item.identity_hash,
        ),
    )
    if not title_or_status_changed and not content_changed:
        return None
    if not write_events:
        return None
    if content_changed and not title_or_status_changed and not bool(item.metadata.get("content_change_alert", True)):
        return None
    event_type = EventType.UPDATE if title_or_status_changed else EventType.CONTENT_CHANGED
    event = RadarEvent(
        source=item.source,
        event_type=event_type,
        item=item,
        previous_title=previous_title,
        previous_status=previous_status,
        previous_content_hash=previous_content_hash,
        created_at=now,
    )
    insert_event(connection, event)
    return event


def insert_event(connection: sqlite3.Connection, event: RadarEvent) -> None:
    connection.execute(
        """
        INSERT INTO events (
            source, item_hash, event_type, title, url, status,
            previous_title, previous_status, content_hash, previous_content_hash, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.source,
            event.item.identity_hash,
            event.event_type.value,
            event.item.title,
            event.item.url,
            event.item.status.value,
            event.previous_title,
            event.previous_status,
            event.item.content_hash,
            event.previous_content_hash,
            event.created_at or utc_now_iso(),
        ),
    )


def stored_content_hash(row: sqlite3.Row) -> str:
    value = str(row["content_hash"] or "")
    if value:
        return value
    return item_content_hash(str(row["content"] or ""))


def list_items(connection: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            source, item_hash, title, url, status, published_at,
            content_hash, metadata_json, first_seen_at, last_seen_at
        FROM items
        ORDER BY
            CASE WHEN published_at = '' THEN 1 ELSE 0 END,
            published_at DESC,
            last_seen_at DESC,
            id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [with_metadata(dict(row)) for row in rows]


def list_events(connection: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            events.source, events.item_hash, event_type, events.title, events.url, events.status,
            previous_title, previous_status, events.content_hash, previous_content_hash,
            created_at, items.published_at, items.metadata_json
        FROM events
        LEFT JOIN items ON items.item_hash = events.item_hash
        ORDER BY
            CASE WHEN items.published_at IS NULL OR items.published_at = '' THEN 1 ELSE 0 END,
            items.published_at DESC,
            created_at DESC,
            events.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [with_metadata(dict(row)) for row in rows]


def with_metadata(row: dict[str, Any]) -> dict[str, Any]:
    raw = str(row.pop("metadata_json", "") or "{}")
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError:
        metadata = {}
    row["metadata"] = metadata if isinstance(metadata, dict) else {}
    return row


def storage_counts(connection: sqlite3.Connection) -> dict[str, int]:
    item_count = int(connection.execute("SELECT COUNT(*) AS count FROM items").fetchone()["count"])
    event_count = int(connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"])
    shop_item_count = int(connection.execute("SELECT COUNT(*) AS count FROM shop_items").fetchone()["count"])
    shop_event_count = int(connection.execute("SELECT COUNT(*) AS count FROM shop_events").fetchone()["count"])
    market_sample_count = int(connection.execute("SELECT COUNT(*) AS count FROM market_samples").fetchone()["count"])
    collector_run_count = int(connection.execute("SELECT COUNT(*) AS count FROM collector_runs").fetchone()["count"])
    return {
        "items": item_count,
        "events": event_count,
        "shop_items": shop_item_count,
        "shop_events": shop_event_count,
        "market_samples": market_sample_count,
        "collector_runs": collector_run_count,
    }


def count_items_for_sources(connection: sqlite3.Connection, sources: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM items WHERE source = ?",
            (source,),
        ).fetchone()
        counts[str(source)] = int(row["count"])
    return counts


def record_source_run(
    connection: sqlite3.Connection,
    source: str,
    ok: bool,
    item_count: int = 0,
    event_count: int = 0,
    error_message: str = "",
    checked_at: str | None = None,
    status: str = "",
    error_rate: float = 0.0,
    latency_ms: int | float = 0,
) -> None:
    item_total = max(0, int(item_count))
    event_total = max(0, int(event_count))
    connection.execute(
        """
        INSERT INTO source_runs (
            source, checked_at, ok, status, error_rate, latency_ms, item_count, event_count, error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source,
            checked_at or utc_now_iso(),
            1 if ok else 0,
            normalize_source_run_status(ok, status),
            normalize_error_rate(error_rate),
            normalize_latency_ms(latency_ms),
            item_total,
            event_total,
            str(error_message or ""),
        ),
    )


def normalize_source_run_status(ok: bool, status: object) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in SOURCE_RUN_STATUSES and (ok or normalized != "ok"):
        return normalized
    return "ok" if ok else "failed"


def normalize_error_rate(value: object) -> float:
    try:
        rate = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(rate):
        return 0.0
    return max(0.0, min(1.0, rate))


def normalize_latency_ms(value: object) -> int:
    try:
        latency = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(latency):
        return 0
    return max(0, int(round(latency)))


def list_source_runs(connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT source, checked_at, ok, status, error_rate, latency_ms, item_count, event_count, error_message
        FROM source_runs
        ORDER BY checked_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            **dict(row),
            "ok": bool(row["ok"]),
        }
        for row in rows
    ]


def list_latest_source_runs(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT source, checked_at, ok, status, error_rate, latency_ms, item_count, event_count, error_message
        FROM source_runs
        WHERE id IN (
            SELECT MAX(id)
            FROM source_runs
            GROUP BY source
        )
        ORDER BY source ASC
        """
    ).fetchall()
    return [
        {
            **dict(row),
            "ok": bool(row["ok"]),
        }
        for row in rows
    ]
