from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .adapters import SourceConfig
from .brands import build_focus_queue, default_brand_weights_path, load_brand_weights, save_brand_weights
from .config import load_sources
from .crawler import enrich_source_runs
from .feed import build_home_feed
from .market import (
    append_market_observation,
    build_brand_weight_profile,
    build_market_alerts,
    build_market_momentum,
    build_opportunity_radar,
    build_pattern_radar,
    build_sample_collection_plan,
    default_market_observations_path,
    load_market_observations,
    summarize_market_observations,
)
from .models import RadarEvent
from .runner import check_sources
from .storage import connect, list_events, list_items, list_source_runs, storage_counts


DEFAULT_WEB_PORT = 8766
ASSETS_DIR = Path(__file__).with_name("assets")


def run_web(
    config_path: Path,
    db_path: Path,
    brands_path: Path | None = None,
    market_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = DEFAULT_WEB_PORT,
) -> int:
    if brands_path is None:
        brands_path = default_brand_weights_path()
    if market_path is None:
        market_path = default_market_observations_path()
    handler = make_handler(config_path=config_path, db_path=db_path, brands_path=brands_path, market_path=market_path)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Lolita Premium Radar web UI: http://{host}:{port}")
    print(f"Config: {config_path.resolve()}")
    print(f"Brand weights: {brands_path.resolve()}")
    print(f"Market observations: {market_path.resolve()}")
    print(f"Database: {db_path.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web UI")
    finally:
        server.server_close()
    return 0


