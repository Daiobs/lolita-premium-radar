from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Optional


SECONDHAND_SOURCES = {"xianyu", "mercari", "wunderwelt", "closet_child", "rakuma", "yahoo_auction"}
DOMESTIC_SALE_SOURCES = {"taobao", "proxy", "daigou"}
DEFAULT_JPY_TO_CNY = 0.05
REVIEW_STATUSES = {"pending", "hit", "miss"}
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
PRICE_RE = re.compile(
    r"(?:[¥￥]\s*|RMB\s*|CNY\s*)(\d[\d,]*(?:\.\d{1,2})?)"
    r"|(\d[\d,]*(?:\.\d{1,2})?)\s*(?:元|RMB|CNY)",
    re.IGNORECASE,
)
JPY_PRICE_RE = re.compile(
    r"(?:JPY\s*|[¥￥]\s*)(\d[\d,]*)"
    r"|(\d[\d,]*)\s*(?:円|yen|JPY)",
    re.IGNORECASE,
)
ISO_DATE_RE = re.compile(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})")
JP_DATE_RE = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日")


@dataclass(frozen=True)
class RadarItem:
    id: int
    brand_name: str
    series_name: str
    item_name: str
    category: str
    colorway: str
    original_price_jpy: Optional[float]
    jpy_to_cny: float
    japan_domestic_shipping_cny: float
    international_shipping_cny: float
    proxy_fee_cny: float
    tax_or_buffer_cny: float
    release_signal_score: float
    source_url: str
    release_date: str


@dataclass(frozen=True)
class RadarResult:
    item_id: int
    label: str
    brand_name: str
    series_name: str
    source_url: str
    release_date: str
    landed_cost_cny: Optional[float]
    market_median_cny: Optional[float]
    premium_cny: Optional[float]
    premium_ratio: Optional[float]
    sample_count: int
    domestic_median_cny: Optional[float]
    domestic_markup_cny: Optional[float]
    domestic_markup_ratio: Optional[float]
    domestic_sample_count: int
    premium_score: float
    liquidity_score: float
    release_signal_score: float
    confidence_score: float
    attention_score: float
    priority_band: str


@dataclass(frozen=True)
class ReleaseWatchItem:
    item_id: int
    label: str
    brand_name: str
    series_name: str
    source_url: str
    release_date: str
    days_until: Optional[int]
    release_status: str
    attention_score: float
    priority_band: str


@dataclass(frozen=True)
class RadarCollectionTask:
    item_id: int
    label: str
    task_type: str
    title: str
    reason: str
    action_hint: str
    action_type: str
    action_label: str
    suggested_source_type: str
    priority_score: float
    priority_band: str


@dataclass(frozen=True)
class RadarWatchRecommendation:
    item_id: int
    label: str
    reason: str
    release_date: str
    source_url: str
    suggested_price_max: Optional[float]
    priority_score: float
    priority_band: str


@dataclass(frozen=True)
class RadarReviewSummary:
    item_id: int
    item_label: str
    review_status: str
    observed_price_cny: Optional[float]
    observed_premium_ratio: Optional[float]
    predicted_premium_ratio: Optional[float]
    predicted_attention_score: Optional[float]
    review_window_days: Optional[int]
    reviewed_at: str
    notes: str


@dataclass(frozen=True)
class RadarReviewStats:
    total_count: int
    reviewed_count: int
    pending_count: int
    hit_count: int
    miss_count: int
    hit_rate: Optional[float]
    average_observed_premium_ratio: Optional[float]
    average_predicted_premium_ratio: Optional[float]


@dataclass(frozen=True)
class RadarAggregate:
    group_type: str
    name: str
    brand_name: str
    series_name: str
    item_count: int
    secondhand_sample_count: int
    domestic_sample_count: int
    median_premium_ratio: Optional[float]
    median_domestic_markup_ratio: Optional[float]
    average_attention_score: float
    max_attention_score: float
    attention_score: float
    priority_band: str


@dataclass(frozen=True)
class RadarItemSummary:
    id: int
    label: str
    brand_name: str
    series_name: str
    item_name: str
    category: str
    colorway: str
    original_price_jpy: Optional[float]
    jpy_to_cny: float
    japan_domestic_shipping_cny: float
    international_shipping_cny: float
    proxy_fee_cny: float
    tax_or_buffer_cny: float
    release_signal_score: float
    source_url: str
    release_date: str


@dataclass(frozen=True)
class RadarImportResult:
    rows_read: int
    items_created: int
    samples_created: int


@dataclass(frozen=True)
class PriceSampleSummary:
    id: int
    item_id: int
    item_label: str
    source_type: str
    source_url: str
    title: str
    listed_price_cny: Optional[float]
    sold_price_cny: Optional[float]
    effective_price_cny: Optional[float]
    condition: str
    listing_status: str
    confidence: float
    captured_at: str
    notes: str


@dataclass(frozen=True)
class PriceSampleCandidate:
    source_type: str
    source_url: str
    title: str
    listed_price_cny: Optional[float]
    sold_price_cny: Optional[float]
    listing_status: str
    confidence: float


@dataclass(frozen=True)
class ReleaseItemCandidate:
    brand_name: str
    series_name: str
    item_name: str
    category: str
    colorway: str
    original_price_jpy: float
    source_url: str
    release_date: str
    raw_title: str


def connect_radar_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    initialize_radar_db(connection)
    return connection


