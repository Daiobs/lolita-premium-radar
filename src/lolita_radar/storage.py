from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .models import EventType, RadarEvent, RadarItem, item_content_hash, utc_now_iso


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
            item_count INTEGER NOT NULL DEFAULT 0,
            event_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT ''
        );
        """
    )
    ensure_column(connection, "items", "content_hash", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "events", "content_hash", "TEXT")
    ensure_column(connection, "events", "previous_content_hash", "TEXT")
    ensure_column(connection, "source_runs", "status", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "source_runs", "error_rate", "REAL NOT NULL DEFAULT 0")
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
    return {"items": item_count, "events": event_count}


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
) -> None:
    item_total = max(0, int(item_count))
    event_total = max(0, int(event_count))
    connection.execute(
        """
        INSERT INTO source_runs (
            source, checked_at, ok, status, error_rate, item_count, event_count, error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source,
            checked_at or utc_now_iso(),
            1 if ok else 0,
            normalize_source_run_status(ok, status),
            normalize_error_rate(error_rate),
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


def list_source_runs(connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT source, checked_at, ok, status, error_rate, item_count, event_count, error_message
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
        SELECT source, checked_at, ok, status, error_rate, item_count, event_count, error_message
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
