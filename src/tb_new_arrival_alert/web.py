from __future__ import annotations

import json
import errno
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from .config import (
    ConfigError,
    get_data_dir,
    get_poll_interval,
    get_targets,
    init_config,
    load_config,
)
from .extractors import extract_items
from .fetchers import make_fetcher
from .matcher import matches_target
from .models import Item, Target
from .notify import make_notifiers, notify_all
from .radar import (
    DEFAULT_JPY_TO_CNY,
    PriceSampleCandidate,
    PriceSampleSummary,
    RadarAggregate,
    RadarCollectionTask,
    RadarImportResult,
    RadarItemSummary,
    RadarReviewStats,
    RadarReviewSummary,
    RadarResult,
    RadarWatchRecommendation,
    ReleaseItemCandidate,
    ReleaseWatchItem,
    add_candidates_as_samples,
    add_release_candidates_as_items,
    add_price_sample,
    analyze_all,
    analyze_aggregates,
    build_release_watch,
    connect_radar_db,
    create_radar_item,
    delete_radar_item,
    delete_price_sample,
    delete_radar_review,
    get_radar_item_summary,
    import_radar_csv,
    list_collection_tasks,
    list_radar_items,
    list_price_samples,
    list_radar_reviews,
    list_watch_recommendations,
    parse_release_item_candidates,
    parse_price_sample_candidates,
    summarize_radar_reviews,
    update_radar_item,
    update_price_sample,
    upsert_radar_review,
)
from .storage import SeenStore


ASSET_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}
DEFAULT_WEB_PORT = 8766


def run_web(config_path: Path, host: str = "127.0.0.1", port: int = DEFAULT_WEB_PORT) -> int:
    ensure_config(config_path)
    handler = make_handler(config_path)
    server, actual_port = open_server(host, port, handler)
    url = f"http://{host}:{actual_port}"
    print(f"TB New Arrival Alert web UI: {url}")
    print(f"Config: {config_path.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web UI")
    finally:
        server.server_close()
    return 0


def open_server(
    host: str,
    port: int,
    handler: type[BaseHTTPRequestHandler],
    fallback_attempts: int = 20,
) -> tuple[ThreadingHTTPServer, int]:
    if port == 0:
        server = ThreadingHTTPServer((host, port), handler)
        return server, int(server.server_address[1])

    last_error: OSError | None = None
    for candidate in range(port, port + fallback_attempts + 1):
        try:
            server = ThreadingHTTPServer((host, candidate), handler)
            if candidate != port:
                print(f"Port {port} is busy; using {candidate} instead.")
            return server, candidate
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            last_error = exc

    raise OSError(
        errno.EADDRINUSE,
        f"No available port found from {port} to {port + fallback_attempts}",
    ) from last_error


def ensure_config(config_path: Path) -> None:
    if not config_path.exists():
        init_config(config_path)