def initialize_radar_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            aliases TEXT NOT NULL DEFAULT '',
            official_site_url TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS radar_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_id INTEGER REFERENCES brands(id),
            series_name TEXT NOT NULL DEFAULT '',
            item_name TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            colorway TEXT NOT NULL DEFAULT '',
            size TEXT NOT NULL DEFAULT '',
            original_price_jpy REAL,
            jpy_to_cny REAL NOT NULL DEFAULT 0.05,
            japan_domestic_shipping_cny REAL NOT NULL DEFAULT 0,
            international_shipping_cny REAL NOT NULL DEFAULT 0,
            proxy_fee_cny REAL NOT NULL DEFAULT 0,
            tax_or_buffer_cny REAL NOT NULL DEFAULT 0,
            source_url TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            release_date TEXT NOT NULL DEFAULT '',
            release_signal_score REAL NOT NULL DEFAULT 50,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS price_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES radar_items(id) ON DELETE CASCADE,
            source_type TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            listed_price_cny REAL,
            sold_price_cny REAL,
            condition TEXT NOT NULL DEFAULT '',
            listing_status TEXT NOT NULL DEFAULT 'unknown',
            captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            confidence REAL NOT NULL DEFAULT 0.7,
            notes TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS radar_reviews (
            item_id INTEGER PRIMARY KEY REFERENCES radar_items(id) ON DELETE CASCADE,
            review_status TEXT NOT NULL DEFAULT 'pending',
            observed_price_cny REAL,
            observed_premium_ratio REAL,
            predicted_premium_ratio REAL,
            predicted_attention_score REAL,
            review_window_days INTEGER,
            reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notes TEXT NOT NULL DEFAULT ''
        );
        """
    )
    connection.commit()


def upsert_brand(
    connection: sqlite3.Connection,
    name: str,
    aliases: str = "",
    official_site_url: str = "",
    notes: str = "",
) -> int:
    connection.execute(
        """
        INSERT INTO brands (name, aliases, official_site_url, notes)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            aliases = excluded.aliases,
            official_site_url = excluded.official_site_url,
            notes = excluded.notes
        """,
        (name, aliases, official_site_url, notes),
    )
    row = connection.execute("SELECT id FROM brands WHERE name = ?", (name,)).fetchone()
    connection.commit()
    return int(row["id"])


def create_radar_item(
    connection: sqlite3.Connection,
    brand_name: str,
    series_name: str,
    item_name: str,
    category: str,
    colorway: str = "",
    original_price_jpy: Optional[float] = None,
    jpy_to_cny: float = DEFAULT_JPY_TO_CNY,
    japan_domestic_shipping_cny: float = 0,
    international_shipping_cny: float = 0,
    proxy_fee_cny: float = 0,
    tax_or_buffer_cny: float = 0,
    release_signal_score: float = 50,
    source_url: str = "",
    image_url: str = "",
    release_date: str = "",
    notes: str = "",
) -> int:
    brand_id = upsert_brand(connection, brand_name)
    cursor = connection.execute(
        """
        INSERT INTO radar_items (
            brand_id, series_name, item_name, category, colorway,
            original_price_jpy, jpy_to_cny, japan_domestic_shipping_cny,
            international_shipping_cny, proxy_fee_cny, tax_or_buffer_cny,
            release_signal_score, source_url, image_url, release_date, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            brand_id,
            series_name,
            item_name,
            category,
            colorway,
            original_price_jpy,
            jpy_to_cny,
            japan_domestic_shipping_cny,
            international_shipping_cny,
            proxy_fee_cny,
            tax_or_buffer_cny,
            release_signal_score,
            source_url,
            image_url,
            release_date,
            notes,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def update_radar_item(
    connection: sqlite3.Connection,
    item_id: int,
    brand_name: str,
    series_name: str,
    item_name: str,
    category: str,
    colorway: str = "",
    original_price_jpy: Optional[float] = None,
    jpy_to_cny: float = DEFAULT_JPY_TO_CNY,
    japan_domestic_shipping_cny: float = 0,
    international_shipping_cny: float = 0,
    proxy_fee_cny: float = 0,
    tax_or_buffer_cny: float = 0,
    release_signal_score: float = 50,
    source_url: str = "",
    image_url: str = "",
    release_date: str = "",
    notes: str = "",
) -> bool:
    existing = connection.execute("SELECT id FROM radar_items WHERE id = ?", (item_id,)).fetchone()
    if existing is None:
        return False
    brand_id = upsert_brand(connection, brand_name)
    cursor = connection.execute(
        """
        UPDATE radar_items
        SET brand_id = ?,
            series_name = ?,
            item_name = ?,
            category = ?,
            colorway = ?,
            original_price_jpy = ?,
            jpy_to_cny = ?,
            japan_domestic_shipping_cny = ?,
            international_shipping_cny = ?,
            proxy_fee_cny = ?,
            tax_or_buffer_cny = ?,
            release_signal_score = ?,
            source_url = ?,
            image_url = ?,
            release_date = ?,
            notes = ?
        WHERE id = ?
        """,
        (
            brand_id,
            series_name,
            item_name,
            category,
            colorway,
            original_price_jpy,
            jpy_to_cny,
            japan_domestic_shipping_cny,
            international_shipping_cny,
            proxy_fee_cny,
            tax_or_buffer_cny,
            release_signal_score,
            source_url,
            image_url,
            release_date,
            notes,
            item_id,
        ),
    )
    connection.commit()
    return cursor.rowcount > 0


def delete_radar_item(connection: sqlite3.Connection, item_id: int) -> bool:
    cursor = connection.execute("DELETE FROM radar_items WHERE id = ?", (item_id,))
    connection.commit()
    return cursor.rowcount > 0


def find_radar_item_id(
    connection: sqlite3.Connection,
    brand_name: str,
    series_name: str,
    item_name: str,
    category: str,
    colorway: str = "",
) -> Optional[int]:
    row = connection.execute(
        """
        SELECT radar_items.id
        FROM radar_items
        JOIN brands ON brands.id = radar_items.brand_id
        WHERE brands.name = ?
          AND radar_items.series_name = ?
          AND radar_items.item_name = ?
          AND radar_items.category = ?
          AND radar_items.colorway = ?
        """,
        (brand_name, series_name, item_name, category, colorway),
    ).fetchone()
    return int(row["id"]) if row else None


def find_or_create_radar_item(
    connection: sqlite3.Connection,
    brand_name: str,
    series_name: str,
    item_name: str,
    category: str,
    colorway: str = "",
    original_price_jpy: Optional[float] = None,
    jpy_to_cny: float = DEFAULT_JPY_TO_CNY,
    japan_domestic_shipping_cny: float = 0,
    international_shipping_cny: float = 0,
    proxy_fee_cny: float = 0,
    tax_or_buffer_cny: float = 0,
    release_signal_score: float = 50,
    source_url: str = "",
    image_url: str = "",
    release_date: str = "",
    notes: str = "",
) -> tuple[int, bool]:
    existing_id = find_radar_item_id(
        connection,
        brand_name=brand_name,
        series_name=series_name,
        item_name=item_name,
        category=category,
        colorway=colorway,
    )
    if existing_id is not None:
        return existing_id, False
    item_id = create_radar_item(
        connection,
        brand_name=brand_name,
        series_name=series_name,
        item_name=item_name,
        category=category,
        colorway=colorway,
        original_price_jpy=original_price_jpy,
        jpy_to_cny=jpy_to_cny,
        japan_domestic_shipping_cny=japan_domestic_shipping_cny,
        international_shipping_cny=international_shipping_cny,
        proxy_fee_cny=proxy_fee_cny,
        tax_or_buffer_cny=tax_or_buffer_cny,
        release_signal_score=release_signal_score,
        source_url=source_url,
        image_url=image_url,
        release_date=release_date,
        notes=notes,
    )
    return item_id, True


def add_price_sample(
    connection: sqlite3.Connection,
    item_id: int,
    source_type: str,
    listed_price_cny: Optional[float] = None,
    sold_price_cny: Optional[float] = None,
    source_url: str = "",
    title: str = "",
    condition: str = "",
    listing_status: str = "unknown",
    confidence: float = 0.7,
    notes: str = "",
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO price_samples (
            item_id, source_type, source_url, title, listed_price_cny,
            sold_price_cny, condition, listing_status, confidence, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            source_type,
            source_url,
            title,
            listed_price_cny,
            sold_price_cny,
            condition,
            listing_status,
            confidence,
            notes,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def update_price_sample(
    connection: sqlite3.Connection,
    sample_id: int,
    item_id: int,
    source_type: str,
    listed_price_cny: Optional[float] = None,
    sold_price_cny: Optional[float] = None,
    source_url: str = "",
    title: str = "",
    condition: str = "",
    listing_status: str = "unknown",
    confidence: float = 0.7,
    notes: str = "",
) -> bool:
    existing = connection.execute("SELECT id FROM price_samples WHERE id = ?", (sample_id,)).fetchone()
    if existing is None:
        return False
    cursor = connection.execute(
        """
        UPDATE price_samples
        SET item_id = ?,
            source_type = ?,
            source_url = ?,
            title = ?,
            listed_price_cny = ?,
            sold_price_cny = ?,
            condition = ?,
            listing_status = ?,
            confidence = ?,
            notes = ?
        WHERE id = ?
        """,
        (
            item_id,
            source_type,
            source_url,
            title,
            listed_price_cny,
            sold_price_cny,
            condition,
            listing_status,
            confidence,
            notes,
            sample_id,
        ),
    )
    connection.commit()
    return cursor.rowcount > 0


def delete_price_sample(connection: sqlite3.Connection, sample_id: int) -> bool:
    cursor = connection.execute("DELETE FROM price_samples WHERE id = ?", (sample_id,))
    connection.commit()
    return cursor.rowcount > 0


def list_price_samples(
    connection: sqlite3.Connection,
    item_id: Optional[int] = None,
    limit: int = 200,
) -> list[PriceSampleSummary]:
    params: list[Any] = []
    where = ""
    if item_id is not None:
        where = "WHERE price_samples.item_id = ?"
        params.append(item_id)
    params.append(limit)
    rows = connection.execute(
        f"""
        SELECT
            price_samples.*,
            COALESCE(price_samples.sold_price_cny, price_samples.listed_price_cny) AS effective_price_cny,
            brands.name AS brand_name,
            radar_items.series_name,
            radar_items.item_name,
            radar_items.category,
            radar_items.colorway
        FROM price_samples
        JOIN radar_items ON radar_items.id = price_samples.item_id
        JOIN brands ON brands.id = radar_items.brand_id
        {where}
        ORDER BY price_samples.captured_at DESC, price_samples.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    summaries = []
    for row in rows:
        item = RadarItem(
            id=int(row["item_id"]),
            brand_name=str(row["brand_name"]),
            series_name=str(row["series_name"]),
            item_name=str(row["item_name"]),
            category=str(row["category"]),
            colorway=str(row["colorway"]),
            original_price_jpy=None,
            jpy_to_cny=DEFAULT_JPY_TO_CNY,
            japan_domestic_shipping_cny=0,
            international_shipping_cny=0,
            proxy_fee_cny=0,
            tax_or_buffer_cny=0,
            release_signal_score=50,
            source_url="",
            release_date="",
        )
        summaries.append(
            PriceSampleSummary(
                id=int(row["id"]),
                item_id=int(row["item_id"]),
                item_label=build_label(item),
                source_type=str(row["source_type"]),
                source_url=str(row["source_url"]),
                title=str(row["title"]),
                listed_price_cny=optional_float(row["listed_price_cny"]),
                sold_price_cny=optional_float(row["sold_price_cny"]),
                effective_price_cny=optional_float(row["effective_price_cny"]),
                condition=str(row["condition"]),
                listing_status=str(row["listing_status"]),
                confidence=float(row["confidence"]),
                captured_at=str(row["captured_at"]),
                notes=str(row["notes"]),
            )
        )
    return summaries


def parse_price_sample_candidates(
    text: str,
    source_type: str,
    default_status: str = "listed",
    confidence: float = 0.65,
) -> list[PriceSampleCandidate]:
    candidates = []
    seen = set()
    for raw_line in text.splitlines():
        line = clean_cell(raw_line)
        if not line:
            continue
        price = extract_price_from_text(line)
        if price is None:
            continue
        source_url = ""
        url_match = URL_RE.search(line)
        if url_match:
            source_url = url_match.group(0).rstrip(".,，。)")
            line = line.replace(url_match.group(0), " ")
        title = clean_candidate_title(PRICE_RE.sub(" ", line))
        if not title:
            title = "price sample"
        listing_status = infer_listing_status(line, default_status)
        sold_price = price if listing_status == "sold" else None
        listed_price = None if listing_status == "sold" else price
        key = (source_type, source_url, title, listed_price, sold_price)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            PriceSampleCandidate(
                source_type=source_type,
                source_url=source_url,
                title=title,
                listed_price_cny=listed_price,
                sold_price_cny=sold_price,
                listing_status=listing_status,
                confidence=confidence,
            )
        )
    return candidates


def add_candidates_as_samples(
    connection: sqlite3.Connection,
    item_id: int,
    candidates: Iterable[PriceSampleCandidate],
) -> list[int]:
    sample_ids = []
    for candidate in candidates:
        sample_ids.append(
            add_price_sample(
                connection,
                item_id=item_id,
                source_type=candidate.source_type,
                listed_price_cny=candidate.listed_price_cny,
                sold_price_cny=candidate.sold_price_cny,
                source_url=candidate.source_url,
                title=candidate.title,
                listing_status=candidate.listing_status,
                confidence=candidate.confidence,
            )
        )
    return sample_ids


def parse_release_item_candidates(
    text: str,
    brand_name: str,
    default_series_name: str = "",
    default_category: str = "",
    default_colorway: str = "",
    default_source_url: str = "",
    default_release_date: str = "",
) -> list[ReleaseItemCandidate]:
    candidates = []
    seen = set()
    fallback_release_date = clean_cell(default_release_date) or extract_release_date_from_text(text)
    for raw_line in text.splitlines():
        line = clean_cell(raw_line)
        if not line:
            continue
        price = extract_jpy_price_from_text(line)
        if price is None:
            continue
        source_url = extract_url_from_text(line) or clean_cell(default_source_url)
        line_without_url = URL_RE.sub(" ", line)
        line_release_date = extract_release_date_from_text(line_without_url) or fallback_release_date
        title = clean_release_title(line_without_url)
        if not title:
            continue
        category = clean_cell(default_category) or infer_category(title)
        colorway = clean_cell(default_colorway)
        key = (brand_name, default_series_name, title, category, colorway, price, source_url, line_release_date)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            ReleaseItemCandidate(
                brand_name=clean_cell(brand_name),
                series_name=clean_cell(default_series_name),
                item_name=title,
                category=category,
                colorway=colorway,
                original_price_jpy=price,
                source_url=source_url,
                release_date=line_release_date,
                raw_title=clean_cell(raw_line),
            )
        )
    return candidates


def add_release_candidates_as_items(
    connection: sqlite3.Connection,
    candidates: Iterable[ReleaseItemCandidate],
    jpy_to_cny: float = DEFAULT_JPY_TO_CNY,
    japan_domestic_shipping_cny: float = 0,
    international_shipping_cny: float = 0,
    proxy_fee_cny: float = 0,
    tax_or_buffer_cny: float = 0,
    release_signal_score: float = 70,
) -> tuple[list[int], int]:
    item_ids = []
    created_count = 0
    for candidate in candidates:
        item_id, created = find_or_create_radar_item(
            connection,
            brand_name=candidate.brand_name,
            series_name=candidate.series_name,
            item_name=candidate.item_name,
            category=candidate.category,
            colorway=candidate.colorway,
            original_price_jpy=candidate.original_price_jpy,
            jpy_to_cny=jpy_to_cny,
            japan_domestic_shipping_cny=japan_domestic_shipping_cny,
            international_shipping_cny=international_shipping_cny,
            proxy_fee_cny=proxy_fee_cny,
            tax_or_buffer_cny=tax_or_buffer_cny,
            release_signal_score=release_signal_score,
            source_url=candidate.source_url,
            release_date=candidate.release_date,
        )
        item_ids.append(item_id)
        if created:
            created_count += 1
    return item_ids, created_count


def list_radar_items(connection: sqlite3.Connection) -> list[RadarItemSummary]:
    rows = connection.execute(
        """
        SELECT
            radar_items.*,
            brands.name AS brand_name
        FROM radar_items
        JOIN brands ON brands.id = radar_items.brand_id
        ORDER BY brands.name, radar_items.series_name, radar_items.item_name, radar_items.colorway
        """
    ).fetchall()
    summaries = []
    for row in rows:
        summaries.append(item_summary_from_row(row))
    return summaries


def get_radar_item_summary(connection: sqlite3.Connection, item_id: int) -> RadarItemSummary:
    rows = connection.execute(
        """
        SELECT
            radar_items.*,
            brands.name AS brand_name
        FROM radar_items
        JOIN brands ON brands.id = radar_items.brand_id
        WHERE radar_items.id = ?
        """,
        (item_id,),
    ).fetchall()
    if not rows:
        raise ValueError(f"Radar item not found: {item_id}")
    return item_summary_from_row(rows[0])


def item_summary_from_row(row: sqlite3.Row) -> RadarItemSummary:
    item = row_to_item(row)
    return RadarItemSummary(
        id=item.id,
        label=build_label(item),
        brand_name=item.brand_name,
        series_name=item.series_name,
        item_name=item.item_name,
        category=item.category,
        colorway=item.colorway,
        original_price_jpy=item.original_price_jpy,
        jpy_to_cny=item.jpy_to_cny,
        japan_domestic_shipping_cny=item.japan_domestic_shipping_cny,
        international_shipping_cny=item.international_shipping_cny,
        proxy_fee_cny=item.proxy_fee_cny,
        tax_or_buffer_cny=item.tax_or_buffer_cny,
        release_signal_score=item.release_signal_score,
        source_url=item.source_url,
        release_date=item.release_date,
    )


def upsert_radar_review(
    connection: sqlite3.Connection,
    item_id: int,
    review_status: str = "pending",
    observed_price_cny: Optional[float] = None,
    review_window_days: Optional[int] = None,
    notes: str = "",
) -> RadarReviewSummary:
    status = normalize_review_status(review_status)
    if observed_price_cny is not None and observed_price_cny < 0:
        raise ValueError("observed_price_cny must be non-negative")
    if review_window_days is not None and review_window_days < 0:
        raise ValueError("review_window_days must be non-negative")

    result = analyze_item(connection, item_id)
    observed_premium_ratio = None
    if observed_price_cny is not None and result.landed_cost_cny:
        observed_premium_ratio = round_optional(
            (observed_price_cny - result.landed_cost_cny) / result.landed_cost_cny,
            digits=4,
        )

    connection.execute(
        """
        INSERT INTO radar_reviews (
            item_id, review_status, observed_price_cny, observed_premium_ratio,
            predicted_premium_ratio, predicted_attention_score,
            review_window_days, reviewed_at, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            review_status = excluded.review_status,
            observed_price_cny = excluded.observed_price_cny,
            observed_premium_ratio = excluded.observed_premium_ratio,
            predicted_premium_ratio = excluded.predicted_premium_ratio,
            predicted_attention_score = excluded.predicted_attention_score,
            review_window_days = excluded.review_window_days,
            reviewed_at = CURRENT_TIMESTAMP,
            notes = excluded.notes
        """,
        (
            item_id,
            status,
            observed_price_cny,
            observed_premium_ratio,
            result.premium_ratio,
            result.attention_score,
            review_window_days,
            clean_cell(notes),
        ),
    )
    connection.commit()
    review = get_radar_review(connection, item_id)
    if review is None:
        raise ValueError(f"Radar review not found after save: {item_id}")
    return review


def delete_radar_review(connection: sqlite3.Connection, item_id: int) -> bool:
    cursor = connection.execute("DELETE FROM radar_reviews WHERE item_id = ?", (item_id,))
    connection.commit()
    return cursor.rowcount > 0


def get_radar_review(connection: sqlite3.Connection, item_id: int) -> Optional[RadarReviewSummary]:
    rows = load_radar_review_rows(connection, "WHERE radar_reviews.item_id = ?", (item_id,))
    return review_summary_from_row(rows[0]) if rows else None


def list_radar_reviews(connection: sqlite3.Connection) -> list[RadarReviewSummary]:
    rows = load_radar_review_rows(connection)
    return [review_summary_from_row(row) for row in rows]


def load_radar_review_rows(
    connection: sqlite3.Connection,
    where: str = "",
    params: Iterable[Any] = (),
) -> list[sqlite3.Row]:
    return connection.execute(
        f"""
        SELECT
            radar_items.*,
            brands.name AS brand_name,
            radar_reviews.review_status,
            radar_reviews.observed_price_cny,
            radar_reviews.observed_premium_ratio,
            radar_reviews.predicted_premium_ratio,
            radar_reviews.predicted_attention_score,
            radar_reviews.review_window_days,
            radar_reviews.reviewed_at,
            radar_reviews.notes AS review_notes
        FROM radar_reviews
        JOIN radar_items ON radar_items.id = radar_reviews.item_id
        JOIN brands ON brands.id = radar_items.brand_id
        {where}
        ORDER BY radar_reviews.reviewed_at DESC, radar_reviews.item_id DESC
        """,
        tuple(params),
    ).fetchall()


def review_summary_from_row(row: sqlite3.Row) -> RadarReviewSummary:
    item = row_to_item(row)
    return RadarReviewSummary(
        item_id=item.id,
        item_label=build_label(item),
        review_status=str(row["review_status"]),
        observed_price_cny=optional_float(row["observed_price_cny"]),
        observed_premium_ratio=optional_float(row["observed_premium_ratio"]),
        predicted_premium_ratio=optional_float(row["predicted_premium_ratio"]),
        predicted_attention_score=optional_float(row["predicted_attention_score"]),
        review_window_days=optional_int(row["review_window_days"]),
        reviewed_at=str(row["reviewed_at"]),
        notes=str(row["review_notes"]),
    )


def summarize_radar_reviews(reviews: Iterable[RadarReviewSummary]) -> RadarReviewStats:
    review_list = list(reviews)
    hit_count = sum(1 for review in review_list if review.review_status == "hit")
    miss_count = sum(1 for review in review_list if review.review_status == "miss")
    pending_count = sum(1 for review in review_list if review.review_status == "pending")
    reviewed_count = hit_count + miss_count
    observed_ratios = [
        review.observed_premium_ratio
        for review in review_list
        if review.review_status in {"hit", "miss"} and review.observed_premium_ratio is not None
    ]
    predicted_ratios = [
        review.predicted_premium_ratio
        for review in review_list
        if review.review_status in {"hit", "miss"} and review.predicted_premium_ratio is not None
    ]
    return RadarReviewStats(
        total_count=len(review_list),
        reviewed_count=reviewed_count,
        pending_count=pending_count,
        hit_count=hit_count,
        miss_count=miss_count,
        hit_rate=round_optional(hit_count / reviewed_count, digits=4) if reviewed_count else None,
        average_observed_premium_ratio=round_optional(sum(observed_ratios) / len(observed_ratios), digits=4)
        if observed_ratios
        else None,
        average_predicted_premium_ratio=round_optional(sum(predicted_ratios) / len(predicted_ratios), digits=4)
        if predicted_ratios
        else None,
    )


def normalize_review_status(status: str) -> str:
    normalized = clean_cell(status).lower() or "pending"
    aliases = {
        "待观察": "pending",
        "观察中": "pending",
        "命中": "hit",
        "成功": "hit",
        "未命中": "miss",
        "失败": "miss",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in REVIEW_STATUSES:
        raise ValueError(f"Unsupported review_status: {status}")
    return normalized


def import_radar_csv(connection: sqlite3.Connection, csv_path: Path) -> RadarImportResult:
    rows_read = 0
    items_created = 0
    samples_created = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows_read += 1
            brand_name = require_text(row, "brand_name", rows_read)
            item_name = require_text(row, "item_name", rows_read)
            category = clean_cell(row.get("category"))
            item_id, created = find_or_create_radar_item(
                connection,
                brand_name=brand_name,
                series_name=clean_cell(row.get("series_name")),
                item_name=item_name,
                category=category,
                colorway=clean_cell(row.get("colorway")),
                original_price_jpy=optional_cell_float(row.get("original_price_jpy")),
                jpy_to_cny=optional_cell_float(row.get("jpy_to_cny")) or DEFAULT_JPY_TO_CNY,
                japan_domestic_shipping_cny=optional_cell_float(row.get("japan_domestic_shipping_cny")) or 0,
                international_shipping_cny=optional_cell_float(row.get("international_shipping_cny")) or 0,
                proxy_fee_cny=optional_cell_float(row.get("proxy_fee_cny")) or 0,
                tax_or_buffer_cny=optional_cell_float(row.get("tax_or_buffer_cny")) or 0,
                release_signal_score=optional_cell_float(row.get("release_signal_score")) or 50,
                source_url=clean_cell(row.get("item_source_url") or row.get("source_url")),
                image_url=clean_cell(row.get("image_url")),
                release_date=clean_cell(row.get("release_date")),
                notes=clean_cell(row.get("item_notes") or row.get("notes")),
            )
            if created:
                items_created += 1

            source_type = clean_cell(row.get("sample_source_type") or row.get("source_type"))
            listed_price = optional_cell_float(row.get("listed_price_cny"))
            sold_price = optional_cell_float(row.get("sold_price_cny"))
            if source_type and (listed_price is not None or sold_price is not None):
                add_price_sample(
                    connection,
                    item_id=item_id,
                    source_type=source_type,
                    listed_price_cny=listed_price,
                    sold_price_cny=sold_price,
                    source_url=clean_cell(row.get("sample_source_url") or row.get("price_source_url")),
                    title=clean_cell(row.get("sample_title") or row.get("title")),
                    condition=clean_cell(row.get("condition")),
                    listing_status=clean_cell(row.get("listing_status")) or "unknown",
                    confidence=optional_cell_float(row.get("confidence")) or 0.7,
                    notes=clean_cell(row.get("sample_notes")),
                )
                samples_created += 1

    return RadarImportResult(rows_read=rows_read, items_created=items_created, samples_created=samples_created)


def analyze_all(connection: sqlite3.Connection) -> list[RadarResult]:
    rows = connection.execute(
        """
        SELECT
            radar_items.*,
            brands.name AS brand_name
        FROM radar_items
        JOIN brands ON brands.id = radar_items.brand_id
        ORDER BY brands.name, radar_items.series_name, radar_items.item_name
        """
    ).fetchall()
    results = [analyze_item_row(connection, row) for row in rows]
    return sorted(results, key=lambda result: result.attention_score, reverse=True)


def analyze_aggregates(connection: sqlite3.Connection) -> list[RadarAggregate]:
    results = analyze_all(connection)
    groups: dict[tuple[str, str, str], list[RadarResult]] = {}
    for result in results:
        groups.setdefault(("brand", result.brand_name, ""), []).append(result)
        if result.series_name:
            groups.setdefault(("series", result.brand_name, result.series_name), []).append(result)

    aggregates = []
    for key, grouped_results in groups.items():
        aggregates.append(build_aggregate(key, grouped_results))
    return sorted(aggregates, key=lambda aggregate: aggregate.attention_score, reverse=True)


def build_aggregate(key: tuple[str, str, str], results: list[RadarResult]) -> RadarAggregate:
    group_type, brand_name, series_name = key
    attention_values = [result.attention_score for result in results]
    average_attention = sum(attention_values) / len(attention_values) if attention_values else 0
    max_attention = max(attention_values) if attention_values else 0
    aggregate_attention = round(average_attention * 0.6 + max_attention * 0.4, 2)
    premium_ratios = [result.premium_ratio for result in results if result.premium_ratio is not None]
    domestic_ratios = [result.domestic_markup_ratio for result in results if result.domestic_markup_ratio is not None]
    name = brand_name if group_type == "brand" else f"{brand_name} / {series_name}"
    return RadarAggregate(
        group_type=group_type,
        name=name,
        brand_name=brand_name,
        series_name=series_name,
        item_count=len(results),
        secondhand_sample_count=sum(result.sample_count for result in results),
        domestic_sample_count=sum(result.domestic_sample_count for result in results),
        median_premium_ratio=round_optional(median(premium_ratios), digits=4) if premium_ratios else None,
        median_domestic_markup_ratio=round_optional(median(domestic_ratios), digits=4) if domestic_ratios else None,
        average_attention_score=round(average_attention, 2),
        max_attention_score=round(max_attention, 2),
        attention_score=aggregate_attention,
        priority_band=priority_band(aggregate_attention),
    )


def analyze_item(connection: sqlite3.Connection, item_id: int) -> RadarResult:
    row = connection.execute(
        """
        SELECT
            radar_items.*,
            brands.name AS brand_name
        FROM radar_items
        JOIN brands ON brands.id = radar_items.brand_id
        WHERE radar_items.id = ?
        """,
        (item_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Radar item not found: {item_id}")
    return analyze_item_row(connection, row)


def analyze_item_row(connection: sqlite3.Connection, row: sqlite3.Row) -> RadarResult:
    item = row_to_item(row)
    landed_cost = calculate_landed_cost(item)
    samples = load_market_samples(connection, item.id)
    sample_prices = [sample["price"] for sample in samples]
    market_median = median(sample_prices) if sample_prices else None
    domestic_samples = load_domestic_sale_samples(connection, item.id)
    domestic_prices = [sample["price"] for sample in domestic_samples]
    domestic_median = median(domestic_prices) if domestic_prices else None
    premium_cny = None
    premium_ratio = None
    if landed_cost and market_median is not None:
        premium_cny = market_median - landed_cost
        premium_ratio = premium_cny / landed_cost
    domestic_markup_cny = None
    domestic_markup_ratio = None
    if landed_cost and domestic_median is not None:
        domestic_markup_cny = domestic_median - landed_cost
        domestic_markup_ratio = domestic_markup_cny / landed_cost

    premium_score_value = premium_score(premium_ratio)
    liquidity_score_value = liquidity_score(len(samples), samples)
    confidence_score_value = confidence_score(samples)
    release_signal = clamp(item.release_signal_score)
    attention = round(
        premium_score_value * 0.45
        + liquidity_score_value * 0.25
        + release_signal * 0.15
        + confidence_score_value * 0.15,
        2,
    )

    return RadarResult(
        item_id=item.id,
        label=build_label(item),
        brand_name=item.brand_name,
        series_name=item.series_name,
        source_url=item.source_url,
        release_date=item.release_date,
        landed_cost_cny=round_optional(landed_cost),
        market_median_cny=round_optional(market_median),
        premium_cny=round_optional(premium_cny),
        premium_ratio=round_optional(premium_ratio, digits=4),
        sample_count=len(samples),
        domestic_median_cny=round_optional(domestic_median),
        domestic_markup_cny=round_optional(domestic_markup_cny),
        domestic_markup_ratio=round_optional(domestic_markup_ratio, digits=4),
        domestic_sample_count=len(domestic_samples),
        premium_score=premium_score_value,
        liquidity_score=liquidity_score_value,
        release_signal_score=release_signal,
        confidence_score=confidence_score_value,
        attention_score=attention,
        priority_band=priority_band(attention),
    )


def list_release_watch(connection: sqlite3.Connection, today: Optional[date] = None) -> list[ReleaseWatchItem]:
    return build_release_watch(analyze_all(connection), today=today)


def list_collection_tasks(connection: sqlite3.Connection) -> list[RadarCollectionTask]:
    items = list_radar_items(connection)
    result_by_item_id = {result.item_id: result for result in analyze_all(connection)}
    tasks: list[RadarCollectionTask] = []
    for item in items:
        result = result_by_item_id.get(item.id)
        attention_boost = min(15, (result.attention_score if result else item.release_signal_score) * 0.15)
        if item.original_price_jpy is None:
            tasks.append(
                build_collection_task(
                    item=item,
                    task_type="original_price",
                    title="补原价",
                    reason="缺少 JPY 原价，无法计算到手成本和溢价率。",
                    action_hint="使用官方发售采集，或编辑款式填写原价与运费假设。",
                    score=90 + attention_boost,
                )
            )
        if not item.release_date:
            tasks.append(
                build_collection_task(
                    item=item,
                    task_type="release_date",
                    title="补发售日期",
                    reason="缺少发售日期，无法进入发售关注队列。",
                    action_hint="从品牌发售页复制日期，编辑款式或使用官方发售采集。",
                    score=55 + attention_boost,
                )
            )
        secondhand_count = result.sample_count if result else 0
        if secondhand_count < 3:
            tasks.append(
                build_collection_task(
                    item=item,
                    task_type="secondhand_samples",
                    title="补二手价样本",
                    reason=f"当前二手样本 {secondhand_count}/3，样本不足会降低溢价判断可信度。",
                    action_hint="在价格样本或粘贴商品行中补闲鱼、Mercari、Wunderwelt 等样本。",
                    score=(85 if secondhand_count == 0 else 65) + attention_boost,
                )
            )
        domestic_count = result.domestic_sample_count if result else 0
        if domestic_count == 0:
            tasks.append(
                build_collection_task(
                    item=item,
                    task_type="domestic_samples",
                    title="补淘宝/代购价",
                    reason="缺少淘宝、代购或国内售卖样本，无法比较国内加价。",
                    action_hint="在价格样本里选择 taobao、proxy 或 daigou，并填入在售价格。",
                    score=70 + attention_boost,
                )
            )
        if not item.source_url:
            tasks.append(
                build_collection_task(
                    item=item,
                    task_type="source_url",
                    title="补来源链接",
                    reason="缺少官方或档案来源链接，后续复核原价和发售信息不方便。",
                    action_hint="编辑款式填写官方发售页、品牌页或可信档案页 URL。",
                    score=35 + attention_boost,
                )
            )
    return sorted(tasks, key=lambda task: (-task.priority_score, task.label, task.task_type))


def list_watch_recommendations(
    connection: sqlite3.Connection,
    today: Optional[date] = None,
) -> list[RadarWatchRecommendation]:
    reference_date = today or date.today()
    recommendations = []
    for result in analyze_all(connection):
        days_until = parse_days_until(result.release_date, reference_date)
        is_upcoming = days_until is not None and 0 <= days_until <= 45
        is_priority = result.priority_band in {"A", "B"}
        has_premium_signal = result.premium_ratio is not None and result.premium_ratio >= 0.25
        if not (is_priority or is_upcoming or has_premium_signal):
            continue
        reasons = []
        if is_priority:
            reasons.append(f"{result.priority_band} 级关注")
        if is_upcoming:
            reasons.append(f"{days_until} 天内发售")
        if has_premium_signal:
            reasons.append(f"二手溢价 {round(result.premium_ratio * 100, 1)}%")
        score = result.attention_score
        if is_upcoming:
            score += 10
        if has_premium_signal:
            score += 5
        recommendations.append(
            RadarWatchRecommendation(
                item_id=result.item_id,
                label=result.label,
                reason=" / ".join(reasons),
                release_date=result.release_date,
                source_url=result.source_url,
                suggested_price_max=suggest_watch_price_max(result),
                priority_score=round(clamp(score), 2),
                priority_band=priority_band(clamp(score)),
            )
        )
    return sorted(recommendations, key=lambda item: (-item.priority_score, item.label))


def suggest_watch_price_max(result: RadarResult) -> Optional[float]:
    reference_price = result.domestic_median_cny or result.market_median_cny
    if reference_price is None:
        return None
    return round(reference_price * 1.1, 2)


def build_collection_task(
    item: RadarItemSummary,
    task_type: str,
    title: str,
    reason: str,
    action_hint: str,
    score: float,
) -> RadarCollectionTask:
    priority = round(clamp(score), 2)
    action_type, action_label, suggested_source_type = collection_task_action_metadata(task_type)
    return RadarCollectionTask(
        item_id=item.id,
        label=item.label,
        task_type=task_type,
        title=title,
        reason=reason,
        action_hint=action_hint,
        action_type=action_type,
        action_label=action_label,
        suggested_source_type=suggested_source_type,
        priority_score=priority,
        priority_band=priority_band(priority),
    )


def collection_task_action_metadata(task_type: str) -> tuple[str, str, str]:
    if task_type == "secondhand_samples":
        return ("add_sample", "补二手样本", "xianyu")
    if task_type == "domestic_samples":
        return ("add_sample", "补淘宝价", "taobao")
    return ("edit_item", "编辑款式", "")


def build_release_watch(
    results: Iterable[RadarResult],
    today: Optional[date] = None,
) -> list[ReleaseWatchItem]:
    reference_date = today or date.today()
    watch_items = []
    for result in results:
        release_date = clean_cell(result.release_date)
        if not release_date:
            continue
        days_until = parse_days_until(release_date, reference_date)
        if days_until is None:
            release_status = "unknown"
        elif days_until >= 0:
            release_status = "upcoming"
        else:
            release_status = "released"
        watch_items.append(
            ReleaseWatchItem(
                item_id=result.item_id,
                label=result.label,
                brand_name=result.brand_name,
                series_name=result.series_name,
                source_url=result.source_url,
                release_date=release_date,
                days_until=days_until,
                release_status=release_status,
                attention_score=result.attention_score,
                priority_band=result.priority_band,
            )
        )
    return sorted(watch_items, key=release_watch_sort_key)


def release_watch_sort_key(item: ReleaseWatchItem) -> tuple[int, int, float, str]:
    if item.days_until is None:
        return (2, 999999, -item.attention_score, item.label)
    if item.days_until >= 0:
        return (0, item.days_until, -item.attention_score, item.label)
    return (1, abs(item.days_until), -item.attention_score, item.label)


def calculate_landed_cost(item: RadarItem) -> Optional[float]:
    if item.original_price_jpy is None:
        return None
    return (
        item.original_price_jpy * item.jpy_to_cny
        + item.japan_domestic_shipping_cny
        + item.international_shipping_cny
        + item.proxy_fee_cny
        + item.tax_or_buffer_cny
    )


def load_market_samples(connection: sqlite3.Connection, item_id: int) -> list[sqlite3.Row]:
    return load_price_samples_by_source(connection, item_id, SECONDHAND_SOURCES)


def load_domestic_sale_samples(connection: sqlite3.Connection, item_id: int) -> list[sqlite3.Row]:
    return load_price_samples_by_source(connection, item_id, DOMESTIC_SALE_SOURCES)


def load_price_samples_by_source(
    connection: sqlite3.Connection,
    item_id: int,
    source_types: set[str],
) -> list[sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT
            *,
            COALESCE(sold_price_cny, listed_price_cny) AS price
        FROM price_samples
        WHERE item_id = ?
          AND source_type IN ({})
          AND COALESCE(sold_price_cny, listed_price_cny) IS NOT NULL
        """.format(",".join("?" for _ in source_types)),
        (item_id, *sorted(source_types)),
    ).fetchall()
    return [row for row in rows if float(row["price"]) > 0]


def premium_score(premium_ratio: Optional[float]) -> float:
    if premium_ratio is None:
        return 0
    if premium_ratio >= 0.8:
        return 100
    if premium_ratio >= 0.5:
        return 80
    if premium_ratio >= 0.3:
        return 60
    if premium_ratio >= 0.1:
        return 30
    return 0


def liquidity_score(sample_count: int, samples: Iterable[sqlite3.Row]) -> float:
    if sample_count >= 10:
        base = 100
    elif sample_count >= 5:
        base = 80
    elif sample_count >= 3:
        base = 60
    elif sample_count == 2:
        base = 40
    elif sample_count == 1:
        base = 20
    else:
        base = 0

    sold_count = sum(1 for sample in samples if str(sample["listing_status"]).lower() == "sold")
    sold_bonus = min(20, sold_count * 5)
    return clamp(base + sold_bonus)


def confidence_score(samples: Iterable[sqlite3.Row]) -> float:
    sample_list = list(samples)
    if not sample_list:
        return 0
    average_confidence = sum(float(sample["confidence"]) for sample in sample_list) / len(sample_list)
    sample_support = min(1.0, len(sample_list) / 5)
    return round(clamp((average_confidence * 0.7 + sample_support * 0.3) * 100), 2)


def priority_band(attention_score: float) -> str:
    if attention_score >= 75:
        return "A"
    if attention_score >= 55:
        return "B"
    if attention_score >= 35:
        return "C"
    return "D"


def row_to_item(row: sqlite3.Row) -> RadarItem:
    return RadarItem(
        id=int(row["id"]),
        brand_name=str(row["brand_name"]),
        series_name=str(row["series_name"]),
        item_name=str(row["item_name"]),
        category=str(row["category"]),
        colorway=str(row["colorway"]),
        original_price_jpy=optional_float(row["original_price_jpy"]),
        jpy_to_cny=float(row["jpy_to_cny"]),
        japan_domestic_shipping_cny=float(row["japan_domestic_shipping_cny"]),
        international_shipping_cny=float(row["international_shipping_cny"]),
        proxy_fee_cny=float(row["proxy_fee_cny"]),
        tax_or_buffer_cny=float(row["tax_or_buffer_cny"]),
        release_signal_score=float(row["release_signal_score"]),
        source_url=str(row["source_url"]),
        release_date=str(row["release_date"]),
    )


def build_label(item: RadarItem) -> str:
    parts = [item.brand_name, item.series_name, item.item_name, item.category, item.colorway]
    return " / ".join(part for part in parts if part)


def optional_float(value: object) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def optional_int(value: object) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)


def optional_cell_float(value: Any) -> Optional[float]:
    cleaned = clean_cell(value)
    if not cleaned:
        return None
    return float(cleaned)


def clean_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def extract_url_from_text(text: str) -> str:
    match = URL_RE.search(text)
    if not match:
        return ""
    return match.group(0).rstrip(".,，。)")


def parse_days_until(release_date: str, today: date) -> Optional[int]:
    cleaned = clean_cell(release_date)
    if not cleaned:
        return None
    for date_format in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(cleaned, date_format).date()
            return (parsed - today).days
        except ValueError:
            continue
    return None


def extract_release_date_from_text(text: str) -> str:
    for pattern in (ISO_DATE_RE, JP_DATE_RE):
        match = pattern.search(text)
        if not match:
            continue
        year, month, day = (int(value) for value in match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""
    return ""


def extract_price_from_text(text: str) -> Optional[float]:
    for match in PRICE_RE.finditer(text):
        raw = match.group(1) or match.group(2)
        if not raw:
            continue
        value = float(raw.replace(",", ""))
        if 0 < value < 1_000_000:
            return value
    return None


def extract_jpy_price_from_text(text: str) -> Optional[float]:
    for match in JPY_PRICE_RE.finditer(text):
        raw = match.group(1) or match.group(2)
        if not raw:
            continue
        value = float(raw.replace(",", ""))
        if 0 < value < 1_000_000:
            return value
    return None


def infer_listing_status(text: str, default_status: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("已售", "售出", "成交", "sold")):
        return "sold"
    if any(token in lowered for token in ("在售", "挂价", "出", "listed")):
        return "listed"
    return default_status or "unknown"


def clean_candidate_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" -|,，。:：")
    return text[:160]


def clean_release_title(text: str) -> str:
    cleaned = JPY_PRICE_RE.sub(" ", text)
    cleaned = ISO_DATE_RE.sub(" ", cleaned)
    cleaned = JP_DATE_RE.sub(" ", cleaned)
    cleaned = re.sub(r"(tax included|税込|税抜|価格|price)", " ", cleaned, flags=re.IGNORECASE)
    return clean_candidate_title(cleaned)


def infer_category(title: str) -> str:
    padded = f" {title.lower()} "
    category_map = [
        ("JSK", (" jsk ", "ジャンパースカート")),
        ("OP", (" op ", "one piece", "ワンピース")),
        ("skirt", ("skirt", "スカート")),
        ("blouse", ("blouse", "ブラウス")),
        ("KC", (" kc ", "カチューシャ")),
        ("bonnet", ("bonnet", "ボンネット")),
        ("bag", ("bag", "バッグ")),
    ]
    for category, tokens in category_map:
        if any(token in padded for token in tokens):
            return category
    return ""


def require_text(row: dict[str, Any], key: str, row_number: int) -> str:
    value = clean_cell(row.get(key))
    if not value:
        raise ValueError(f"CSV row {row_number} missing required column: {key}")
    return value


def round_optional(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def clamp(value: float, lower: float = 0, upper: float = 100) -> float:
    return max(lower, min(upper, value))