def make_handler(
    config_path: Path,
    db_path: Path,
    brands_path: Path | None = None,
    market_path: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    if brands_path is None:
        brands_path = default_brand_weights_path()
    if market_path is None:
        market_path = default_market_observations_path()

    class WebHandler(BaseHTTPRequestHandler):
        server_version = "LolitaPremiumRadar/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[web] {self.address_string()} - {fmt % args}", file=sys.stderr)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    self.send_html(FEED_INDEX_HTML)
                elif parsed.path.startswith("/assets/"):
                    self.send_asset(parsed.path.removeprefix("/assets/"))
                elif parsed.path == "/api/health":
                    self.send_json({"ok": True})
                elif parsed.path == "/api/state":
                    self.send_json(get_feed_state(config_path, db_path, brands_path, market_path))
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            except Exception as exc:
                self.send_exception(exc)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/check":
                    self.handle_check()
                elif parsed.path == "/api/market/observations":
                    self.handle_market_observation()
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            except Exception as exc:
                self.send_exception(exc)

        def do_PUT(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/brand-weights":
                    self.handle_brand_weights()
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            except Exception as exc:
                self.send_exception(exc)

        def handle_check(self) -> None:
            payload = self.read_json(default={})
            source_name = text_value(payload.get("source")) or None
            notify = bool(payload.get("notify", False))
            events = check_sources(config_path=config_path, db_path=db_path, source_name=source_name, notify=notify)
            state = get_feed_state(config_path, db_path, brands_path, market_path)
            state.update(
                {
                    "checked_source": source_name or "all",
                    "new_events": [event_to_dict(event) for event in events],
                    "new_event_count": len(events),
                }
            )
            self.send_json(state)

        def handle_market_observation(self) -> None:
            payload = self.read_json(default={})
            observation = append_market_observation(market_path, payload)
            state = get_feed_state(config_path, db_path, brands_path, market_path)
            state.update({"added_market_observation": observation})
            self.send_json(state, status=HTTPStatus.CREATED)

        def handle_brand_weights(self) -> None:
            payload = self.read_json(default={})
            rows = payload.get("weights") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("brand weight update must include a weights list")
            updated = save_brand_weights(brands_path, rows)
            state = get_feed_state(config_path, db_path, brands_path, market_path)
            state.update({"updated_brand_weights": updated})
            self.send_json(state)

        def read_json(self, default: Any | None = None) -> Any:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return default if default is not None else {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_asset(self, asset_name: str) -> None:
            asset_path = (ASSETS_DIR / asset_name).resolve()
            if asset_path.parent != ASSETS_DIR.resolve() or not asset_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            content_type = "image/png" if asset_path.suffix == ".png" else "application/octet-stream"
            body = asset_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_exception(self, exc: Exception) -> None:
            self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    return WebHandler


def get_feed_state(
    config_path: Path,
    db_path: Path,
    brands_path: Path | None = None,
    market_path: Path | None = None,
) -> dict[str, Any]:
    sources = load_sources(config_path)
    brand_weights = load_brand_weights(brands_path)
    market_observations = load_market_observations(market_path)
    market_summary = summarize_market_observations(market_observations, brand_weights)
    market_alerts = build_market_alerts(brand_weights, market_summary)
    momentum = build_market_momentum(market_observations, brand_weights)
    connection = connect(db_path)
    try:
        counts = storage_counts(connection)
        items = list_items(connection, limit=100)
        events = list_events(connection, limit=100)
        source_runs = enrich_source_runs(list_source_runs(connection, limit=50))
    finally:
        connection.close()
    return {
        "ok": True,
        "config_path": str(config_path.resolve()),
        "db_path": str(db_path.resolve()),
        "brands_path": str((brands_path or default_brand_weights_path()).resolve()),
        "market_path": str((market_path or default_market_observations_path()).resolve()),
        "counts": {
            **counts,
            "sources": len(sources),
            "enabled_sources": sum(1 for source in sources.values() if source.enabled),
        },
        "brand_weights": brand_weights,
        "brand_weight_profile": build_brand_weight_profile(brand_weights, market_summary["brands"]),
        "market_alerts": market_alerts,
        "focus_queue": build_focus_queue(brand_weights, items, events, market_summary["brands"]),
        "opportunity_radar": build_opportunity_radar(brand_weights, market_summary["brands"]),
        "market": {
            "observations": market_observations,
            "summary": market_summary,
            "momentum": momentum,
            "patterns": build_pattern_radar(brand_weights, market_observations),
            "sample_plan": build_sample_collection_plan(brand_weights, market_summary["brands"]),
        },
        "feed": build_home_feed(events, items, market_summary, market_alerts, momentum, source_runs, brand_weights),
        "sources": [source_to_dict(source) for source in sources.values()],
        "source_runs": source_runs,
        "items": items,
        "events": events,
    }


def source_to_dict(source: SourceConfig) -> dict[str, Any]:
    return {
        "name": source.name,
        "type": source.type,
        "url": source.url,
        "enabled": source.enabled,
        "keywords": list(source.keywords),
    }


def event_to_dict(event: RadarEvent) -> dict[str, Any]:
    return {
        "source": event.source,
        "event_type": event.event_type.value,
        "title": event.item.title,
        "url": event.item.url,
        "status": event.item.status.value,
        "previous_title": event.previous_title,
        "previous_status": event.previous_status,
        "content_hash": event.item.content_hash,
        "previous_content_hash": event.previous_content_hash,
        "created_at": event.created_at,
    }


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


FEED_INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Lolita Radar OS</title>
    <style>
      :root {
        color-scheme: light;
        --ink: #251f28;
        --muted: #746a74;
        --line: #eadfe4;
        --paper: #fffaf8;
        --rose: #b84d68;
        --teal: #26716e;
        --gold: #a56b23;
        --blue: #476987;
        --warn: #a23a34;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        color: var(--ink);
        background: #fbf7f4;
        font: 14px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      a { color: inherit; text-decoration: none; }
      .app { max-width: 1120px; margin: 0 auto; padding: 18px; }
      .topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
      .brand h1 { margin: 0; font: 700 24px/1.1 Georgia, "Times New Roman", serif; }
      .brand p { margin: 4px 0 0; color: var(--muted); }
      .actions { display: flex; gap: 8px; }
      button {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fff;
        color: var(--ink);
        min-height: 36px;
        padding: 0 12px;
        cursor: pointer;
      }
      button.active { border-color: var(--rose); color: var(--rose); background: #fff5f6; }
      .summary {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 10px;
        margin-bottom: 12px;
      }
      .summary-card, .feed-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--paper);
      }
      .summary-card { padding: 12px; }
      .summary-card strong { display: block; font-size: 22px; line-height: 1; }
      .summary-card span { color: var(--muted); }
      .filters { display: flex; gap: 8px; overflow-x: auto; padding: 4px 0 12px; }
      .feed-stream { display: grid; gap: 10px; }
      .feed-card {
        display: block;
        padding: 13px;
        transition: border-color .15s ease, transform .15s ease;
      }
      .feed-card:hover { border-color: #d5b5c0; transform: translateY(-1px); }
      .feed-head { display: flex; justify-content: space-between; align-items: start; gap: 10px; margin-bottom: 8px; }
      .badge { display: inline-flex; align-items: center; min-height: 24px; border-radius: 999px; padding: 0 8px; font-size: 12px; color: #fff; }
      .release { background: var(--rose); }
      .drop { background: var(--teal); }
      .trend { background: var(--blue); }
      .alert { background: var(--warn); }
      .kind { color: var(--muted); font-size: 12px; }
      .feed-card h2 { margin: 0; font-size: 17px; line-height: 1.25; overflow-wrap: anywhere; }
      .meta { margin: 7px 0 0; color: var(--muted); overflow-wrap: anywhere; }
      .foot { display: flex; justify-content: space-between; gap: 10px; margin-top: 10px; color: var(--muted); font-size: 12px; }
      .cta { color: var(--rose); font-weight: 700; }
      .empty { border: 1px dashed var(--line); border-radius: 8px; padding: 18px; color: var(--muted); text-align: center; }
      @media (max-width: 720px) {
        .app { padding: 14px; }
        .topbar { align-items: start; flex-direction: column; }
        .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .actions { width: 100%; }
        .actions button { flex: 1; }
      }
    </style>
  </head>
  <body>
    <main class="app">
      <header class="topbar">
        <div class="brand">
          <h1>Lolita Radar OS</h1>
          <p>日牌发售与二级市场信息流</p>
        </div>
        <div class="actions">
          <button id="refreshBtn" type="button">Refresh</button>
          <button id="checkBtn" type="button">Check</button>
        </div>
      </header>
      <section class="summary" aria-label="Summary">
        <article class="summary-card"><strong id="dropsCount">0</strong><span>🔥 Drops</span></article>
        <article class="summary-card"><strong id="shopsCount">0</strong><span>🛒 Shops</span></article>
        <article class="summary-card"><strong id="trendsCount">0</strong><span>📈 Trends</span></article>
        <article class="summary-card"><strong id="alertsCount">0</strong><span>⚠️ Alerts</span></article>
      </section>
      <nav class="filters" aria-label="Feed filter">
        <button class="active" data-filter="all" type="button">All</button>
        <button data-filter="release" type="button">Release</button>
        <button data-filter="drop" type="button">Drop</button>
        <button data-filter="trend" type="button">Trend</button>
        <button data-filter="alert" type="button">Alert</button>
      </nav>
      <section id="feedStream" class="feed-stream" aria-live="polite"></section>
    </main>
    <script>
      const $ = (id) => document.getElementById(id);
      let state = {};
      let activeFilter = "all";
      async function api(path, options = {}) {
        const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
        if (!response.ok) throw new Error(await response.text());
        return response.json();
      }
      async function load() {
        state = await api("/api/state");
        render();
      }
      function render() {
        const feed = state.feed || { summary: {}, streams: {}, all: [] };
        $("dropsCount").textContent = feed.summary?.drops || 0;
        $("shopsCount").textContent = feed.summary?.shops || 0;
        $("trendsCount").textContent = feed.summary?.trends || 0;
        $("alertsCount").textContent = feed.summary?.alerts || 0;
        const rows = activeFilter === "all" ? (feed.all || []) : (feed.streams?.[activeFilter] || []);
        $("feedStream").innerHTML = rows.length ? rows.map(cardHtml).join("") : `<div class="empty">暂无 ${escapeHtml(activeFilter)} feed</div>`;
      }
      function cardHtml(row) {
        const type = row.feed_type || "alert";
        const href = row.url || "#";
        const confidence = row.confidence !== undefined ? ` · confidence ${row.confidence}` : "";
        return `<a class="feed-card" href="${escapeHtml(href)}" target="${row.url ? "_blank" : "_self"}" rel="noreferrer">
          <div class="feed-head">
            <span class="badge ${escapeHtml(type)}">${escapeHtml(type.toUpperCase())} · ${escapeHtml(row.brand || "-")}</span>
            <span class="kind">${escapeHtml(row.kind || "")}</span>
          </div>
          <h2>${escapeHtml(row.title || "-")}</h2>
          <p class="meta">${escapeHtml(row.meta || "")}${escapeHtml(confidence)}</p>
          <div class="foot"><span>${escapeHtml(row.time || "")}</span><span class="cta">Open source</span></div>
        </a>`;
      }
      function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
      }
      document.querySelectorAll("[data-filter]").forEach((button) => {
        button.addEventListener("click", () => {
          activeFilter = button.dataset.filter;
          document.querySelectorAll("[data-filter]").forEach((item) => item.classList.toggle("active", item === button));
          render();
        });
      });
      $("refreshBtn").addEventListener("click", load);
      $("checkBtn").addEventListener("click", async () => {
        state = await api("/api/check", { method: "POST", body: JSON.stringify({ notify: false }) });
        render();
      });
      load().catch((error) => {
        $("feedStream").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
      });
    </script>
  </body>
</html>"""


INDEX_HTML = FEED_INDEX_HTML
