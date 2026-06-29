from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .models import EventType, RadarEvent, RadarItem, utc_now_iso


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
            created_at TEXT NOT NULL
        );
        """
    )
    connection.commit()


def diff_and_store(connection: sqlite3.Connection, items: Iterable[RadarItem]) -> list[RadarEvent]:
    events: list[RadarEvent] = []
    for item in items:
        event = upsert_item(connection, item)
        if event is not None:
            events.append(event)
    connection.commit()
    return events


def upsert_item(connection: sqlite3.Connection, item: RadarItem) -> RadarEvent | None:
    now = utc_now_iso()
    row = connection.execute(
        "SELECT * FROM items WHERE item_hash = ?",
        (item.identity_hash,),
    ).fetchone()
    metadata_json = json.dumps(item.metadata, ensure_ascii=False, sort_keys=True)
    if row is None:
        connection.execute(
            """
            INSERT INTO items (
                source, item_hash, title, url, status, published_at,
                content, metadata_json, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.source,
                item.identity_hash,
                item.title,
                item.url,
                item.status.value,
                item.published_at,
                item.content,
                metadata_json,
                now,
                now,
            ),
        )
        event = RadarEvent(source=item.source, event_type=EventType.NEW_ITEM, item=item, created_at=now)
        insert_event(connection, event)
        return event

    previous_title = str(row["title"])
    previous_status = str(row["status"])
    changed = previous_title != item.title or previous_status != item.status.value
    connection.execute(
        """
        UPDATE items
        SET title = ?, url = ?, status = ?, published_at = ?,
            content = ?, metadata_json = ?, last_seen_at = ?
        WHERE item_hash = ?
        """,
        (
            item.title,
            item.url,
            item.status.value,
            item.published_at,
            item.content,
            metadata_json,
            now,
            item.identity_hash,
        ),
    )
    if not changed:
        return None
    event = RadarEvent(
        source=item.source,
        event_type=EventType.UPDATE,
        item=item,
        previous_title=previous_title,
        previous_status=previous_status,
        created_at=now,
    )
    insert_event(connection, event)
    return event


def insert_event(connection: sqlite3.Connection, event: RadarEvent) -> None:
    connection.execute(
        """
        INSERT INTO events (
            source, item_hash, event_type, title, url, status,
            previous_title, previous_status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            event.created_at or utc_now_iso(),
        ),
    )


def list_items(connection: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            source, item_hash, title, url, status, published_at,
            first_seen_at, last_seen_at
        FROM items
        ORDER BY last_seen_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def list_events(connection: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            source, item_hash, event_type, title, url, status,
            previous_title, previous_status, created_at
        FROM events
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def storage_counts(connection: sqlite3.Connection) -> dict[str, int]:
    item_count = int(connection.execute("SELECT COUNT(*) AS count FROM items").fetchone()["count"])
    event_count = int(connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"])
    return {"items": item_count, "events": event_count}