def make_handler(config_path: Path) -> type[BaseHTTPRequestHandler]:
    class WebHandler(BaseHTTPRequestHandler):
        server_version = "TBNewArrivalAlert/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[web] {self.address_string()} - {fmt % args}", file=sys.stderr)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    self.send_asset("index.html")
                elif parsed.path.startswith("/assets/"):
                    self.send_asset(parsed.path.removeprefix("/assets/"))
                elif parsed.path == "/api/config":
                    self.send_json({"config": load_config(config_path)})
                elif parsed.path == "/api/state":
                    self.send_json(get_state(config_path))
                elif parsed.path == "/api/radar":
                    self.send_json(get_radar(config_path))
                elif parsed.path == "/api/health":
                    self.send_json({"ok": True})
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            except Exception as exc:
                self.send_exception(exc)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/config":
                    payload = self.read_json()
                    config = payload.get("config", payload)
                    save_config(config_path, config)
                    self.send_json({"ok": True, "config": config})
                elif parsed.path == "/api/scan":
                    payload = self.read_json(default={})
                    result = scan_once(
                        config_path,
                        send_notifications=bool(payload.get("send_notifications", True)),
                    )
                    self.send_json(result)
                elif parsed.path == "/api/radar/items":
                    payload = self.read_json()
                    self.send_json(create_radar_item_from_payload(config_path, payload))
                elif parsed.path == "/api/radar/items/update":
                    payload = self.read_json()
                    self.send_json(update_radar_item_from_payload(config_path, payload))
                elif parsed.path == "/api/radar/items/delete":
                    payload = self.read_json()
                    self.send_json(delete_radar_item_from_payload(config_path, payload))
                elif parsed.path == "/api/radar/samples":
                    payload = self.read_json()
                    self.send_json(add_radar_sample_from_payload(config_path, payload))
                elif parsed.path == "/api/radar/samples/update":
                    payload = self.read_json()
                    self.send_json(update_radar_sample_from_payload(config_path, payload))
                elif parsed.path == "/api/radar/import":
                    payload = self.read_json()
                    self.send_json(import_radar_from_payload(config_path, payload))
                elif parsed.path == "/api/radar/watch-target":
                    payload = self.read_json()
                    self.send_json(create_watch_target_from_payload(config_path, payload))
                elif parsed.path == "/api/radar/collect":
                    payload = self.read_json()
                    self.send_json(collect_radar_samples_from_payload(config_path, payload))
                elif parsed.path == "/api/radar/release-collect":
                    payload = self.read_json()
                    self.send_json(collect_release_items_from_payload(config_path, payload))
                elif parsed.path == "/api/radar/reviews":
                    payload = self.read_json()
                    self.send_json(upsert_radar_review_from_payload(config_path, payload))
                elif parsed.path == "/api/radar/reviews/delete":
                    payload = self.read_json()
                    self.send_json(delete_radar_review_from_payload(config_path, payload))
                elif parsed.path == "/api/radar/samples/delete":
                    payload = self.read_json()
                    self.send_json(delete_radar_sample_from_payload(config_path, payload))
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            except Exception as exc:
                self.send_exception(exc)

        def read_json(self, default: Any | None = None) -> Any:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                if default is not None:
                    return default
                raise ValueError("Missing JSON body")
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw)

        def send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_asset(self, name: str) -> None:
            if "/" in name or "\\" in name or name.startswith("."):
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            try:
                asset = resources.files("tb_new_arrival_alert.web_assets").joinpath(name)
                data = asset.read_bytes()
            except FileNotFoundError:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            suffix = Path(name).suffix
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ASSET_TYPES.get(suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_exception(self, exc: Exception) -> None:
            status = HTTPStatus.BAD_REQUEST if isinstance(exc, (ConfigError, ValueError)) else HTTPStatus.INTERNAL_SERVER_ERROR
            self.send_json({"ok": False, "error": str(exc)}, status=status)

    return WebHandler


def save_config(config_path: Path, config: Dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ConfigError("Config must be a JSON object")
    get_poll_interval(config)
    get_targets(config)
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_state(config_path: Path) -> Dict[str, Any]:
    config = load_config(config_path)
    data_dir = get_data_dir(config, config_path)
    store = SeenStore(data_dir / "seen.json")
    targets = get_targets(config)
    return {
        "config_path": str(config_path.resolve()),
        "data_dir": str(data_dir.resolve()),
        "targets": [target_to_dict(target) for target in targets],
        "seen_counts": {name: len(items) for name, items in store.data.items()},
    }


def get_radar(config_path: Path) -> Dict[str, Any]:
    db_path = get_radar_db_path(config_path)
    config = load_config(config_path)
    monitored_target_names = {target.name for target in get_targets(config)}
    connection = connect_radar_db(db_path)
    try:
        results = analyze_all(connection)
        aggregates = analyze_aggregates(connection)
        release_watch = build_release_watch(results)
        collection_tasks = list_collection_tasks(connection)
        watch_recommendations = list_watch_recommendations(connection)
        items = list_radar_items(connection)
        samples = list_price_samples(connection)
        reviews = list_radar_reviews(connection)
        review_stats = summarize_radar_reviews(reviews)
    finally:
        connection.close()
    watch_recommendation_dicts = [
        watch_recommendation_to_dict(item, monitored_target_names)
        for item in watch_recommendations
    ]
    watch_recommendation_dicts.sort(
        key=lambda item: (
            bool(item["already_watched"]),
            -float(item["priority_score"]),
            str(item["label"]),
        )
    )
    return {
        "ok": True,
        "db_path": str(db_path.resolve()),
        "results": [radar_result_to_dict(result) for result in results],
        "aggregates": [radar_aggregate_to_dict(aggregate) for aggregate in aggregates],
        "release_watch": [release_watch_item_to_dict(item) for item in release_watch],
        "collection_tasks": [collection_task_to_dict(task) for task in collection_tasks],
        "watch_recommendations": watch_recommendation_dicts,
        "items": [radar_item_summary_to_dict(item) for item in items],
        "samples": [price_sample_summary_to_dict(sample) for sample in samples],
        "reviews": [radar_review_summary_to_dict(review) for review in reviews],
        "review_stats": radar_review_stats_to_dict(review_stats),
    }


def get_radar_db_path(config_path: Path) -> Path:
    config = load_config(config_path)
    data_dir = get_data_dir(config, config_path)
    return data_dir / "radar.sqlite"


def create_radar_item_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    brand_name = required_text(payload, "brand_name")
    item_name = required_text(payload, "item_name")
    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        item_id = create_radar_item(
            connection,
            brand_name=brand_name,
            series_name=text_value(payload.get("series_name")),
            item_name=item_name,
            category=text_value(payload.get("category")),
            colorway=text_value(payload.get("colorway")),
            original_price_jpy=optional_number(payload.get("original_price_jpy")),
            jpy_to_cny=optional_number(payload.get("jpy_to_cny")) or DEFAULT_JPY_TO_CNY,
            japan_domestic_shipping_cny=optional_number(payload.get("japan_domestic_shipping_cny")) or 0,
            international_shipping_cny=optional_number(payload.get("international_shipping_cny")) or 0,
            proxy_fee_cny=optional_number(payload.get("proxy_fee_cny")) or 0,
            tax_or_buffer_cny=optional_number(payload.get("tax_or_buffer_cny")) or 0,
            release_signal_score=optional_number(payload.get("release_signal_score")) or 50,
            source_url=text_value(payload.get("source_url")),
            image_url=text_value(payload.get("image_url")),
            release_date=text_value(payload.get("release_date")),
            notes=text_value(payload.get("notes")),
        )
        result = get_radar(config_path)
        result.update({"created_item_id": item_id})
        return result
    finally:
        connection.close()


def update_radar_item_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    item_id = int(required_text(payload, "item_id"))
    brand_name = required_text(payload, "brand_name")
    item_name = required_text(payload, "item_name")
    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        updated = update_radar_item(
            connection,
            item_id=item_id,
            brand_name=brand_name,
            series_name=text_value(payload.get("series_name")),
            item_name=item_name,
            category=text_value(payload.get("category")),
            colorway=text_value(payload.get("colorway")),
            original_price_jpy=optional_number(payload.get("original_price_jpy")),
            jpy_to_cny=optional_number(payload.get("jpy_to_cny")) or DEFAULT_JPY_TO_CNY,
            japan_domestic_shipping_cny=optional_number(payload.get("japan_domestic_shipping_cny")) or 0,
            international_shipping_cny=optional_number(payload.get("international_shipping_cny")) or 0,
            proxy_fee_cny=optional_number(payload.get("proxy_fee_cny")) or 0,
            tax_or_buffer_cny=optional_number(payload.get("tax_or_buffer_cny")) or 0,
            release_signal_score=optional_number(payload.get("release_signal_score")) or 50,
            source_url=text_value(payload.get("source_url")),
            image_url=text_value(payload.get("image_url")),
            release_date=text_value(payload.get("release_date")),
            notes=text_value(payload.get("notes")),
        )
        if not updated:
            raise ValueError(f"Radar item not found: {item_id}")
        result = get_radar(config_path)
        result.update({"updated_item_id": item_id})
        return result
    finally:
        connection.close()


def delete_radar_item_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    item_id = int(required_text(payload, "item_id"))
    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        deleted = delete_radar_item(connection, item_id)
    finally:
        connection.close()
    radar = get_radar(config_path)
    radar.update({"deleted": deleted, "deleted_item_id": item_id})
    return radar


def add_radar_sample_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    item_id = int(required_text(payload, "item_id"))
    source_type = required_text(payload, "source_type")
    listed_price = optional_number(payload.get("listed_price_cny"))
    sold_price = optional_number(payload.get("sold_price_cny"))
    if listed_price is None and sold_price is None:
        raise ValueError("Either listed_price_cny or sold_price_cny is required")

    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        sample_id = add_price_sample(
            connection,
            item_id=item_id,
            source_type=source_type,
            listed_price_cny=listed_price,
            sold_price_cny=sold_price,
            source_url=text_value(payload.get("source_url")),
            title=text_value(payload.get("title")),
            condition=text_value(payload.get("condition")),
            listing_status=text_value(payload.get("listing_status")) or "unknown",
            confidence=optional_number(payload.get("confidence")) or 0.7,
            notes=text_value(payload.get("notes")),
        )
        result = get_radar(config_path)
        result.update({"created_sample_id": sample_id})
        return result
    finally:
        connection.close()


def update_radar_sample_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    sample_id = int(required_text(payload, "sample_id"))
    item_id = int(required_text(payload, "item_id"))
    source_type = required_text(payload, "source_type")
    listed_price = optional_number(payload.get("listed_price_cny"))
    sold_price = optional_number(payload.get("sold_price_cny"))
    if listed_price is None and sold_price is None:
        raise ValueError("Either listed_price_cny or sold_price_cny is required")

    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        updated = update_price_sample(
            connection,
            sample_id=sample_id,
            item_id=item_id,
            source_type=source_type,
            listed_price_cny=listed_price,
            sold_price_cny=sold_price,
            source_url=text_value(payload.get("source_url")),
            title=text_value(payload.get("title")),
            condition=text_value(payload.get("condition")),
            listing_status=text_value(payload.get("listing_status")) or "unknown",
            confidence=optional_number(payload.get("confidence")) or 0.7,
            notes=text_value(payload.get("notes")),
        )
        if not updated:
            raise ValueError(f"Radar sample not found: {sample_id}")
        result = get_radar(config_path)
        result.update({"updated_sample_id": sample_id})
        return result
    finally:
        connection.close()


def import_radar_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    csv_path = Path(required_text(payload, "path")).expanduser()
    if not csv_path.is_absolute() and not csv_path.exists():
        csv_path = config_path.parent / csv_path
    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        result = import_radar_csv(connection, csv_path)
    finally:
        connection.close()
    radar = get_radar(config_path)
    radar.update({"import": radar_import_result_to_dict(result)})
    return radar


def create_watch_target_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    item_id = int(required_text(payload, "item_id"))
    url = required_text(payload, "url")
    price_min = optional_number(payload.get("price_min"))
    price_max = optional_number(payload.get("price_max"))

    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        item = get_radar_item_summary(connection, item_id)
    finally:
        connection.close()

    config = load_config(config_path)
    targets = config.setdefault("targets", [])
    if not isinstance(targets, list):
        raise ConfigError("targets must be a list")

    target = build_watch_target(item, url=url, price_min=price_min, price_max=price_max)
    existing = find_existing_target(targets, target)
    if existing is None:
        targets.append(target)
        save_config(config_path, config)
        created = True
    else:
        target = existing
        created = False

    return {
        "ok": True,
        "created": created,
        "target": target,
        "state": get_state(config_path),
    }


def collect_radar_samples_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    item_id = int(required_text(payload, "item_id"))
    text = required_text(payload, "text")
    source_type = required_text(payload, "source_type")
    default_status = text_value(payload.get("listing_status")) or "listed"
    confidence = optional_number(payload.get("confidence")) or 0.65
    candidates = parse_price_sample_candidates(
        text=text,
        source_type=source_type,
        default_status=default_status,
        confidence=confidence,
    )
    if not candidates:
        raise ValueError("No price candidates found in pasted text")

    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        sample_ids = add_candidates_as_samples(connection, item_id=item_id, candidates=candidates)
    finally:
        connection.close()
    radar = get_radar(config_path)
    radar.update(
        {
            "collected": {
                "candidates": [price_sample_candidate_to_dict(candidate) for candidate in candidates],
                "sample_ids": sample_ids,
                "saved_count": len(sample_ids),
            }
        }
    )
    return radar


def collect_release_items_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    brand_name = required_text(payload, "brand_name")
    text = required_text(payload, "text")
    candidates = parse_release_item_candidates(
        text=text,
        brand_name=brand_name,
        default_series_name=text_value(payload.get("series_name")),
        default_category=text_value(payload.get("category")),
        default_colorway=text_value(payload.get("colorway")),
        default_source_url=text_value(payload.get("source_url")),
        default_release_date=text_value(payload.get("release_date")),
    )
    if not candidates:
        raise ValueError("No release items with JPY prices found in pasted text")

    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        item_ids, created_count = add_release_candidates_as_items(
            connection,
            candidates=candidates,
            jpy_to_cny=optional_number(payload.get("jpy_to_cny")) or DEFAULT_JPY_TO_CNY,
            japan_domestic_shipping_cny=optional_number(payload.get("japan_domestic_shipping_cny")) or 0,
            international_shipping_cny=optional_number(payload.get("international_shipping_cny")) or 0,
            proxy_fee_cny=optional_number(payload.get("proxy_fee_cny")) or 0,
            tax_or_buffer_cny=optional_number(payload.get("tax_or_buffer_cny")) or 0,
            release_signal_score=optional_number(payload.get("release_signal_score")) or 70,
        )
    finally:
        connection.close()
    radar = get_radar(config_path)
    radar.update(
        {
            "release_collected": {
                "candidates": [release_item_candidate_to_dict(candidate) for candidate in candidates],
                "item_ids": item_ids,
                "saved_count": len(item_ids),
                "created_count": created_count,
            }
        }
    )
    return radar


def upsert_radar_review_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    item_id = int(required_text(payload, "item_id"))
    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        review = upsert_radar_review(
            connection,
            item_id=item_id,
            review_status=text_value(payload.get("review_status")) or "pending",
            observed_price_cny=optional_number(payload.get("observed_price_cny")),
            review_window_days=optional_integer(payload.get("review_window_days")),
            notes=text_value(payload.get("notes")),
        )
    finally:
        connection.close()
    radar = get_radar(config_path)
    radar.update(
        {
            "reviewed_item_id": item_id,
            "review": radar_review_summary_to_dict(review),
        }
    )
    return radar


def delete_radar_review_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    item_id = int(required_text(payload, "item_id"))
    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        deleted = delete_radar_review(connection, item_id)
    finally:
        connection.close()
    radar = get_radar(config_path)
    radar.update({"deleted": deleted, "deleted_review_item_id": item_id})
    return radar


def delete_radar_sample_from_payload(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    sample_id = int(required_text(payload, "sample_id"))
    db_path = get_radar_db_path(config_path)
    connection = connect_radar_db(db_path)
    try:
        deleted = delete_price_sample(connection, sample_id)
    finally:
        connection.close()
    radar = get_radar(config_path)
    radar.update({"deleted": deleted, "deleted_sample_id": sample_id})
    return radar


def build_watch_target(
    item: RadarItemSummary,
    url: str,
    price_min: float | None = None,
    price_max: float | None = None,
) -> Dict[str, Any]:
    return {
        "name": f"radar-{item.label}",
        "enabled": True,
        "url": url,
        "include_keywords": build_watch_keywords(item),
        "exclude_keywords": ["尾款", "定金单独"],
        "price_min": price_min,
        "price_max": price_max,
    }


def build_watch_keywords(item: RadarItemSummary) -> list[str]:
    raw_keywords = [
        item.series_name,
        item.item_name,
        item.colorway,
        item.brand_name,
        item.category,
    ]
    keywords: list[str] = []
    for keyword in raw_keywords:
        cleaned = text_value(keyword)
        if not cleaned or cleaned in keywords:
            continue
        keywords.append(cleaned)
    return keywords


def find_existing_target(targets: list[Any], candidate: Dict[str, Any]) -> Dict[str, Any] | None:
    for target in targets:
        if not isinstance(target, dict):
            continue
        if target.get("name") == candidate["name"] and target.get("url") == candidate["url"]:
            return target
    return None


def scan_once(config_path: Path, send_notifications: bool = True) -> Dict[str, Any]:
    config = load_config(config_path)
    targets = get_targets(config)
    data_dir = get_data_dir(config, config_path)
    store = SeenStore(data_dir / "seen.json")
    fetcher = make_fetcher(config)
    notifiers = make_notifiers(config) if send_notifications else []
    notify_on_first_scan = bool(config.get("notify_on_first_scan", False))

    results = []
    total_new = 0
    total_notified = 0

    for target in targets:
        if not target.enabled:
            results.append({"target": target.name, "enabled": False, "skipped": True})
            continue

        try:
            html_text = fetcher.fetch(target.url)
            extracted = extract_items(html_text, target.url)
            matched = [item for item in extracted if matches_target(item, target)]
            had_baseline = store.has_baseline(target.name)
            new_items = store.new_items(target.name, matched)
            store.mark_seen(target.name, matched)
            store.save()

            should_notify = send_notifications and (had_baseline or notify_on_first_scan)
            if should_notify:
                for item in new_items:
                    notify_all(notifiers, target, item)

            notified_count = len(new_items) if should_notify else 0
            total_new += len(new_items)
            total_notified += notified_count
            results.append(
                {
                    "target": target.name,
                    "enabled": True,
                    "found": len(extracted),
                    "matched": len(matched),
                    "new": len(new_items),
                    "notified": notified_count,
                    "baseline_only": not should_notify and len(new_items) > 0,
                    "items": [item_to_dict(item) for item in new_items],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "target": target.name,
                    "enabled": True,
                    "error": str(exc),
                }
            )

    return {
        "ok": True,
        "total_new": total_new,
        "total_notified": total_notified,
        "results": results,
    }


def item_to_dict(item: Item) -> Dict[str, Any]:
    return {
        "item_id": item.item_id,
        "title": item.title,
        "url": item.url,
        "price": item.price,
    }


def target_to_dict(target: Target) -> Dict[str, Any]:
    return {
        "name": target.name,
        "url": target.url,
        "enabled": target.enabled,
        "include_keywords": list(target.include_keywords),
        "exclude_keywords": list(target.exclude_keywords),
        "price_min": target.price_min,
        "price_max": target.price_max,
    }


def radar_result_to_dict(result: RadarResult) -> Dict[str, Any]:
    return {
        "item_id": result.item_id,
        "label": result.label,
        "brand_name": result.brand_name,
        "series_name": result.series_name,
        "source_url": result.source_url,
        "release_date": result.release_date,
        "landed_cost_cny": result.landed_cost_cny,
        "market_median_cny": result.market_median_cny,
        "premium_cny": result.premium_cny,
        "premium_ratio": result.premium_ratio,
        "sample_count": result.sample_count,
        "domestic_median_cny": result.domestic_median_cny,
        "domestic_markup_cny": result.domestic_markup_cny,
        "domestic_markup_ratio": result.domestic_markup_ratio,
        "domestic_sample_count": result.domestic_sample_count,
        "premium_score": result.premium_score,
        "liquidity_score": result.liquidity_score,
        "release_signal_score": result.release_signal_score,
        "confidence_score": result.confidence_score,
        "attention_score": result.attention_score,
        "priority_band": result.priority_band,
    }


def radar_aggregate_to_dict(aggregate: RadarAggregate) -> Dict[str, Any]:
    return {
        "group_type": aggregate.group_type,
        "name": aggregate.name,
        "brand_name": aggregate.brand_name,
        "series_name": aggregate.series_name,
        "item_count": aggregate.item_count,
        "secondhand_sample_count": aggregate.secondhand_sample_count,
        "domestic_sample_count": aggregate.domestic_sample_count,
        "median_premium_ratio": aggregate.median_premium_ratio,
        "median_domestic_markup_ratio": aggregate.median_domestic_markup_ratio,
        "average_attention_score": aggregate.average_attention_score,
        "max_attention_score": aggregate.max_attention_score,
        "attention_score": aggregate.attention_score,
        "priority_band": aggregate.priority_band,
    }


def radar_item_summary_to_dict(item: RadarItemSummary) -> Dict[str, Any]:
    return {
        "id": item.id,
        "label": item.label,
        "brand_name": item.brand_name,
        "series_name": item.series_name,
        "item_name": item.item_name,
        "category": item.category,
        "colorway": item.colorway,
        "original_price_jpy": item.original_price_jpy,
        "jpy_to_cny": item.jpy_to_cny,
        "japan_domestic_shipping_cny": item.japan_domestic_shipping_cny,
        "international_shipping_cny": item.international_shipping_cny,
        "proxy_fee_cny": item.proxy_fee_cny,
        "tax_or_buffer_cny": item.tax_or_buffer_cny,
        "release_signal_score": item.release_signal_score,
        "source_url": item.source_url,
        "release_date": item.release_date,
    }


def release_watch_item_to_dict(item: ReleaseWatchItem) -> Dict[str, Any]:
    return {
        "item_id": item.item_id,
        "label": item.label,
        "brand_name": item.brand_name,
        "series_name": item.series_name,
        "source_url": item.source_url,
        "release_date": item.release_date,
        "days_until": item.days_until,
        "release_status": item.release_status,
        "attention_score": item.attention_score,
        "priority_band": item.priority_band,
    }


def collection_task_to_dict(task: RadarCollectionTask) -> Dict[str, Any]:
    return {
        "item_id": task.item_id,
        "label": task.label,
        "task_type": task.task_type,
        "title": task.title,
        "reason": task.reason,
        "action_hint": task.action_hint,
        "action_type": task.action_type,
        "action_label": task.action_label,
        "suggested_source_type": task.suggested_source_type,
        "priority_score": task.priority_score,
        "priority_band": task.priority_band,
    }


def watch_recommendation_to_dict(
    item: RadarWatchRecommendation,
    monitored_target_names: set[str] | None = None,
) -> Dict[str, Any]:
    target_name = f"radar-{item.label}"
    already_watched = target_name in (monitored_target_names or set())
    return {
        "item_id": item.item_id,
        "label": item.label,
        "reason": item.reason,
        "release_date": item.release_date,
        "source_url": item.source_url,
        "suggested_price_max": item.suggested_price_max,
        "priority_score": item.priority_score,
        "priority_band": item.priority_band,
        "monitor_target_name": target_name,
        "already_watched": already_watched,
        "action_label": "已监控" if already_watched else "载入监控",
    }


def radar_review_summary_to_dict(review: RadarReviewSummary) -> Dict[str, Any]:
    return {
        "item_id": review.item_id,
        "item_label": review.item_label,
        "review_status": review.review_status,
        "observed_price_cny": review.observed_price_cny,
        "observed_premium_ratio": review.observed_premium_ratio,
        "predicted_premium_ratio": review.predicted_premium_ratio,
        "predicted_attention_score": review.predicted_attention_score,
        "review_window_days": review.review_window_days,
        "reviewed_at": review.reviewed_at,
        "notes": review.notes,
    }


def radar_review_stats_to_dict(stats: RadarReviewStats) -> Dict[str, Any]:
    return {
        "total_count": stats.total_count,
        "reviewed_count": stats.reviewed_count,
        "pending_count": stats.pending_count,
        "hit_count": stats.hit_count,
        "miss_count": stats.miss_count,
        "hit_rate": stats.hit_rate,
        "average_observed_premium_ratio": stats.average_observed_premium_ratio,
        "average_predicted_premium_ratio": stats.average_predicted_premium_ratio,
    }


def price_sample_summary_to_dict(sample: PriceSampleSummary) -> Dict[str, Any]:
    return {
        "id": sample.id,
        "item_id": sample.item_id,
        "item_label": sample.item_label,
        "source_type": sample.source_type,
        "source_url": sample.source_url,
        "title": sample.title,
        "listed_price_cny": sample.listed_price_cny,
        "sold_price_cny": sample.sold_price_cny,
        "effective_price_cny": sample.effective_price_cny,
        "condition": sample.condition,
        "listing_status": sample.listing_status,
        "confidence": sample.confidence,
        "captured_at": sample.captured_at,
        "notes": sample.notes,
    }


def price_sample_candidate_to_dict(candidate: PriceSampleCandidate) -> Dict[str, Any]:
    return {
        "source_type": candidate.source_type,
        "source_url": candidate.source_url,
        "title": candidate.title,
        "listed_price_cny": candidate.listed_price_cny,
        "sold_price_cny": candidate.sold_price_cny,
        "listing_status": candidate.listing_status,
        "confidence": candidate.confidence,
    }


def release_item_candidate_to_dict(candidate: ReleaseItemCandidate) -> Dict[str, Any]:
    return {
        "brand_name": candidate.brand_name,
        "series_name": candidate.series_name,
        "item_name": candidate.item_name,
        "category": candidate.category,
        "colorway": candidate.colorway,
        "original_price_jpy": candidate.original_price_jpy,
        "source_url": candidate.source_url,
        "release_date": candidate.release_date,
        "raw_title": candidate.raw_title,
    }


def radar_import_result_to_dict(result: RadarImportResult) -> Dict[str, Any]:
    return {
        "rows_read": result.rows_read,
        "items_created": result.items_created,
        "samples_created": result.samples_created,
    }


def required_text(payload: Dict[str, Any], key: str) -> str:
    value = text_value(payload.get(key))
    if not value:
        raise ValueError(f"{key} is required")
    return value


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def optional_integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    parsed = float(value)
    if not parsed.is_integer():
        raise ValueError("Expected an integer value")
    return int(parsed)
