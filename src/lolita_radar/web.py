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
from .storage import connect, list_events, list_items, storage_counts


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
                    self.send_html(INDEX_HTML)
                elif parsed.path.startswith("/assets/"):
                    self.send_asset(parsed.path.removeprefix("/assets/"))
                elif parsed.path == "/api/health":
                    self.send_json({"ok": True})
                elif parsed.path == "/api/state":
                    self.send_json(get_dashboard_state(config_path, db_path, brands_path, market_path))
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
            state = get_dashboard_state(config_path, db_path, brands_path, market_path)
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
            state = get_dashboard_state(config_path, db_path, brands_path, market_path)
            state.update({"added_market_observation": observation})
            self.send_json(state, status=HTTPStatus.CREATED)

        def handle_brand_weights(self) -> None:
            payload = self.read_json(default={})
            rows = payload.get("weights") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("brand weight update must include a weights list")
            updated = save_brand_weights(brands_path, rows)
            state = get_dashboard_state(config_path, db_path, brands_path, market_path)
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


def get_dashboard_state(
    config_path: Path,
    db_path: Path,
    brands_path: Path | None = None,
    market_path: Path | None = None,
) -> dict[str, Any]:
    sources = load_sources(config_path)
    brand_weights = load_brand_weights(brands_path)
    market_observations = load_market_observations(market_path)
    market_summary = summarize_market_observations(market_observations, brand_weights)
    connection = connect(db_path)
    try:
        counts = storage_counts(connection)
        items = list_items(connection, limit=100)
        events = list_events(connection, limit=100)
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
        "market_alerts": build_market_alerts(brand_weights, market_summary),
        "focus_queue": build_focus_queue(brand_weights, items, events, market_summary["brands"]),
        "opportunity_radar": build_opportunity_radar(brand_weights, market_summary["brands"]),
        "market": {
            "observations": market_observations,
            "summary": market_summary,
            "momentum": build_market_momentum(market_observations, brand_weights),
            "patterns": build_pattern_radar(brand_weights, market_observations),
            "sample_plan": build_sample_collection_plan(brand_weights, market_summary["brands"]),
        },
        "sources": [source_to_dict(source) for source in sources.values()],
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
        "created_at": event.created_at,
    }


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Lolita Premium Radar</title>
    <style>
      :root {
        --bg: #f4eee9;
        --bg-soft: #fff8f5;
        --panel: #fffdfb;
        --ivory: #fffaf2;
        --porcelain: #fffefb;
        --powder: #f7e4e8;
        --mint: #eaf6f1;
        --satin: #f6d8df;
        --velvet: #421323;
        --text: #24171f;
        --muted: #766871;
        --line: #e4d3cf;
        --lace: #f0e5df;
        --ink: #20151d;
        --rose: #b4576f;
        --rose-dark: #7c3148;
        --wine: #611b31;
        --teal: #0f6760;
        --gold: #a9782c;
        --warn: #a44322;
        --theme-rose-rgb: 180,87,111;
        --theme-wine-rgb: 97,27,49;
        --button-top: #93415b;
        --topbar-bg:
          radial-gradient(circle at 92% 28%, rgba(246,216,223,.22) 0 2px, transparent 2px),
          radial-gradient(circle at 18% 16%, rgba(255,255,255,.14) 0 1px, transparent 2px),
          repeating-linear-gradient(90deg, rgba(255,255,255,.045) 0 12px, rgba(255,255,255,0) 12px 24px),
          linear-gradient(135deg, rgba(136,59,80,.92), rgba(32,21,29,.96) 50%, rgba(15,111,106,.86)),
          #241c21;
        --shadow: 0 18px 44px rgba(63, 39, 47, .13);
        --paper-shadow: inset 0 0 0 3px rgba(255,255,255,.44), 0 14px 30px rgba(63,39,47,.09);
        --pearl-shadow: 0 1px 0 rgba(255,255,255,.88), 0 7px 18px rgba(97,27,49,.1);
        --ribbon-shadow: 0 11px 22px rgba(97,27,49,.16);
      }
      :root[data-lolita-theme="sweet"] {
        --bg: #f7edf1;
        --bg-soft: #fff5f8;
        --panel: #fffbfd;
        --ivory: #fff7f1;
        --porcelain: #fffefe;
        --powder: #f8dce7;
        --mint: #ecf8f3;
        --satin: #f4c7d5;
        --velvet: #4a1429;
        --text: #2d1722;
        --muted: #7b6672;
        --line: #ead0da;
        --lace: #f4e0e8;
        --ink: #24131d;
        --rose: #c45f82;
        --rose-dark: #84314f;
        --wine: #681932;
        --teal: #2f756a;
        --gold: #b88735;
        --theme-rose-rgb: 196,95,130;
        --theme-wine-rgb: 104,25,50;
        --button-top: #b45373;
        --topbar-bg:
          radial-gradient(circle at 92% 28%, rgba(255,232,240,.34) 0 2px, transparent 2px),
          radial-gradient(circle at 18% 16%, rgba(255,255,255,.2) 0 1px, transparent 2px),
          repeating-linear-gradient(90deg, rgba(255,255,255,.06) 0 12px, rgba(255,255,255,0) 12px 24px),
          linear-gradient(135deg, rgba(188,86,119,.94), rgba(78,24,45,.96) 52%, rgba(47,117,106,.82)),
          #351724;
      }
      :root[data-lolita-theme="classic"] {
        --bg: #f4eee9;
        --bg-soft: #fff8f5;
        --panel: #fffdfb;
        --ivory: #fffaf2;
        --porcelain: #fffefb;
        --powder: #f7e4e8;
        --mint: #eaf6f1;
        --satin: #f6d8df;
        --velvet: #421323;
        --text: #24171f;
        --muted: #766871;
        --line: #e4d3cf;
        --lace: #f0e5df;
        --ink: #20151d;
        --rose: #b4576f;
        --rose-dark: #7c3148;
        --wine: #611b31;
        --teal: #0f6760;
        --gold: #a9782c;
        --theme-rose-rgb: 180,87,111;
        --theme-wine-rgb: 97,27,49;
        --button-top: #93415b;
      }
      :root[data-lolita-theme="gothic"] {
        --bg: #ede8ea;
        --bg-soft: #fbf8fa;
        --panel: #fffdfd;
        --ivory: #f9f3ed;
        --porcelain: #fffdfd;
        --powder: #eadbe4;
        --mint: #e7f2f0;
        --satin: #d8b7c6;
        --velvet: #1f141c;
        --text: #211720;
        --muted: #716673;
        --line: #d8ccd4;
        --lace: #e9dee5;
        --ink: #181116;
        --rose: #7b2b4b;
        --rose-dark: #4d1930;
        --wine: #421127;
        --teal: #245f61;
        --gold: #9b7a37;
        --theme-rose-rgb: 123,43,75;
        --theme-wine-rgb: 66,17,39;
        --button-top: #6f2945;
        --topbar-bg:
          radial-gradient(circle at 92% 28%, rgba(216,183,198,.26) 0 2px, transparent 2px),
          radial-gradient(circle at 18% 16%, rgba(255,255,255,.13) 0 1px, transparent 2px),
          repeating-linear-gradient(90deg, rgba(255,255,255,.04) 0 12px, rgba(255,255,255,0) 12px 24px),
          linear-gradient(135deg, rgba(66,17,39,.96), rgba(24,17,22,.98) 54%, rgba(36,95,97,.88)),
          #181116;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background:
          radial-gradient(circle at 50% 0, rgba(255,255,255,.72) 0 7px, transparent 7px) 0 0 / 22px 14px repeat-x,
          radial-gradient(circle at 50% 14px, rgba(var(--theme-rose-rgb), .11) 0 1px, transparent 2px) 0 0 / 22px 14px repeat-x,
          radial-gradient(circle at 16px 16px, rgba(var(--theme-rose-rgb), .09) 0 2px, transparent 2px),
          linear-gradient(90deg, rgba(var(--theme-wine-rgb), .045) 1px, transparent 1px),
          linear-gradient(rgba(15,103,96,.035) 1px, transparent 1px),
          repeating-linear-gradient(90deg, rgba(255,255,255,.34) 0 18px, rgba(255,255,255,0) 18px 36px),
          var(--bg);
        background-size: 22px 14px, 22px 14px, 32px 32px, 28px 28px, 28px 28px, 72px 72px, auto;
        color: var(--text);
        font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      a { color: var(--teal); text-decoration: none; }
      a:hover { text-decoration: underline; }
      button {
        min-height: 36px;
        border: 1px solid rgba(255,255,255,.22);
        border-radius: 6px;
        padding: 0 13px;
        color: #fff;
        background: linear-gradient(180deg, var(--button-top), var(--rose-dark));
        box-shadow: inset 0 1px 0 rgba(255,255,255,.2), 0 8px 18px rgba(97,27,49,.16);
        cursor: pointer;
        font: inherit;
      }
      button:hover { filter: brightness(1.04); }
      button:focus-visible, input:focus-visible, select:focus-visible {
        outline: 2px solid rgba(169,120,44,.55);
        outline-offset: 2px;
      }
      button.secondary { background: linear-gradient(180deg, #3f5a63, #2f424a); }
      button[disabled] { background: #ad9fa5; }
      button:disabled { opacity: .65; cursor: wait; }
      .topbar {
        position: relative;
        isolation: isolate;
        display: grid;
        grid-template-columns: minmax(260px, 1fr) minmax(240px, 340px) auto;
        gap: 18px;
        align-items: center;
        padding: 28px 24px 20px;
        color: #fff;
        background: var(--topbar-bg);
        border-bottom: 5px double rgba(255,255,255,.24);
        overflow: hidden;
      }
      .topbar > div, .hero-visual, .actions {
        position: relative;
        z-index: 1;
      }
      .topbar::before {
        content: "";
        position: absolute;
        inset: 9px 10px auto;
        height: 9px;
        border-top: 1px solid rgba(255,255,255,.24);
        border-bottom: 1px solid rgba(255,255,255,.18);
        background: radial-gradient(circle at 8px 9px, rgba(255,255,255,.26) 0 6px, transparent 6px) 0 0 / 16px 9px repeat-x;
        pointer-events: none;
      }
      .topbar::after {
        content: "";
        position: absolute;
        left: 0;
        right: 0;
        bottom: -9px;
        height: 9px;
        background: radial-gradient(circle at 10px 0, rgba(255,255,255,.6) 0 9px, transparent 9px) 0 0 / 20px 9px repeat-x;
        opacity: .7;
      }
      .eyebrow { margin: 0 0 5px; color: #f1dad7; font-size: 12px; letter-spacing: 0; text-transform: uppercase; }
      .topbar h1 {
        position: relative;
        display: inline-block;
        margin: 0 0 2px;
        padding-right: 42px;
        font: 600 34px/1.02 Georgia, "Times New Roman", serif;
        text-shadow: 0 2px 0 rgba(0,0,0,.14);
      }
      .topbar h1::after {
        content: "";
        position: absolute;
        top: 50%;
        right: 0;
        width: 28px;
        height: 15px;
        transform: translateY(-45%);
        background:
          linear-gradient(45deg, transparent 0 34%, rgba(255,255,255,.76) 34% 66%, transparent 66%),
          linear-gradient(-45deg, transparent 0 34%, rgba(255,255,255,.76) 34% 66%, transparent 66%),
          var(--satin);
        border: 1px solid rgba(255,255,255,.55);
        border-radius: 2px;
        box-shadow: var(--pearl-shadow);
      }
      .topbar p { margin: 6px 0 0; max-width: 820px; color: #f2e8e6; word-break: break-word; }
      .hero-visual {
        position: relative;
        min-height: 190px;
        display: grid;
        align-content: end;
        gap: 10px;
        padding: 14px;
        border: 1px solid rgba(255,255,255,.22);
        border-radius: 8px;
        background:
          radial-gradient(circle at 50% 12%, rgba(255,255,255,.34), transparent 28%),
          linear-gradient(180deg, rgba(36,23,31,.04), rgba(36,23,31,.46)),
          url("/assets/lolita-radar-fabric.png") center / cover;
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.1), var(--ribbon-shadow);
        overflow: hidden;
      }
      .hero-visual::before {
        content: "";
        position: absolute;
        inset: 10px;
        border: 1px solid rgba(255,255,255,.34);
        border-radius: 6px;
        pointer-events: none;
      }
      .hero-visual::after {
        content: "";
        position: absolute;
        left: 0;
        right: 0;
        top: 0;
        height: 18px;
        background:
          radial-gradient(circle at 10px 0, rgba(255,253,251,.72) 0 9px, transparent 9px) 0 0 / 20px 18px repeat-x,
          linear-gradient(180deg, rgba(255,253,251,.28), transparent);
        pointer-events: none;
      }
      .hero-visual strong {
        position: relative;
        max-width: 210px;
        color: #fffdfb;
        font: 650 22px/1.05 Georgia, "Times New Roman", serif;
        text-shadow: 0 2px 10px rgba(35,15,22,.38);
      }
      .hero-pearls {
        position: relative;
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .hero-pearls span {
        min-height: 25px;
        display: inline-flex;
        align-items: center;
        padding: 0 8px;
        border: 1px solid rgba(255,255,255,.36);
        border-radius: 999px;
        background: rgba(255,253,251,.82);
        color: var(--wine);
        font-size: 12px;
        box-shadow: var(--pearl-shadow);
      }
      .style-compass {
        display: grid;
        grid-template-columns: repeat(5, minmax(82px, 1fr));
        gap: 7px;
        margin-top: 12px;
        max-width: 820px;
      }
      .style-compass-card {
        position: relative;
        display: grid;
        gap: 5px;
        min-height: 78px;
        padding: 9px 9px 10px;
        border: 1px solid rgba(255,255,255,.18);
        border-radius: 8px;
        background:
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 34%, transparent), transparent 40%),
          linear-gradient(135deg, rgba(255,255,255,.13), rgba(255,255,255,.05)),
          rgba(255,255,255,.07);
        box-shadow: inset 0 1px 0 rgba(255,255,255,.16), 0 10px 22px rgba(20,12,18,.16);
        overflow: hidden;
      }
      .style-compass-card::before {
        content: "";
        position: absolute;
        left: 0;
        right: 0;
        top: 0;
        height: 5px;
        background:
          radial-gradient(circle at 7px 0, rgba(255,255,255,.7) 0 5px, transparent 5px) 0 0 / 14px 5px repeat-x,
          linear-gradient(90deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .style-compass-card strong {
        color: #fffdfb;
        font: 650 14px/1.15 Georgia, "Times New Roman", serif;
        overflow-wrap: anywhere;
      }
      .style-compass-card span, .style-compass-card small {
        color: #f0e3e0;
        font-size: 11px;
      }
      .style-compass-foot {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
      }
      .style-compass-foot b {
        color: #fffdfb;
        font: 650 16px/1 Georgia, "Times New Roman", serif;
      }
      .style-compass-card .signal-bar {
        height: 8px;
        background: rgba(255,255,255,.2);
      }
      .actions { display: flex; gap: 9px; flex-wrap: wrap; justify-content: flex-end; }
      .preference-stack { display: grid; gap: 6px; justify-items: end; }
      .language-switch, .theme-switch { display: inline-flex; align-items: center; gap: 2px; padding: 2px; border: 1px solid rgba(255,255,255,.18); border-radius: 7px; background: rgba(255,255,255,.08); }
      .language-switch button, .theme-switch button { min-height: 32px; padding: 0 10px; border-radius: 5px; background: transparent; color: #c9d6dc; box-shadow: none; }
      .language-switch button.active, .theme-switch button.active { background: #fff; color: #14242d; }
      .theme-switch button { display: inline-flex; align-items: center; gap: 6px; }
      .theme-swatch {
        width: 10px;
        height: 10px;
        flex: 0 0 auto;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,.72);
        background: var(--swatch, var(--rose));
        box-shadow: var(--pearl-shadow);
      }
      .theme-swatch.sweet { --swatch: #c45f82; }
      .theme-swatch.classic { --swatch: #a9782c; }
      .theme-swatch.gothic { --swatch: #421127; }
      .metrics { display: grid; grid-template-columns: repeat(5, minmax(132px, 1fr)); gap: 12px; padding: 22px 20px 12px; }
      .north-star-board { margin: 0 20px 14px; }
      .north-star-grid { display: grid; grid-template-columns: minmax(240px, .72fr) minmax(360px, 1.28fr); gap: 12px; padding: 12px; }
      .north-star-brief, .north-star-card {
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 18%, var(--line));
        border-radius: 8px;
        background: #fffaf8;
      }
      .north-star-brief {
        position: relative;
        display: grid;
        gap: 10px;
        align-content: start;
        padding: 13px;
        background:
          radial-gradient(circle at 100% 0, rgba(180,87,111,.13), transparent 36%),
          radial-gradient(circle at 0 100%, rgba(15,103,96,.1), transparent 38%),
          linear-gradient(135deg, rgba(255,247,232,.82), rgba(248,251,250,.94));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
        overflow: hidden;
      }
      .north-star-brief::before {
        content: "";
        position: absolute;
        inset: 8px;
        border: 1px double rgba(169,120,44,.24);
        border-radius: 6px;
        pointer-events: none;
      }
      .north-star-brief::after, .north-star-card::after {
        content: "";
        position: absolute;
        left: 12px;
        right: 12px;
        bottom: 8px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.34) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .north-star-brief strong { color: var(--wine); font: 650 38px/1 Georgia, "Times New Roman", serif; }
      .north-star-brief p, .north-star-card p { margin: 0; color: var(--muted); }
      .north-star-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
      .north-star-stats span {
        display: grid;
        gap: 3px;
        min-height: 50px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 11px;
      }
      .north-star-stats strong { color: var(--wine); font: 650 18px/1 Georgia, "Times New Roman", serif; }
      .north-star-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
      .north-star-card {
        position: relative;
        display: grid;
        gap: 8px;
        min-height: 124px;
        padding: 12px 12px 15px;
        background:
          radial-gradient(circle at 50% 0, rgba(255,255,255,.9) 0 5px, transparent 5px) 0 0 / 18px 10px repeat-x,
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 13%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 66%, #fff), rgba(248,251,250,.92));
        box-shadow: var(--paper-shadow);
        overflow: hidden;
      }
      .north-star-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .north-star-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .north-star-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .north-star-score { font: 650 28px/1 Georgia, "Times New Roman", serif; color: var(--wine); white-space: nowrap; }
      .crown-board { margin: 0 20px 14px; }
      .crown-grid { display: grid; grid-template-columns: minmax(220px, .55fr) minmax(420px, 1.45fr); gap: 12px; padding: 12px; }
      .crown-brief, .crown-card {
        border: 1px solid color-mix(in srgb, var(--gold) 24%, var(--line));
        border-radius: 8px;
        background: #fffaf8;
        box-shadow: var(--paper-shadow);
      }
      .crown-brief {
        position: relative;
        display: grid;
        gap: 10px;
        align-content: start;
        padding: 13px;
        overflow: hidden;
        background:
          radial-gradient(circle at 50% 0, rgba(169,120,44,.16), transparent 36%),
          radial-gradient(circle at 0 100%, rgba(var(--theme-rose-rgb), .12), transparent 38%),
          linear-gradient(135deg, rgba(255,248,236,.9), rgba(248,251,250,.94));
      }
      .crown-brief::before, .crown-card::before {
        content: "";
        position: absolute;
        top: 0;
        left: 10px;
        right: 10px;
        height: 7px;
        background:
          radial-gradient(circle at 6px 0, rgba(169,120,44,.5) 0 5px, transparent 5px) 0 0 / 18px 7px repeat-x;
        pointer-events: none;
      }
      .crown-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .crown-brief p, .crown-card p { margin: 0; color: var(--muted); }
      .crown-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
      .crown-stats span {
        display: grid;
        gap: 3px;
        min-height: 50px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.76);
        color: var(--muted);
        font-size: 11px;
      }
      .crown-stats strong { color: var(--wine); font: 650 18px/1 Georgia, "Times New Roman", serif; }
      .crown-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
      .crown-card {
        position: relative;
        display: grid;
        gap: 9px;
        min-height: 184px;
        padding: 14px 12px 12px;
        overflow: hidden;
        background:
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 14%, transparent), transparent 42%),
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 20px 20px,
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 68%, #fff), rgba(255,253,251,.94));
      }
      .crown-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .crown-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; overflow-wrap: anywhere; }
      .crown-score { font: 650 30px/1 Georgia, "Times New Roman", serif; color: var(--wine); white-space: nowrap; }
      .crown-score-stack { display: grid; justify-items: end; gap: 6px; }
      .crown-meta, .crown-keywords { display: flex; flex-wrap: wrap; gap: 6px; }
      .crown-meta span, .crown-keywords span {
        padding: 4px 7px;
        border-radius: 999px;
        border: 1px solid rgba(97,27,49,.1);
        background: rgba(255,255,255,.68);
        color: var(--muted);
        font-size: 11px;
      }
      .crown-actions { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; }
      .crown-actions button, .crown-actions a {
        min-height: 30px;
        padding: 0 9px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 18%, var(--line));
        background: rgba(255,255,255,.74);
        color: var(--wine);
        font-size: 12px;
        text-decoration: none;
      }
      .draft-risk-board { margin: 0 20px 14px; }
      .draft-risk-grid { display: grid; grid-template-columns: minmax(220px, .58fr) minmax(360px, 1.42fr); gap: 12px; padding: 12px; }
      .draft-risk-brief, .draft-risk-card {
        border: 1px solid color-mix(in srgb, var(--warn) 16%, var(--line));
        border-radius: 8px;
        background: #fffaf8;
        box-shadow: var(--paper-shadow);
      }
      .draft-risk-brief {
        display: grid;
        gap: 10px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(199,87,58,.12), transparent 35%),
          linear-gradient(135deg, rgba(255,248,236,.86), rgba(248,251,250,.94));
      }
      .draft-risk-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .draft-risk-brief p, .draft-risk-card p { margin: 0; color: var(--muted); }
      .draft-risk-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
      .draft-risk-stats span {
        display: grid;
        gap: 3px;
        min-height: 50px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.76);
        color: var(--muted);
        font-size: 11px;
      }
      .draft-risk-stats strong { color: var(--wine); font: 650 18px/1 Georgia, "Times New Roman", serif; }
      .draft-risk-list { display: grid; gap: 8px; }
      .draft-risk-card {
        display: grid;
        grid-template-columns: minmax(68px, .28fr) minmax(0, 1fr) auto;
        gap: 10px;
        align-items: center;
        padding: 10px;
      }
      .draft-risk-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .draft-risk-actions { display: flex; flex-wrap: wrap; gap: 7px; }
      .draft-risk-actions button { min-height: 30px; padding-inline: 10px; }
      .style-premium-board { margin: 0 20px 14px; }
      .style-premium-grid { display: grid; grid-template-columns: minmax(220px, .55fr) minmax(360px, 1.45fr); gap: 12px; padding: 12px; }
      .style-premium-brief, .style-premium-card {
        position: relative;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 20%, var(--line));
        border-radius: 8px;
        background:
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 15%, transparent), transparent 36%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 74%, #fff), rgba(255,253,251,.94));
        box-shadow: var(--paper-shadow);
        overflow: hidden;
      }
      .style-premium-brief {
        display: grid;
        gap: 10px;
        align-content: start;
        padding: 12px;
      }
      .style-premium-brief::before, .style-premium-card::before {
        content: "";
        position: absolute;
        inset: 0 0 auto;
        height: 5px;
        background:
          radial-gradient(circle at 8px 0, rgba(255,255,255,.82) 0 5px, transparent 5px) 0 0 / 16px 5px repeat-x,
          linear-gradient(90deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .style-premium-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .style-premium-brief p, .style-premium-card p { margin: 0; color: var(--muted); }
      .style-premium-stats {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 7px;
      }
      .style-premium-stats span {
        display: grid;
        gap: 3px;
        min-height: 50px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.74);
        color: var(--muted);
        font-size: 11px;
      }
      .style-premium-stats strong { color: var(--wine); font: 650 18px/1 Georgia, "Times New Roman", serif; }
      .style-premium-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 8px; }
      .style-premium-card {
        display: grid;
        gap: 8px;
        min-height: 160px;
        padding: 12px;
      }
      .style-premium-card header { display: flex; justify-content: space-between; gap: 8px; align-items: start; }
      .style-premium-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .style-premium-meta { display: flex; flex-wrap: wrap; gap: 5px; }
      .style-premium-meta span {
        display: inline-flex;
        align-items: center;
        min-height: 22px;
        padding: 0 7px;
        border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 28%, var(--line));
        border-radius: 999px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 12px;
      }
      .radar-nav {
        position: sticky;
        top: 0;
        z-index: 5;
        display: flex;
        gap: 7px;
        align-items: center;
        padding: 8px 20px 10px;
        overflow-x: auto;
        background:
          linear-gradient(180deg, rgba(244,238,233,.95), rgba(244,238,233,.86)),
          var(--bg);
        border-bottom: 1px solid rgba(97,27,49,.1);
        backdrop-filter: blur(8px);
      }
      .radar-nav button {
        min-height: 32px;
        flex: 0 0 auto;
        padding: 0 10px;
        border: 1px solid rgba(97,27,49,.12);
        border-radius: 999px;
        background: rgba(255,253,251,.78);
        box-shadow: var(--pearl-shadow);
        color: var(--wine);
      }
      .radar-nav button:hover { background: #fff; }
      .daily-board { margin: 0 20px 14px; }
      .daily-grid { display: grid; grid-template-columns: minmax(220px, .58fr) minmax(360px, 1.42fr); gap: 12px; padding: 12px; }
      .daily-brief, .daily-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .daily-brief {
        position: relative;
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(180,87,111,.12), transparent 36%),
          linear-gradient(135deg, rgba(255,247,232,.78), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
        overflow: hidden;
      }
      .daily-brief::after {
        content: "";
        position: absolute;
        left: 12px;
        right: 12px;
        bottom: 8px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.36) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .daily-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .daily-brief p, .daily-card p { margin: 0; color: var(--muted); }
      .daily-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
      .daily-stat {
        display: grid;
        gap: 3px;
        min-height: 52px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
      }
      .daily-stat strong { color: var(--wine); font: 650 20px/1 Georgia, "Times New Roman", serif; }
      .daily-stat span { color: var(--muted); font-size: 11px; }
      .daily-lanes { display: grid; gap: 6px; }
      .daily-lane {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 42px;
        gap: 8px;
        align-items: center;
        width: 100%;
        min-height: 30px;
        padding: 6px 8px;
        border: 1px dashed rgba(97,27,49,.14);
        border-radius: 7px;
        background: rgba(255,253,251,.66);
        box-shadow: none;
        color: var(--muted);
        cursor: pointer;
        font-size: 12px;
        text-align: left;
      }
      .daily-lane:hover, .daily-lane.active {
        border-color: rgba(180,87,111,.32);
        background:
          linear-gradient(90deg, rgba(255,253,251,.9), color-mix(in srgb, var(--brand-paper, #fff3f6) 72%, #fff));
        filter: none;
      }
      .daily-lane strong { color: var(--wine); overflow-wrap: anywhere; }
      .daily-lane span { text-align: right; color: var(--brand-accent, var(--rose)); font-weight: 650; }
      .daily-lane.active span { color: var(--wine); }
      .daily-list { display: grid; gap: 9px; }
      .daily-card {
        position: relative;
        display: grid;
        gap: 8px;
        padding: 12px 12px 14px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 12%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 64%, #fff), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.38);
        overflow: hidden;
      }
      .daily-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .daily-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .daily-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .daily-card-actions { display: flex; flex-wrap: wrap; gap: 7px; }
      .daily-card-actions button { min-height: 30px; padding-inline: 10px; }
      .run-sheet-board { margin: 0 20px 14px; }
      .run-sheet-grid { display: grid; grid-template-columns: minmax(230px, .62fr) minmax(330px, 1.38fr); gap: 12px; padding: 12px; }
      .run-sheet-brief, .run-sheet-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .run-sheet-brief {
        position: relative;
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(180,87,111,.13), transparent 36%),
          linear-gradient(135deg, rgba(255,243,246,.82), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
        overflow: hidden;
      }
      .run-sheet-brief::after, .run-sheet-card::after {
        content: "";
        position: absolute;
        left: 12px;
        right: 12px;
        bottom: 8px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.34) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .run-sheet-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .run-sheet-brief p, .run-sheet-card p { margin: 0; color: var(--muted); }
      .run-sheet-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
      .run-sheet-stats span {
        display: grid;
        gap: 3px;
        min-height: 48px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 11px;
      }
      .run-sheet-stats strong { color: var(--wine); font: 650 18px/1 Georgia, "Times New Roman", serif; }
      .run-sheet-list { display: grid; gap: 8px; }
      .run-sheet-card {
        position: relative;
        display: grid;
        gap: 8px;
        padding: 12px 12px 14px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 13%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 66%, #fff), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.38);
        overflow: hidden;
      }
      .run-sheet-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .run-sheet-card header { display: flex; align-items: start; justify-content: space-between; gap: 10px; }
      .run-sheet-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .run-sheet-meta, .run-sheet-actions { display: flex; flex-wrap: wrap; gap: 6px; }
      .run-sheet-meta span {
        display: inline-flex;
        align-items: center;
        min-height: 24px;
        padding: 0 7px;
        border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 24%, var(--line));
        border-radius: 999px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 12px;
      }
      .run-sheet-actions a, .run-sheet-actions button {
        min-height: 28px;
        display: inline-flex;
        align-items: center;
        padding: 0 8px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 22%, var(--line));
        border-radius: 999px;
        background: #fffdfb;
        color: color-mix(in srgb, var(--brand-accent, var(--rose)) 72%, var(--wine));
        box-shadow: none;
        font: inherit;
        font-size: 12px;
        text-decoration: none;
      }
      .metric, .panel, .atelier {
        background:
          radial-gradient(circle at 50% 0, rgba(255,255,255,.88) 0 5px, transparent 5px) 0 0 / 18px 10px repeat-x,
          radial-gradient(circle at 16px 16px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          repeating-linear-gradient(90deg, rgba(180,87,111,.035) 0 1px, transparent 1px 18px),
          linear-gradient(180deg, rgba(255,255,255,.72), rgba(255,253,251,.96)),
          var(--panel);
        border: 1px solid var(--line);
        border-radius: 8px;
        box-shadow: var(--shadow);
      }
      .metric {
        position: relative;
        min-height: 88px;
        display: grid;
        align-content: center;
        gap: 5px;
        padding: 13px 15px;
        border-top: 4px solid var(--rose);
        border-left: 1px solid rgba(169,120,44,.24);
        overflow: hidden;
      }
      .metric::before {
        content: "";
        position: absolute;
        inset: 6px 8px auto;
        height: 1px;
        background: linear-gradient(90deg, transparent, rgba(169,120,44,.36), transparent);
      }
      .metric::after {
        content: "";
        position: absolute;
        left: 12px;
        right: 12px;
        bottom: 6px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.42) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
      }
      .metric strong { font: 650 27px/1 Georgia, "Times New Roman", serif; color: var(--wine); }
      .metric span, .muted { color: var(--muted); }
      .atelier { position: relative; margin: 0 20px 14px; padding: 14px; display: grid; grid-template-columns: minmax(220px, .7fr) 1fr; gap: 14px; }
      .atelier h2, .panel h2 { margin: 0; font: 650 17px/1.2 Georgia, "Times New Roman", serif; }
      .atelier h2, .toolbar h2, .panel > h2 {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .atelier h2::before, .toolbar h2::before, .panel > h2::before {
        content: "";
        width: 10px;
        height: 10px;
        flex: 0 0 auto;
        border-radius: 999px;
        background: radial-gradient(circle at 35% 30%, #fff, #f4d2d9 56%, #b4576f 100%);
        box-shadow: var(--pearl-shadow);
      }
      .watch-grid { display: grid; grid-template-columns: repeat(4, minmax(125px, 1fr)); gap: 9px; }
      .brand-chip {
        position: relative;
        display: grid;
        gap: 9px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 14px 10px 12px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 18%, transparent), transparent 34%),
          linear-gradient(90deg, color-mix(in srgb, var(--brand-accent, var(--rose)) 12%, transparent), transparent 42%),
          var(--bg-soft);
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.42);
        overflow: hidden;
      }
      .brand-chip::before {
        content: "";
        position: absolute;
        left: 0;
        right: 0;
        top: 0;
        height: 6px;
        background:
          radial-gradient(circle at 8px 0, rgba(255,255,255,.85) 0 6px, transparent 6px) 0 0 / 16px 6px repeat-x,
          linear-gradient(90deg, var(--brand-accent, var(--rose)), var(--gold), var(--teal));
      }
      .brand-chip::after {
        content: "";
        position: absolute;
        inset: auto 9px 6px;
        height: 4px;
        background: radial-gradient(circle, color-mix(in srgb, var(--brand-accent, var(--rose)) 34%, transparent) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .brand-chip.theme-sweet { --brand-accent: #b4576f; --brand-paper: #fff3f6; }
      .brand-chip.theme-classic { --brand-accent: #a9782c; --brand-paper: #fff8ec; }
      .brand-chip.theme-gothic { --brand-accent: #611b31; --brand-paper: #fff3f5; }
      .brand-chip.theme-mint { --brand-accent: #0f6760; --brand-paper: #f1fbf8; }
      .brand-chip-header { display: grid; grid-template-columns: 54px minmax(0, 1fr); gap: 9px; align-items: center; }
      .brand-cameo {
        position: relative;
        display: grid;
        place-items: center;
        align-content: center;
        min-height: 54px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 35%, var(--line));
        border-radius: 999px;
        background:
          radial-gradient(circle at 50% 25%, rgba(255,255,255,.85), transparent 36%),
          var(--brand-paper, #fff3f6);
        box-shadow: var(--pearl-shadow);
      }
      .brand-cameo::before {
        content: "";
        position: absolute;
        top: -5px;
        width: 28px;
        height: 13px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 34%, #fff);
        border-radius: 3px;
        background:
          linear-gradient(45deg, transparent 0 35%, rgba(255,255,255,.82) 35% 65%, transparent 65%),
          linear-gradient(-45deg, transparent 0 35%, rgba(255,255,255,.82) 35% 65%, transparent 65%),
          color-mix(in srgb, var(--brand-accent, var(--rose)) 28%, #fff);
        box-shadow: var(--pearl-shadow);
      }
      .brand-cameo strong { font: 650 17px/1 Georgia, "Times New Roman", serif; }
      .brand-cameo span { color: var(--brand-accent, var(--rose)); font: 650 11px/1 Georgia, "Times New Roman", serif; }
      .brand-title { min-width: 0; }
      .brand-title strong, .brand-title span { overflow-wrap: anywhere; }
      .brand-title strong { font-family: Georgia, "Times New Roman", serif; }
      .brand-ribbon { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
      .brand-ribbon span {
        display: inline-flex;
        align-items: center;
        min-height: 22px;
        padding: 0 7px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 999px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 12px;
      }
      .brand-ribbon span:first-child { background: var(--brand-paper, #fff3f6); color: var(--brand-accent, var(--rose)); }
      .brand-keywords { display: flex; flex-wrap: wrap; gap: 5px; min-height: 23px; }
      .brand-keywords span {
        display: inline-flex;
        align-items: center;
        min-height: 22px;
        padding: 0 7px;
        border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 28%, var(--line));
        border-radius: 999px;
        background: rgba(255,255,255,.54);
        color: var(--muted);
        font-size: 12px;
      }
      .brand-chip.dirty { border-color: rgba(169,120,44,.68); box-shadow: inset 0 0 0 1px rgba(169,120,44,.2); }
      .brand-chip strong { display: block; color: var(--wine); }
      .brand-chip span { color: var(--muted); font-size: 12px; }
      .brand-cameo span { color: var(--brand-accent, var(--rose)); font: 650 11px/1 Georgia, "Times New Roman", serif; }
      .brand-ribbon span:first-child { color: var(--brand-accent, var(--rose)); }
      .brand-chip input[type="range"] { width: 100%; accent-color: var(--rose-dark); }
      .weight-control { display: grid; gap: 4px; margin-top: 7px; color: var(--muted); font-size: 12px; }
      .brand-identity {
        display: grid;
        gap: 5px;
        padding: 8px 9px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 18%, var(--line));
        border-radius: 7px;
        background:
          radial-gradient(circle at 12px 10px, rgba(255,255,255,.78) 0 2px, transparent 2px) 0 0 / 18px 18px,
          color-mix(in srgb, var(--brand-paper, #fff3f6) 74%, #fff);
      }
      .brand-identity span {
        color: var(--brand-accent, var(--rose));
        font-weight: 650;
      }
      .brand-identity p { margin: 0; color: var(--muted); font-size: 12px; }
      .weight-insight { display: grid; gap: 5px; margin-top: 8px; padding-top: 8px; border-top: 1px dashed rgba(97,27,49,.16); }
      .weight-insight p { margin: 0; color: var(--muted); font-size: 12px; }
      .weight-insight strong { display: inline; color: var(--wine); }
      .brand-tools { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 9px; }
      .brand-actions { display: flex; align-items: center; justify-content: flex-end; gap: 7px; flex-wrap: wrap; }
      .brand-style-ledger {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 8px;
        margin: 0 0 10px;
      }
      .style-ledger-card {
        position: relative;
        display: grid;
        gap: 7px;
        min-height: 132px;
        padding: 11px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 22%, var(--line));
        border-radius: 8px;
        background:
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 16%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 74%, #fff), rgba(255,253,251,.94));
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.38);
        overflow: hidden;
      }
      .style-ledger-card::before {
        content: "";
        position: absolute;
        left: 0;
        right: 0;
        top: 0;
        height: 5px;
        background:
          radial-gradient(circle at 8px 0, rgba(255,255,255,.82) 0 5px, transparent 5px) 0 0 / 16px 5px repeat-x,
          linear-gradient(90deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .style-ledger-card header { display: flex; justify-content: space-between; gap: 8px; align-items: start; }
      .style-ledger-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .style-ledger-score { font: 650 26px/1 Georgia, "Times New Roman", serif; color: var(--wine); }
      .style-ledger-meta, .style-ledger-keywords { display: flex; gap: 5px; flex-wrap: wrap; }
      .style-ledger-meta span, .style-ledger-keywords span {
        display: inline-flex;
        align-items: center;
        min-height: 22px;
        padding: 0 7px;
        border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 28%, var(--line));
        border-radius: 999px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 12px;
      }
      .weight-salon {
        display: grid;
        grid-template-columns: minmax(210px, .42fr) minmax(0, 1fr);
        gap: 9px;
        margin: 0 0 10px;
      }
      .weight-salon-brief, .weight-salon-card {
        position: relative;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 20%, var(--line));
        border-radius: 8px;
        background:
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 14%, transparent), transparent 36%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 76%, #fff), rgba(255,253,251,.95));
        box-shadow: var(--paper-shadow);
        overflow: hidden;
      }
      .weight-salon-brief {
        display: grid;
        align-content: start;
        gap: 10px;
        padding: 12px;
      }
      .weight-salon-brief::before, .weight-salon-card::before {
        content: "";
        position: absolute;
        inset: 0 0 auto;
        height: 5px;
        background:
          radial-gradient(circle at 8px 0, rgba(255,255,255,.82) 0 5px, transparent 5px) 0 0 / 16px 5px repeat-x,
          linear-gradient(90deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .weight-salon-brief strong { color: var(--wine); font: 650 28px/1 Georgia, "Times New Roman", serif; }
      .weight-salon-brief p, .weight-salon-card p { margin: 0; color: var(--muted); }
      .weight-salon-stats {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 7px;
      }
      .weight-salon-stats span {
        display: grid;
        gap: 3px;
        min-height: 48px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.74);
        color: var(--muted);
        font-size: 11px;
      }
      .weight-salon-stats strong { color: var(--wine); font: 650 17px/1 Georgia, "Times New Roman", serif; }
      .weight-salon-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(176px, 1fr)); gap: 8px; }
      .weight-salon-card {
        display: grid;
        gap: 8px;
        min-height: 150px;
        padding: 12px;
      }
      .weight-salon-card header { display: flex; justify-content: space-between; gap: 8px; align-items: start; }
      .weight-salon-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .weight-salon-track {
        display: grid;
        grid-template-columns: 1fr auto 1fr;
        gap: 7px;
        align-items: center;
      }
      .weight-salon-track span {
        display: grid;
        gap: 2px;
        color: var(--muted);
        font-size: 11px;
      }
      .weight-salon-track strong { font-size: 18px; }
      .weight-salon-meta { display: flex; flex-wrap: wrap; gap: 5px; }
      .weight-salon-meta span {
        display: inline-flex;
        align-items: center;
        min-height: 22px;
        padding: 0 7px;
        border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 28%, var(--line));
        border-radius: 999px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 12px;
      }
      .weight-scenarios {
        display: inline-flex;
        flex-wrap: wrap;
        gap: 5px;
        padding: 3px;
        border: 1px solid rgba(97,27,49,.12);
        border-radius: 8px;
        background: rgba(255,253,251,.62);
      }
      .weight-scenarios button { min-height: 28px; padding: 0 9px; }
      .weight-draft-audit {
        display: grid;
        gap: 7px;
        margin-top: 10px;
        padding: 10px;
        border: 1px solid rgba(169,120,44,.24);
        border-radius: 8px;
        background:
          radial-gradient(circle at 14px 14px, rgba(255,255,255,.86) 0 2px, transparent 2px) 0 0 / 22px 22px,
          linear-gradient(135deg, rgba(255,247,232,.72), rgba(248,251,250,.92));
      }
      .weight-draft-audit.empty {
        border-style: dashed;
        background: rgba(255,253,251,.55);
      }
      .weight-draft-summary {
        display: grid;
        grid-template-columns: repeat(4, minmax(78px, 1fr));
        gap: 7px;
      }
      .weight-draft-stat {
        display: grid;
        gap: 3px;
        min-height: 52px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
      }
      .weight-draft-stat strong { color: var(--wine); font: 650 20px/1 Georgia, "Times New Roman", serif; }
      .weight-draft-stat span { color: var(--muted); font-size: 11px; }
      .weight-draft-warnings { display: grid; gap: 6px; }
      .weight-draft-warning {
        display: grid;
        grid-template-columns: minmax(72px, auto) 1fr;
        gap: 8px;
        align-items: center;
        min-height: 34px;
        padding: 7px 8px;
        border: 1px solid rgba(180,87,111,.18);
        border-radius: 7px;
        background: linear-gradient(90deg, rgba(255,243,246,.75), rgba(255,253,251,.84));
        color: var(--muted);
        font-size: 12px;
      }
      .weight-draft-warning strong { color: var(--wine); }
      .weight-draft-head, .weight-draft-row {
        display: grid;
        grid-template-columns: minmax(110px, 1fr) 64px 64px 58px;
        gap: 8px;
        align-items: center;
      }
      .weight-draft-head { color: var(--muted); font-size: 12px; }
      .weight-draft-head strong { color: var(--wine); font-size: 13px; }
      .weight-draft-list { display: grid; gap: 5px; }
      .weight-draft-row {
        min-height: 32px;
        padding: 6px 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.74);
        color: var(--muted);
        font-size: 12px;
      }
      .weight-draft-row strong { color: var(--wine); }
      .weight-draft-delta { color: var(--gold); font-weight: 650; text-align: right; }
      .lookbook-board { margin: 0 20px 14px; }
      .lookbook-grid { display: grid; grid-template-columns: minmax(230px, .58fr) minmax(360px, 1.42fr); gap: 12px; padding: 12px; }
      .lookbook-brief, .lookbook-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .lookbook-brief {
        position: relative;
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(180,87,111,.12), transparent 36%),
          linear-gradient(135deg, rgba(255,247,232,.78), rgba(248,251,250,.9));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
        overflow: hidden;
      }
      .lookbook-brief::after {
        content: "";
        position: absolute;
        left: 12px;
        right: 12px;
        bottom: 8px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.36) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .lookbook-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .lookbook-brief p, .lookbook-card p { margin: 0; color: var(--muted); }
      .lookbook-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
      .lookbook-stat {
        display: grid;
        gap: 3px;
        min-height: 52px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
      }
      .lookbook-stat strong { color: var(--wine); font: 650 20px/1 Georgia, "Times New Roman", serif; }
      .lookbook-stat span { color: var(--muted); font-size: 11px; }
      .lookbook-rail { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
      .lookbook-card {
        position: relative;
        display: grid;
        gap: 9px;
        min-height: 220px;
        padding: 13px 12px 15px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 16%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 68%, #fff), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.38);
        overflow: hidden;
      }
      .lookbook-card::before {
        content: "";
        position: absolute;
        left: 0;
        right: 0;
        top: 0;
        height: 7px;
        background:
          radial-gradient(circle at 8px 0, rgba(255,255,255,.88) 0 6px, transparent 6px) 0 0 / 16px 7px repeat-x,
          linear-gradient(90deg, var(--brand-accent, var(--rose)), var(--gold), var(--teal));
      }
      .lookbook-card::after {
        content: "";
        position: absolute;
        right: 12px;
        top: 12px;
        width: 34px;
        height: 15px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 28%, #fff);
        border-radius: 3px;
        background:
          linear-gradient(45deg, transparent 0 35%, rgba(255,255,255,.78) 35% 65%, transparent 65%),
          linear-gradient(-45deg, transparent 0 35%, rgba(255,255,255,.78) 35% 65%, transparent 65%),
          color-mix(in srgb, var(--brand-accent, var(--rose)) 22%, #fff);
        opacity: .72;
        pointer-events: none;
      }
      .lookbook-card header { display: grid; grid-template-columns: 58px minmax(0, 1fr); gap: 10px; align-items: center; padding-top: 4px; }
      .lookbook-cameo {
        display: grid;
        place-items: center;
        width: 52px;
        height: 52px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 35%, var(--line));
        border-radius: 999px;
        background:
          radial-gradient(circle at 35% 25%, rgba(255,255,255,.88), transparent 38%),
          var(--brand-paper, #fff3f6);
        color: var(--brand-accent, var(--rose));
        font: 650 15px/1 Georgia, "Times New Roman", serif;
        box-shadow: var(--pearl-shadow);
      }
      .lookbook-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .lookbook-card header p { overflow-wrap: anywhere; }
      .lookbook-fit { display: grid; grid-template-columns: 54px 1fr 42px; gap: 8px; align-items: center; color: var(--muted); font-size: 12px; }
      .lookbook-tags, .lookbook-actions { display: flex; flex-wrap: wrap; gap: 6px; }
      .lookbook-tags span {
        display: inline-flex;
        align-items: center;
        min-height: 23px;
        padding: 0 7px;
        border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 28%, var(--line));
        border-radius: 999px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 12px;
      }
      .lookbook-actions button { min-height: 30px; padding-inline: 10px; }
      .scorecard-board { margin: 0 20px 14px; }
      .scorecard-grid { display: grid; grid-template-columns: minmax(220px, .62fr) minmax(360px, 1.38fr); gap: 12px; padding: 12px; }
      .scorecard-brief, .scorecard-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .scorecard-brief {
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(15,103,96,.12), transparent 36%),
          linear-gradient(135deg, rgba(248,251,250,.92), rgba(255,247,232,.78));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
      }
      .scorecard-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .scorecard-brief p, .scorecard-card p { margin: 0; color: var(--muted); }
      .scorecard-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
      .scorecard-stat {
        display: grid;
        gap: 3px;
        min-height: 52px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
      }
      .scorecard-stat strong { color: var(--wine); font: 650 20px/1 Georgia, "Times New Roman", serif; }
      .scorecard-stat span { color: var(--muted); font-size: 11px; }
      .scorecard-list { display: grid; gap: 9px; }
      .scorecard-card {
        position: relative;
        display: grid;
        gap: 8px;
        padding: 12px 12px 14px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 12%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 62%, #fff), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.38);
        overflow: hidden;
      }
      .scorecard-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .scorecard-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .scorecard-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .scorecard-score { display: grid; grid-template-columns: 60px 1fr 60px; gap: 8px; align-items: center; }
      .scorecard-score strong { font: 650 26px/1 Georgia, "Times New Roman", serif; }
      .scorecard-parts { display: grid; gap: 5px; }
      .scorecard-parts .profile-row { grid-template-columns: 74px 1fr 42px; }
      .scorecard-actions { display: flex; flex-wrap: wrap; gap: 7px; }
      .scorecard-actions button { min-height: 30px; padding-inline: 10px; }
      .focus-list { display: grid; gap: 8px; }
      .focus-card { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: linear-gradient(135deg, #fff7f7, #f8fbfa); box-shadow: inset 3px 0 0 rgba(180,87,111,.22); }
      .focus-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .focus-card strong { color: var(--wine); }
      .alert-board { margin: 0 20px 14px; }
      .alert-grid { display: grid; grid-template-columns: minmax(220px, .72fr) minmax(300px, 1.28fr); gap: 12px; padding: 12px; }
      .alert-brief, .alert-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .alert-brief { display: grid; gap: 9px; align-content: start; padding: 12px; background: linear-gradient(135deg, rgba(255,247,232,.78), rgba(248,251,250,.9)); box-shadow: inset 0 0 0 4px rgba(255,255,255,.48); }
      .alert-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .alert-list { display: grid; gap: 8px; }
      .alert-card { display: grid; gap: 8px; padding: 12px; }
      .alert-card header { display: flex; align-items: start; justify-content: space-between; gap: 10px; }
      .alert-card strong { color: var(--wine); }
      .momentum-board { margin: 0 20px 14px; }
      .momentum-grid { display: grid; grid-template-columns: minmax(220px, .68fr) minmax(300px, 1.32fr); gap: 12px; padding: 12px; }
      .momentum-brief, .momentum-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .momentum-brief {
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(15,103,96,.12), transparent 34%),
          linear-gradient(135deg, rgba(248,251,250,.9), rgba(255,247,232,.76));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
      }
      .momentum-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .momentum-brief p, .momentum-card p { margin: 0; color: var(--muted); }
      .momentum-list { display: grid; gap: 8px; }
      .momentum-card { display: grid; gap: 8px; padding: 12px; }
      .momentum-card header { display: flex; align-items: start; justify-content: space-between; gap: 10px; }
      .momentum-card strong { color: var(--wine); }
      .momentum-delta { font: 650 24px/1 Georgia, "Times New Roman", serif; color: var(--wine); white-space: nowrap; }
      .weight-snapshot-board { margin: 0 20px 14px; }
      .weight-snapshot { display: grid; grid-template-columns: minmax(190px, .62fr) minmax(300px, 1.15fr) minmax(260px, 1fr); gap: 12px; padding: 12px; }
      .weight-hero, .weight-radar-map, .weight-metric, .weight-lane, .weight-gap-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .weight-hero { display: grid; gap: 9px; align-content: start; padding: 12px; background: linear-gradient(135deg, rgba(255,247,232,.78), rgba(248,251,250,.9)); box-shadow: inset 0 0 0 4px rgba(255,255,255,.48); }
      .weight-hero strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .weight-hero p, .weight-metric span, .weight-lane span, .weight-gap-card p { margin: 0; color: var(--muted); }
      .weight-command-title { color: var(--wine); font: 650 13px/1.2 Georgia, "Times New Roman", serif; text-transform: uppercase; }
      .weight-radar-map {
        position: relative;
        min-height: 260px;
        overflow: hidden;
        background:
          radial-gradient(circle at 50% 50%, rgba(255,255,255,.96) 0 18%, transparent 19%),
          repeating-radial-gradient(circle at 50% 50%, rgba(180,87,111,.13) 0 1px, transparent 1px 34px),
          conic-gradient(from -90deg, rgba(180,87,111,.18), rgba(169,120,44,.15), rgba(15,103,96,.13), rgba(97,27,49,.16), rgba(180,87,111,.18)),
          linear-gradient(135deg, rgba(255,243,246,.84), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.5);
      }
      .weight-radar-center {
        position: absolute;
        left: 50%;
        top: 50%;
        width: 112px;
        min-height: 82px;
        transform: translate(-50%, -50%);
        display: grid;
        place-items: center;
        gap: 3px;
        padding: 9px;
        border: 1px solid rgba(97,27,49,.16);
        border-radius: 8px;
        background: rgba(255,253,251,.9);
        text-align: center;
      }
      .weight-radar-center strong { color: var(--wine); font: 650 24px/1 Georgia, "Times New Roman", serif; }
      .weight-radar-center span { color: var(--muted); font-size: 11px; }
      .weight-radar-node {
        position: absolute;
        left: var(--x);
        top: var(--y);
        width: 66px;
        min-height: 54px;
        transform: translate(-50%, -50%);
        display: grid;
        gap: 2px;
        align-content: center;
        padding: 7px 6px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 34%, var(--line));
        border-radius: 8px;
        background:
          linear-gradient(180deg, rgba(255,255,255,.92), color-mix(in srgb, var(--brand-paper, #fff3f6) 72%, #fff));
        box-shadow: 0 8px 18px rgba(75,38,47,.09);
        text-align: center;
      }
      .weight-radar-node b { color: var(--wine); font: 650 15px/1 Georgia, "Times New Roman", serif; overflow-wrap: anywhere; }
      .weight-radar-node em { color: var(--brand-accent, var(--rose)); font-style: normal; font-weight: 700; }
      .weight-radar-node span { color: var(--muted); font-size: 10px; }
      .weight-command-panel { display: grid; gap: 8px; align-content: start; }
      .weight-metrics { display: grid; grid-template-columns: repeat(2, minmax(110px, 1fr)); gap: 8px; }
      .weight-metric { display: grid; gap: 5px; min-height: 72px; padding: 10px; }
      .weight-metric strong { color: var(--wine); font: 650 24px/1 Georgia, "Times New Roman", serif; }
      .weight-lanes { display: grid; gap: 7px; }
      .weight-lane { display: grid; grid-template-columns: 74px 1fr 54px; gap: 8px; align-items: center; min-height: 40px; padding: 8px 10px; }
      .weight-lane strong { color: var(--wine); }
      .weight-gaps { display: grid; gap: 8px; }
      .weight-gap-card { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: center; padding: 10px; }
      .weight-gap-card strong { color: var(--wine); }
      .weight-gap-card button { min-height: 30px; padding-inline: 10px; }
      .scenario-board { margin: 0 20px 14px; }
      .scenario-grid { display: grid; grid-template-columns: repeat(3, minmax(220px, 1fr)); gap: 12px; padding: 12px; }
      .scenario-card {
        position: relative;
        display: grid;
        gap: 9px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px 12px 15px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 13%, transparent), transparent 36%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 64%, #fff), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.38);
        overflow: hidden;
      }
      .scenario-card::before {
        content: "";
        position: absolute;
        inset: 0 0 auto;
        height: 6px;
        background:
          radial-gradient(circle at 8px 0, rgba(255,255,255,.86) 0 5px, transparent 5px) 0 0 / 16px 6px repeat-x,
          linear-gradient(90deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .scenario-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; padding-top: 2px; }
      .scenario-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .scenario-score { display: grid; grid-template-columns: 62px 1fr; gap: 9px; align-items: center; }
      .scenario-score strong { font: 650 30px/1 Georgia, "Times New Roman", serif; }
      .scenario-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
      .scenario-stat {
        display: grid;
        gap: 3px;
        min-height: 48px;
        padding: 7px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
      }
      .scenario-stat strong { color: var(--wine); font: 650 18px/1 Georgia, "Times New Roman", serif; }
      .scenario-stat span, .scenario-card p { margin: 0; color: var(--muted); font-size: 12px; }
      .scenario-moves { display: grid; gap: 6px; }
      .scenario-move {
        display: grid;
        grid-template-columns: minmax(54px, .58fr) 44px 44px 48px;
        gap: 7px;
        align-items: center;
        min-height: 31px;
        padding: 6px 7px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 12px;
      }
      .scenario-move strong { overflow-wrap: anywhere; }
      .scenario-delta { color: var(--gold); font-weight: 700; text-align: right; }
      .scenario-card button { justify-self: start; min-height: 30px; }
      .rubric-board { margin: 0 20px 14px; }
      .rubric-grid { display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); gap: 10px; padding: 12px; }
      .rubric-card {
        position: relative;
        display: grid;
        gap: 9px;
        min-height: 218px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px 12px 16px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 13%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 66%, #fff), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.38);
        overflow: hidden;
      }
      .rubric-card::before {
        content: "";
        position: absolute;
        left: 10px;
        right: 10px;
        top: 7px;
        height: 5px;
        background:
          radial-gradient(circle at 8px 0, rgba(255,255,255,.86) 0 5px, transparent 5px) 0 0 / 16px 6px repeat-x,
          linear-gradient(90deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .rubric-card::after {
        content: "";
        position: absolute;
        left: 12px;
        right: 12px;
        bottom: 8px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.34) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .rubric-card header { display: flex; align-items: start; justify-content: space-between; gap: 10px; padding-top: 4px; }
      .rubric-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .rubric-score { display: grid; grid-template-columns: 58px 1fr; gap: 9px; align-items: center; }
      .rubric-score strong { font: 650 28px/1 Georgia, "Times New Roman", serif; }
      .rubric-card p { margin: 0; color: var(--muted); }
      .rubric-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
      .rubric-stats span {
        display: grid;
        gap: 3px;
        min-height: 50px;
        padding: 7px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 11px;
      }
      .rubric-stats strong { color: var(--wine); font: 650 17px/1 Georgia, "Times New Roman", serif; }
      .rubric-brands, .rubric-actions { display: flex; flex-wrap: wrap; gap: 6px; }
      .rubric-brands span {
        display: inline-flex;
        align-items: center;
        min-height: 24px;
        padding: 0 7px;
        border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 24%, var(--line));
        border-radius: 999px;
        background: rgba(255,253,251,.74);
        color: var(--muted);
        font-size: 12px;
      }
      .rubric-actions button { min-height: 29px; padding-inline: 9px; }
      .playbook-board { margin: 0 20px 14px; }
      .playbook-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(275px, 1fr)); gap: 10px; padding: 12px; }
      .playbook-card {
        position: relative;
        display: grid;
        gap: 9px;
        min-height: 246px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px 12px 16px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 13%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 66%, #fff), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.38);
        overflow: hidden;
      }
      .playbook-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .playbook-card::after {
        content: "";
        position: absolute;
        left: 12px;
        right: 12px;
        bottom: 8px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.34) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .playbook-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .playbook-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .playbook-card p { margin: 0; color: var(--muted); }
      .playbook-score { display: grid; grid-template-columns: 62px 1fr; gap: 9px; align-items: center; }
      .playbook-score strong { font: 650 30px/1 Georgia, "Times New Roman", serif; }
      .playbook-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
      .playbook-stats span {
        display: grid;
        gap: 3px;
        min-height: 52px;
        padding: 7px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 11px;
      }
      .playbook-stats strong { color: var(--wine); font: 650 17px/1 Georgia, "Times New Roman", serif; overflow-wrap: anywhere; }
      .playbook-reasons, .playbook-actions { display: flex; flex-wrap: wrap; gap: 6px; }
      .playbook-reasons span {
        display: inline-flex;
        align-items: center;
        min-height: 24px;
        padding: 0 7px;
        border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 24%, var(--line));
        border-radius: 999px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 12px;
      }
      .playbook-actions a, .playbook-actions button {
        min-height: 28px;
        display: inline-flex;
        align-items: center;
        padding: 0 8px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 22%, var(--line));
        border-radius: 999px;
        background: #fffdfb;
        color: color-mix(in srgb, var(--brand-accent, var(--rose)) 72%, var(--wine));
        box-shadow: none;
        font: inherit;
        font-size: 12px;
        text-decoration: none;
      }
      .portfolio-board { margin: 0 20px 14px; }
      .portfolio-grid { display: grid; grid-template-columns: minmax(230px, .62fr) minmax(340px, 1.38fr); gap: 12px; padding: 12px; }
      .portfolio-brief, .portfolio-card {
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 18%, var(--line));
        border-radius: 8px;
        background: #fffaf8;
      }
      .portfolio-brief {
        position: relative;
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(15,103,96,.12), transparent 36%),
          linear-gradient(135deg, rgba(248,251,250,.92), rgba(255,247,232,.78));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
        overflow: hidden;
      }
      .portfolio-brief::before {
        content: "";
        position: absolute;
        inset: 8px;
        border: 1px double rgba(169,120,44,.24);
        border-radius: 6px;
        pointer-events: none;
      }
      .portfolio-brief::after, .portfolio-card::after {
        content: "";
        position: absolute;
        left: 12px;
        right: 12px;
        bottom: 8px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.34) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .portfolio-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .portfolio-brief p, .portfolio-card p { margin: 0; color: var(--muted); }
      .portfolio-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
      .portfolio-stats span {
        display: grid;
        gap: 3px;
        min-height: 50px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 11px;
      }
      .portfolio-stats strong { color: var(--wine); font: 650 18px/1 Georgia, "Times New Roman", serif; }
      .portfolio-list { display: grid; gap: 8px; }
      .portfolio-card {
        position: relative;
        display: grid;
        gap: 8px;
        padding: 12px 12px 14px;
        background:
          radial-gradient(circle at 50% 0, rgba(255,255,255,.9) 0 5px, transparent 5px) 0 0 / 18px 10px repeat-x,
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 13%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 66%, #fff), rgba(248,251,250,.92));
        box-shadow: var(--paper-shadow);
        overflow: hidden;
      }
      .portfolio-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .portfolio-card header { display: flex; align-items: start; justify-content: space-between; gap: 10px; }
      .portfolio-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .portfolio-meter { display: grid; grid-template-columns: 58px 1fr; gap: 8px; align-items: center; }
      .portfolio-meter strong { font: 650 26px/1 Georgia, "Times New Roman", serif; }
      .portfolio-actions { display: flex; flex-wrap: wrap; gap: 6px; }
      .portfolio-actions button {
        min-height: 29px;
        padding: 0 9px;
        border-radius: 999px;
      }
      .release-board { margin: 0 20px 14px; }
      .release-grid { display: grid; grid-template-columns: minmax(230px, .62fr) minmax(340px, 1.38fr); gap: 12px; padding: 12px; }
      .release-brief, .release-card {
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 18%, var(--line));
        border-radius: 8px;
        background: #fffaf8;
      }
      .release-brief {
        position: relative;
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(15,103,96,.14), transparent 36%),
          linear-gradient(135deg, rgba(241,251,248,.92), rgba(255,247,232,.82));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
        overflow: hidden;
      }
      .release-brief::after, .release-card::after {
        content: "";
        position: absolute;
        left: 12px;
        right: 12px;
        bottom: 8px;
        height: 4px;
        background: radial-gradient(circle, rgba(15,103,96,.28) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .release-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .release-brief p, .release-card p { margin: 0; color: var(--muted); }
      .release-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
      .release-stats span {
        display: grid;
        gap: 3px;
        min-height: 50px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
        color: var(--muted);
        font-size: 11px;
      }
      .release-stats strong { color: var(--wine); font: 650 18px/1 Georgia, "Times New Roman", serif; }
      .release-list { display: grid; gap: 8px; }
      .release-card {
        position: relative;
        display: grid;
        gap: 9px;
        padding: 12px 12px 14px;
        background:
          radial-gradient(circle at 50% 0, rgba(255,255,255,.9) 0 5px, transparent 5px) 0 0 / 18px 10px repeat-x,
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 13%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 66%, #fff), rgba(248,251,250,.92));
        box-shadow: var(--paper-shadow);
        overflow: hidden;
      }
      .release-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--teal));
      }
      .release-card header { display: flex; align-items: start; justify-content: space-between; gap: 10px; }
      .release-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .release-score { display: grid; grid-template-columns: 58px 1fr; gap: 8px; align-items: center; }
      .release-score strong { font: 650 26px/1 Georgia, "Times New Roman", serif; }
      .release-meta, .release-actions { display: flex; flex-wrap: wrap; gap: 6px; }
      .release-meta span, .release-actions a, .release-actions button {
        min-height: 27px;
        display: inline-flex;
        align-items: center;
        padding: 0 8px;
        border-radius: 999px;
        font-size: 12px;
      }
      .release-meta span {
        border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 24%, var(--line));
        background: rgba(255,253,251,.72);
        color: var(--muted);
      }
      .release-actions a, .release-actions button {
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 22%, var(--line));
        background: #fffdfb;
        box-shadow: none;
        color: color-mix(in srgb, var(--brand-accent, var(--rose)) 72%, var(--wine));
        font: inherit;
        text-decoration: none;
      }
      .guardrail-board { margin: 0 20px 14px; }
      .guardrail-grid { display: grid; grid-template-columns: minmax(220px, .62fr) minmax(340px, 1.38fr); gap: 12px; padding: 12px; }
      .guardrail-brief, .guardrail-card, .guardrail-lane {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .guardrail-brief {
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(169,120,44,.14), transparent 34%),
          linear-gradient(135deg, rgba(255,247,232,.78), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
      }
      .guardrail-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .guardrail-brief p, .guardrail-card p { margin: 0; color: var(--muted); }
      .guardrail-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
      .guardrail-stat {
        display: grid;
        gap: 3px;
        min-height: 52px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.72);
      }
      .guardrail-stat strong { color: var(--wine); font: 650 20px/1 Georgia, "Times New Roman", serif; }
      .guardrail-stat span { color: var(--muted); font-size: 11px; }
      .guardrail-lanes, .guardrail-list { display: grid; gap: 8px; }
      .guardrail-lane { display: grid; grid-template-columns: 58px 1fr 40px; gap: 8px; align-items: center; min-height: 42px; padding: 8px 10px; }
      .guardrail-lane strong, .guardrail-card strong { color: var(--wine); }
      .guardrail-card {
        position: relative;
        display: grid;
        gap: 8px;
        padding: 12px 12px 14px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 13%, transparent), transparent 36%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 62%, #fff), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.38);
        overflow: hidden;
      }
      .guardrail-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .guardrail-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .guardrail-card .profile-row { grid-template-columns: 74px 1fr 42px; }
      .guardrail-actions { display: flex; flex-wrap: wrap; gap: 7px; }
      .guardrail-actions button { min-height: 30px; padding-inline: 10px; }
      .strategy-board { margin: 0 20px 14px; }
      .strategy-grid { display: grid; grid-template-columns: minmax(210px, .72fr) minmax(260px, .9fr) minmax(300px, 1.38fr); gap: 12px; padding: 12px; }
      .strategy-brief, .strategy-lane, .strategy-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .strategy-brief {
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(180,87,111,.12), transparent 36%),
          linear-gradient(135deg, rgba(255,247,232,.78), rgba(248,251,250,.9));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
      }
      .strategy-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .strategy-brief p, .strategy-lane p, .strategy-card p { margin: 0; color: var(--muted); }
      .strategy-lanes, .strategy-list { display: grid; gap: 8px; }
      .strategy-lane { display: grid; grid-template-columns: 76px 1fr 54px; gap: 8px; align-items: center; min-height: 46px; padding: 9px 10px; }
      .strategy-lane strong, .strategy-card strong { color: var(--wine); }
      .strategy-card { display: grid; gap: 8px; padding: 11px; }
      .strategy-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .strategy-card .profile-row { grid-template-columns: 62px 1fr 32px; }
      .formula-board { margin: 0 20px 14px; }
      .formula-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 10px; padding: 12px; }
      .formula-summary {
        grid-column: 1 / -1;
        display: grid;
        grid-template-columns: minmax(220px, .58fr) minmax(320px, 1.42fr);
        gap: 10px;
        border: 1px solid color-mix(in srgb, var(--gold) 22%, var(--line));
        border-radius: 8px;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(169,120,44,.16), transparent 32%),
          radial-gradient(circle at 0 100%, rgba(var(--theme-rose-rgb), .1), transparent 36%),
          linear-gradient(135deg, rgba(255,248,236,.88), rgba(248,251,250,.94));
        box-shadow: var(--paper-shadow);
      }
      .formula-summary h3 { margin: 0; color: var(--wine); font: 650 18px/1.15 Georgia, "Times New Roman", serif; }
      .formula-summary p { margin: 0; color: var(--muted); }
      .formula-summary-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
      .formula-summary-stats span {
        display: grid;
        gap: 3px;
        min-height: 50px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 7px;
        background: rgba(255,253,251,.76);
        color: var(--muted);
        font-size: 11px;
      }
      .formula-summary-stats strong { color: var(--wine); font: 650 18px/1 Georgia, "Times New Roman", serif; }
      .formula-contrib-list { display: grid; gap: 7px; }
      .formula-contrib-row { display: grid; grid-template-columns: 86px 1fr 44px; gap: 8px; align-items: center; color: var(--muted); font-size: 12px; }
      .formula-card {
        position: relative;
        display: grid;
        gap: 9px;
        min-height: 210px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px 12px 16px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 13%, transparent), transparent 34%),
          linear-gradient(135deg, rgba(255,247,232,.72), rgba(248,251,250,.92)),
          #fffaf8;
        overflow: hidden;
      }
      .formula-card::after {
        content: "";
        position: absolute;
        left: 10px;
        right: 10px;
        bottom: 7px;
        height: 4px;
        background: radial-gradient(circle, color-mix(in srgb, var(--brand-accent, var(--rose)) 28%, transparent) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .formula-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .formula-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .formula-score { display: grid; grid-template-columns: minmax(62px, .42fr) minmax(80px, .58fr); gap: 10px; align-items: center; }
      .formula-score strong { font: 650 30px/1 Georgia, "Times New Roman", serif; }
      .formula-parts { display: grid; gap: 5px; }
      .formula-parts .profile-row { grid-template-columns: 82px 1fr 42px; }
      .formula-card button { justify-self: start; min-height: 30px; }
      .trajectory-board { margin: 0 20px 14px; }
      .trajectory-grid { display: grid; grid-template-columns: minmax(210px, .62fr) minmax(300px, 1.38fr); gap: 12px; padding: 12px; }
      .trajectory-brief, .trajectory-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .trajectory-brief {
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(80,130,126,.12), transparent 36%),
          linear-gradient(135deg, rgba(255,248,236,.78), rgba(252,246,249,.9));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.45);
      }
      .trajectory-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .trajectory-brief p, .trajectory-card p { margin: 0; color: var(--muted); }
      .trajectory-list { display: grid; gap: 8px; }
      .trajectory-card { display: grid; gap: 9px; padding: 11px; }
      .trajectory-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .trajectory-card strong { color: var(--wine); }
      .trajectory-path { display: grid; grid-template-columns: 56px 1fr 56px; gap: 8px; align-items: center; }
      .trajectory-node { display: grid; gap: 2px; text-align: center; }
      .trajectory-node strong { font: 650 22px/1 Georgia, "Times New Roman", serif; }
      .trajectory-node span { color: var(--muted); font-size: 11px; }
      .trajectory-line { height: 9px; overflow: hidden; border-radius: 999px; background: var(--lace); }
      .trajectory-line span { display: block; height: 100%; width: var(--score); background: linear-gradient(90deg, var(--teal), var(--rose), var(--gold)); }
      .trajectory-actions { display: flex; flex-wrap: wrap; gap: 7px; }
      .trajectory-actions button { min-height: 30px; }
      .profile-board { margin: 0 20px 14px; }
      .profile-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; padding: 12px; }
      .profile-card {
        position: relative;
        display: grid;
        gap: 9px;
        min-height: 190px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px 12px 16px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 24px 24px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 12%, transparent), transparent 36%),
          linear-gradient(135deg, rgba(255,247,232,.78), rgba(248,251,250,.9)),
          #fffaf8;
      }
      .profile-card::after {
        content: "";
        position: absolute;
        left: 10px;
        right: 10px;
        bottom: 7px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.32) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .profile-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .profile-card strong { color: var(--wine); }
      .profile-score { display: grid; grid-template-columns: 62px 1fr; gap: 10px; align-items: center; }
      .profile-score strong { font: 650 28px/1 Georgia, "Times New Roman", serif; }
      .profile-bars { display: grid; gap: 6px; }
      .profile-row { display: grid; grid-template-columns: 68px 1fr 34px; gap: 7px; align-items: center; color: var(--muted); font-size: 12px; }
      .profile-keywords { display: flex; flex-wrap: wrap; gap: 5px; }
      .profile-keywords span { min-height: 23px; display: inline-flex; align-items: center; padding: 0 7px; border: 1px solid rgba(97,27,49,.12); border-radius: 999px; background: rgba(255,253,251,.74); color: var(--muted); font-size: 12px; }
      .identity-board { margin: 0 20px 14px; }
      .identity-grid { display: grid; grid-template-columns: minmax(220px, .72fr) minmax(320px, 1.28fr); gap: 12px; padding: 12px; }
      .identity-brief, .identity-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .identity-brief {
        position: relative;
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(180,87,111,.12), transparent 36%),
          linear-gradient(135deg, rgba(255,247,232,.78), rgba(248,251,250,.9));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
        overflow: hidden;
      }
      .identity-brief::after {
        content: "";
        position: absolute;
        left: 12px;
        right: 12px;
        bottom: 8px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.36) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .identity-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .identity-brief p, .identity-card p { margin: 0; color: var(--muted); }
      .identity-counts { display: flex; flex-wrap: wrap; gap: 6px; }
      .identity-counts span { display: inline-flex; align-items: center; min-height: 26px; padding: 0 8px; border: 1px solid var(--line); border-radius: 999px; background: rgba(255,253,251,.74); color: var(--muted); font-size: 12px; }
      .identity-counts strong { margin-right: 5px; color: var(--wine); font: 650 15px/1 Georgia, "Times New Roman", serif; }
      .identity-stack { display: grid; gap: 8px; }
      .identity-card {
        position: relative;
        display: grid;
        grid-template-columns: 58px minmax(0, 1fr) minmax(104px, auto);
        gap: 10px;
        align-items: center;
        padding: 12px 12px 14px;
        overflow: hidden;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.88) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 14%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 72%, #fff), rgba(248,251,250,.92));
      }
      .identity-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .identity-card::after {
        content: "";
        position: absolute;
        right: 12px;
        top: 8px;
        width: 30px;
        height: 13px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 28%, #fff);
        border-radius: 3px;
        background:
          linear-gradient(45deg, transparent 0 35%, rgba(255,255,255,.78) 35% 65%, transparent 65%),
          linear-gradient(-45deg, transparent 0 35%, rgba(255,255,255,.78) 35% 65%, transparent 65%),
          color-mix(in srgb, var(--brand-accent, var(--rose)) 22%, #fff);
        opacity: .76;
        pointer-events: none;
      }
      .identity-swatch {
        position: relative;
        display: grid;
        place-items: center;
        width: 48px;
        height: 48px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 35%, var(--line));
        border-radius: 999px;
        background:
          radial-gradient(circle at 35% 25%, rgba(255,255,255,.86), transparent 38%),
          var(--brand-paper, #fff3f6);
        color: var(--brand-accent, var(--rose));
        font: 650 15px/1 Georgia, "Times New Roman", serif;
        box-shadow: var(--pearl-shadow);
      }
      .identity-swatch::after {
        content: "";
        position: absolute;
        inset: 5px;
        border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 34%, transparent);
        border-radius: 999px;
        pointer-events: none;
      }
      .identity-main { display: grid; gap: 5px; min-width: 0; }
      .identity-main strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .identity-main p { overflow-wrap: anywhere; }
      .identity-tags { display: flex; flex-wrap: wrap; gap: 5px; }
      .identity-tags span { display: inline-flex; align-items: center; min-height: 23px; padding: 0 7px; border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 24%, var(--line)); border-radius: 999px; background: rgba(255,253,251,.72); color: var(--muted); font-size: 12px; }
      .identity-links { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 2px; }
      .identity-links a {
        display: inline-flex;
        align-items: center;
        min-height: 26px;
        padding: 0 8px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 22%, var(--line));
        border-radius: 6px;
        background:
          linear-gradient(90deg, color-mix(in srgb, var(--brand-accent, var(--rose)) 10%, #fff), #fffdfb);
        color: color-mix(in srgb, var(--brand-accent, var(--rose)) 72%, var(--wine));
        box-shadow: inset 0 1px 0 rgba(255,255,255,.72);
        font-size: 12px;
        text-decoration: none;
      }
      .identity-links a:hover { background: #fff; text-decoration: none; }
      .identity-score { display: grid; gap: 4px; min-width: 92px; text-align: right; }
      .identity-score strong { color: var(--wine); font: 650 22px/1 Georgia, "Times New Roman", serif; }
      .core-watch-board { margin: 0 20px 14px; }
      .core-watch-grid { display: grid; grid-template-columns: minmax(220px, .68fr) minmax(320px, 1.32fr); gap: 12px; padding: 12px; }
      .core-watch-brief, .core-watch-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .core-watch-brief {
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(180,87,111,.13), transparent 36%),
          linear-gradient(135deg, rgba(255,243,246,.82), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
      }
      .core-watch-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .core-watch-brief p, .core-watch-card p { margin: 0; color: var(--muted); }
      .core-watch-list { display: grid; gap: 8px; }
      .core-watch-card {
        position: relative;
        display: grid;
        gap: 9px;
        padding: 12px 12px 14px;
        overflow: hidden;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 24px 24px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 14%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 72%, #fff), rgba(248,251,250,.92));
      }
      .core-watch-card::before {
        content: "";
        position: absolute;
        left: 0;
        top: 10px;
        bottom: 10px;
        width: 5px;
        border-radius: 0 999px 999px 0;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .core-watch-card header { display: flex; align-items: start; justify-content: space-between; gap: 10px; }
      .core-watch-card strong { color: var(--wine); }
      .core-watch-side { display: grid; gap: 5px; justify-items: end; }
      .core-watch-score { font: 650 26px/1 Georgia, "Times New Roman", serif; color: var(--wine); white-space: nowrap; }
      .core-watch-reasons, .core-watch-terms, .core-watch-links { display: flex; flex-wrap: wrap; gap: 6px; }
      .core-watch-reasons span {
        display: inline-flex;
        align-items: center;
        min-height: 24px;
        padding: 0 7px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 24%, var(--line));
        border-radius: 999px;
        background: rgba(255,253,251,.78);
        color: var(--muted);
        font-size: 12px;
      }
      .core-watch-reasons span.rose { color: var(--rose-dark); border-color: rgba(180,87,111,.28); background: rgba(255,243,246,.82); }
      .core-watch-reasons span.gold { color: #7b581e; border-color: rgba(169,120,44,.32); background: rgba(255,248,236,.86); }
      .core-watch-reasons span.warn { color: #8d3a32; border-color: rgba(141,58,50,.28); background: rgba(255,245,242,.86); }
      .core-watch-price {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 6px;
      }
      .core-watch-price span {
        display: grid;
        gap: 3px;
        min-height: 54px;
        padding: 8px;
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 18%, var(--line));
        border-radius: 8px;
        background: rgba(255,253,251,.76);
        color: var(--muted);
        font-size: 11px;
      }
      .core-watch-price strong { color: var(--wine); font: 650 15px/1 Georgia, "Times New Roman", serif; overflow-wrap: anywhere; }
      .core-watch-price.missing { grid-template-columns: 1fr; }
      .core-watch-price.missing span { min-height: 42px; align-content: center; border-style: dashed; }
      .core-watch-terms button, .core-watch-links a, .core-watch-links button {
        min-height: 26px;
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        font-size: 12px;
      }
      .core-watch-terms button { padding: 0 8px; border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 30%, var(--line)); background: rgba(255,253,251,.76); color: var(--wine); box-shadow: none; }
      .core-watch-links a { padding: 0 8px; border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 24%, var(--line)); background: #fffdfb; color: color-mix(in srgb, var(--brand-accent, var(--rose)) 74%, var(--wine)); text-decoration: none; }
      .core-watch-links button { padding: 0 9px; }
      .keyword-board { margin: 0 20px 14px; }
      .keyword-radar { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 10px; padding: 12px; }
      .keyword-card {
        position: relative;
        display: grid;
        gap: 8px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 13%, transparent), transparent 34%),
          linear-gradient(135deg, rgba(248,251,250,.92), rgba(255,247,232,.72)),
          #fffaf8;
      }
      .keyword-card::after, .pattern-card::after, .opportunity-card::after {
        content: "";
        position: absolute;
        left: 10px;
        right: 10px;
        bottom: 7px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.32) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .keyword-card header { display: flex; align-items: start; justify-content: space-between; gap: 10px; }
      .keyword-card strong { color: var(--wine); }
      .keyword-chips { display: flex; flex-wrap: wrap; gap: 6px; }
      .keyword-chips button { min-height: 28px; padding: 0 8px; border-color: rgba(15,103,96,.18); background: #f8fbfa; box-shadow: none; color: var(--teal); }
      .seed-board { margin: 0 20px 14px; }
      .seed-summary {
        display: grid;
        grid-template-columns: repeat(4, minmax(120px, 1fr));
        gap: 8px;
        padding: 12px 12px 0;
      }
      .seed-summary-card {
        display: grid;
        gap: 4px;
        min-height: 70px;
        padding: 10px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background:
          radial-gradient(circle at 100% 0, rgba(180,87,111,.1), transparent 36%),
          linear-gradient(135deg, rgba(255,247,232,.72), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.38);
      }
      .seed-summary-card strong { color: var(--wine); font: 650 24px/1 Georgia, "Times New Roman", serif; }
      .seed-summary-card span { color: var(--muted); font-size: 12px; }
      .seed-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; padding: 12px; }
      .seed-card {
        position: relative;
        display: grid;
        gap: 9px;
        min-height: 188px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px 12px 16px;
        background:
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 13%, transparent), transparent 36%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 68%, #fff), rgba(248,251,250,.92)),
          #fffaf8;
        overflow: hidden;
      }
      .seed-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .seed-card::after {
        content: "";
        position: absolute;
        left: 10px;
        right: 10px;
        bottom: 7px;
        height: 4px;
        background: radial-gradient(circle, color-mix(in srgb, var(--brand-accent, var(--rose)) 30%, transparent) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .seed-card header { display: flex; align-items: start; justify-content: space-between; gap: 10px; }
      .seed-card header > div:last-child { display: grid; gap: 5px; justify-items: end; }
      .seed-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .seed-score { font: 650 30px/1 Georgia, "Times New Roman", serif; color: var(--wine); white-space: nowrap; }
      .seed-meta, .seed-keywords, .seed-links { display: flex; flex-wrap: wrap; gap: 6px; }
      .seed-meta span, .seed-keywords button, .seed-links a {
        display: inline-flex;
        align-items: center;
        min-height: 26px;
        padding: 0 8px;
        border-radius: 999px;
        font-size: 12px;
      }
      .seed-meta span {
        border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 24%, var(--line));
        background: rgba(255,253,251,.72);
        color: var(--muted);
      }
      .seed-keywords button {
        border: 1px solid color-mix(in srgb, var(--brand-accent, var(--rose)) 24%, var(--line));
        background: rgba(255,253,251,.82);
        box-shadow: none;
        color: color-mix(in srgb, var(--brand-accent, var(--rose)) 74%, var(--wine));
      }
      .seed-links a {
        border: 1px solid rgba(15,103,96,.18);
        background: #f8fbfa;
        color: var(--teal);
        text-decoration: none;
      }
      .seed-links a:hover { background: #fff; text-decoration: none; }
      .pattern-board { margin: 0 20px 14px; }
      .pattern-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; padding: 12px; }
      .pattern-card {
        position: relative;
        display: grid;
        gap: 8px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px 12px 16px;
        background:
          linear-gradient(135deg, rgba(180,87,111,.1), rgba(255,247,232,.82)),
          #fffaf8;
      }
      .pattern-card header { display: flex; align-items: start; justify-content: space-between; gap: 10px; }
      .pattern-card strong { color: var(--wine); }
      .pattern-card button { justify-self: start; min-height: 30px; padding-inline: 10px; }
      .evidence-list { display: grid; gap: 6px; }
      .evidence-list article { border-top: 1px dashed rgba(97,27,49,.16); padding-top: 7px; }
      .evidence-list a { color: var(--teal); }
      .action-board { margin: 0 20px 14px; }
      .action-grid { display: grid; grid-template-columns: minmax(240px, .7fr) minmax(280px, 1.3fr); gap: 12px; padding: 12px; }
      .action-brief, .action-list article {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .action-brief { display: grid; gap: 9px; align-content: start; padding: 12px; background: linear-gradient(135deg, rgba(255,247,232,.78), rgba(248,251,250,.9)); box-shadow: inset 0 0 0 4px rgba(255,255,255,.48); }
      .action-brief strong { color: var(--wine); font: 650 32px/1 Georgia, "Times New Roman", serif; }
      .action-list { display: grid; gap: 8px; }
      .action-list article { display: grid; gap: 8px; padding: 12px; }
      .action-list header { display: flex; align-items: start; justify-content: space-between; gap: 10px; }
      .action-list strong { color: var(--wine); }
      .search-links { display: flex; flex-wrap: wrap; gap: 6px; }
      .search-links a, .search-links button { min-height: 28px; padding: 0 8px; border: 1px solid rgba(15,103,96,.18); border-radius: 999px; background: #f8fbfa; color: var(--teal); box-shadow: none; font: inherit; text-decoration: none; display: inline-flex; align-items: center; }
      .price-board { margin: 0 20px 14px; }
      .price-grid { display: grid; grid-template-columns: minmax(230px, .62fr) minmax(320px, 1.38fr); gap: 12px; padding: 12px; }
      .price-brief, .price-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .price-brief {
        display: grid;
        gap: 9px;
        align-content: start;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(15,103,96,.12), transparent 36%),
          linear-gradient(135deg, rgba(248,251,250,.92), rgba(255,247,232,.78));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
      }
      .price-brief strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .price-brief p, .price-card p { margin: 0; color: var(--muted); }
      .price-list { display: grid; gap: 8px; }
      .price-card {
        position: relative;
        display: grid;
        gap: 8px;
        padding: 12px 12px 14px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.9) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 12%, transparent), transparent 38%),
          linear-gradient(135deg, color-mix(in srgb, var(--brand-paper, #fff3f6) 62%, #fff), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 3px rgba(255,255,255,.38);
        overflow: hidden;
      }
      .price-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, var(--brand-accent, var(--rose)), var(--gold));
      }
      .price-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .price-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .price-ladder { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
      .price-ladder span {
        display: grid;
        gap: 3px;
        min-height: 54px;
        padding: 8px;
        border: 1px solid rgba(97,27,49,.1);
        border-radius: 8px;
        background: rgba(255,253,251,.74);
        color: var(--muted);
        font-size: 11px;
      }
      .price-ladder strong { color: var(--wine); font: 650 15px/1 Georgia, "Times New Roman", serif; overflow-wrap: anywhere; }
      .price-actions { display: flex; flex-wrap: wrap; gap: 7px; }
      .price-actions button { min-height: 30px; padding-inline: 10px; }
      .quality-board { margin: 0 20px 14px; }
      .quality-grid { display: grid; grid-template-columns: minmax(220px, .65fr) minmax(280px, 1.35fr); gap: 12px; padding: 12px; }
      .quality-hero, .quality-check {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .quality-hero { display: grid; gap: 9px; align-content: start; padding: 12px; background: linear-gradient(135deg, rgba(248,251,250,.92), rgba(255,247,232,.72)); box-shadow: inset 0 0 0 4px rgba(255,255,255,.48); }
      .quality-hero strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .quality-checks { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
      .quality-check { display: grid; gap: 5px; padding: 10px; }
      .quality-check strong { color: var(--wine); font: 650 22px/1 Georgia, "Times New Roman", serif; }
      .signal-strip { display: grid; gap: 8px; align-content: start; }
      .signal-bar { height: 11px; overflow: hidden; border-radius: 999px; background: var(--lace); box-shadow: inset 0 0 0 1px rgba(97,27,49,.06); }
      .signal-bar span { display: block; height: 100%; width: var(--score); background: linear-gradient(90deg, var(--teal), var(--rose), var(--gold)); }
      .workspace { display: grid; grid-template-columns: 340px 1fr; gap: 14px; padding: 0 20px 20px; }
      .panel { position: relative; min-width: 0; overflow: hidden; }
      .panel::before, .atelier::before {
        content: "";
        position: absolute;
        left: 0;
        right: 0;
        top: 0;
        height: 10px;
        background:
          radial-gradient(circle at 8px 0, rgba(255,255,255,.82) 0 6px, transparent 6px) 0 0 / 16px 8px repeat-x,
          radial-gradient(circle at 8px 2px, rgba(180,87,111,.18) 0 7px, transparent 7px) 0 2px / 16px 8px repeat-x,
          linear-gradient(90deg, rgba(180,87,111,.18), rgba(169,120,44,.16), rgba(15,103,96,.13));
        pointer-events: none;
      }
      .panel::after, .atelier::after {
        content: "";
        position: absolute;
        left: 0;
        right: 0;
        bottom: 0;
        height: 5px;
        background: radial-gradient(circle at 8px 5px, rgba(180,87,111,.28) 0 4px, transparent 4px) 0 0 / 16px 5px repeat-x;
        opacity: .72;
        pointer-events: none;
      }
      .panel h2 { padding: 14px 15px; border-bottom: 1px solid var(--line); background: linear-gradient(90deg, #fff7f7, #f8fbfa); }
      .toolbar, .panel > h2 {
        background:
          radial-gradient(circle at 12px 100%, rgba(180,87,111,.12) 0 6px, transparent 6px) 0 100% / 24px 10px repeat-x,
          linear-gradient(90deg, #fff7f7, #f8fbfa);
      }
      .source-list, .event-list, .item-list, .status-list { display: grid; gap: 9px; padding: 12px; }
      .source-card, .row, .status-card { border: 1px solid var(--line); border-radius: 8px; padding: 11px; background: #fffaf8; box-shadow: inset 0 1px 0 rgba(255,255,255,.74); }
      .source-card header, .row header { display: flex; justify-content: space-between; align-items: start; gap: 10px; margin-bottom: 6px; }
      .source-card strong, .row strong { overflow-wrap: anywhere; }
      .pill { display: inline-flex; align-items: center; min-height: 24px; padding: 0 8px; border-radius: 999px; background: #edf7f5; color: var(--teal); font-size: 12px; white-space: nowrap; }
      .pill.off { background: #eef1f3; color: var(--muted); }
      .pill.warn { background: #fff1ed; color: var(--warn); }
      .pill.rose { background: #fff0f3; color: var(--wine); }
      .pill.gold { background: #fff7e8; color: var(--gold); }
      .row p, .source-card p { margin: 0; color: var(--muted); overflow-wrap: anywhere; }
      .main-stack { display: grid; gap: 14px; }
      .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--line); }
      .toolbar h2 { border: 0; padding: 0; }
      .event-list { grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); }
      .event-card { display: grid; gap: 8px; border-left: 4px solid var(--rose); }
      .event-meta { display: flex; flex-wrap: wrap; gap: 6px; }
      .item-list { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
      .status-card { display: grid; gap: 8px; }
      .status-card header { display: flex; justify-content: space-between; gap: 10px; }
      .status-count { font: 650 22px/1 Georgia, "Times New Roman", serif; color: var(--wine); }
      .market-board { margin: 0 20px 14px; }
      .market-grid { display: grid; grid-template-columns: .8fr 1.2fr; gap: 12px; padding: 12px; }
      .market-list { display: grid; gap: 9px; }
      .matrix-board { margin: 0 20px 14px; }
      .matrix-toolbar { display: grid; grid-template-columns: minmax(220px, 1fr) auto; gap: 10px; align-items: center; }
      .matrix-tools { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; align-items: center; }
      .matrix-sort { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); font-size: 12px; }
      .matrix-sort select { min-height: 32px; border: 1px solid var(--line); border-radius: 6px; padding: 0 8px; background: #fffdfb; color: var(--text); font: inherit; }
      .radar-matrix { display: grid; gap: 6px; padding: 12px; }
      .matrix-row {
        display: grid;
        grid-template-columns: minmax(128px, 1.2fr) 82px 86px 74px 96px minmax(120px, .9fr);
        gap: 10px;
        align-items: center;
        min-height: 46px;
        padding: 9px 11px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .matrix-row.header {
        min-height: 34px;
        color: var(--muted);
        background: transparent;
        border: 0;
        padding-block: 0;
        font-size: 12px;
      }
      .matrix-brand strong { display: block; color: var(--wine); }
      .matrix-brand span { color: var(--muted); font-size: 12px; }
      .matrix-score { display: grid; gap: 5px; }
      .matrix-score strong { color: var(--wine); font: 650 18px/1 Georgia, "Times New Roman", serif; }
      .matrix-action { display: grid; gap: 4px; align-content: center; }
      .matrix-action span.muted { font-size: 12px; overflow-wrap: anywhere; }
      .coverage-board { margin: 0 20px 14px; }
      .coverage-grid { display: grid; grid-template-columns: minmax(220px, .8fr) minmax(260px, 1.2fr); gap: 12px; padding: 12px; }
      .coverage-meter { display: grid; gap: 9px; align-content: start; }
      .coverage-stats { display: grid; grid-template-columns: repeat(3, minmax(80px, 1fr)); gap: 8px; }
      .coverage-stat { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fffaf8; }
      .coverage-stat strong { display: block; color: var(--wine); font: 650 22px/1 Georgia, "Times New Roman", serif; }
      .coverage-list { display: grid; gap: 8px; }
      .coverage-card {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 8px;
        align-items: center;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 10px;
        background: #fffaf8;
      }
      .coverage-card strong { color: var(--wine); }
      .coverage-card button { min-height: 30px; padding-inline: 10px; }
      .sample-plan-board { margin: 0 20px 14px; }
      .sample-plan-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 10px; padding: 12px; }
      .sample-plan-card {
        position: relative;
        display: grid;
        gap: 8px;
        min-height: 188px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px 12px 16px;
        background:
          radial-gradient(circle at 18px 18px, rgba(255,255,255,.88) 0 2px, transparent 2px) 0 0 / 22px 22px,
          radial-gradient(circle at 100% 0, color-mix(in srgb, var(--brand-accent, var(--rose)) 12%, transparent), transparent 36%),
          linear-gradient(135deg, rgba(255,247,232,.76), rgba(248,251,250,.92)),
          #fffaf8;
        overflow: hidden;
      }
      .sample-plan-card::after {
        content: "";
        position: absolute;
        left: 10px;
        right: 10px;
        bottom: 7px;
        height: 4px;
        background: radial-gradient(circle, rgba(169,120,44,.34) 0 2px, transparent 2px) 0 0 / 12px 4px repeat-x;
        pointer-events: none;
      }
      .sample-plan-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .sample-plan-card strong { color: var(--wine); font-family: Georgia, "Times New Roman", serif; }
      .sample-plan-meta, .sample-plan-links, .sample-plan-keywords { display: flex; flex-wrap: wrap; gap: 6px; }
      .sample-plan-keywords span, .sample-plan-keywords button { display: inline-flex; align-items: center; min-height: 23px; padding: 0 7px; border: 1px dashed color-mix(in srgb, var(--brand-accent, var(--rose)) 24%, var(--line)); border-radius: 999px; background: rgba(255,253,251,.72); box-shadow: none; color: var(--muted); font: inherit; font-size: 12px; }
      .sample-plan-keywords button { cursor: pointer; }
      .sample-plan-keywords button:hover { color: var(--brand-accent, var(--rose)); background: #fff; }
      .sample-plan-card button { justify-self: start; min-height: 30px; }
      .sample-plan-summary {
        grid-column: 1 / -1;
        display: grid;
        grid-template-columns: minmax(180px, .72fr) minmax(260px, 1.28fr);
        gap: 12px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px;
        background:
          radial-gradient(circle at 100% 0, rgba(180,87,111,.12), transparent 36%),
          linear-gradient(135deg, rgba(255,247,232,.78), rgba(248,251,250,.92));
        box-shadow: inset 0 0 0 4px rgba(255,255,255,.48);
      }
      .sample-plan-hero { display: grid; gap: 8px; align-content: start; }
      .sample-plan-hero strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .sample-plan-stats { display: grid; grid-template-columns: repeat(4, minmax(80px, 1fr)); gap: 8px; }
      .sample-plan-stat { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: rgba(255,253,251,.74); }
      .sample-plan-stat strong { display: block; color: var(--wine); font: 650 22px/1 Georgia, "Times New Roman", serif; }
      .tuning-board { margin: 0 20px 14px; }
      .tuning-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; padding: 12px; }
      .tuning-card {
        display: grid;
        gap: 7px;
        min-height: 128px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px;
        background:
          linear-gradient(135deg, rgba(255,247,232,.72), rgba(248,251,250,.9)),
          #fffaf8;
      }
      .tuning-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .tuning-card strong { color: var(--wine); }
      .tuning-card button { justify-self: start; min-height: 32px; }
      .opportunity-board { margin: 0 20px 14px; }
      .opportunity-toolbar { display: grid; grid-template-columns: minmax(220px, 1fr) auto; gap: 10px; align-items: center; }
      .opportunity-summary { display: flex; flex-wrap: wrap; gap: 7px; }
      .summary-chip { display: inline-flex; align-items: center; gap: 6px; min-height: 28px; padding: 0 9px; border: 1px solid var(--line); border-radius: 999px; background: rgba(255,253,251,.78); color: var(--muted); font-size: 12px; }
      .summary-chip strong { color: var(--wine); font: 650 15px/1 Georgia, "Times New Roman", serif; }
      .segmented { display: inline-flex; flex-wrap: wrap; gap: 2px; padding: 2px; border: 1px solid var(--line); border-radius: 7px; background: rgba(255,253,251,.78); }
      .segmented button { min-height: 30px; padding: 0 9px; border: 0; border-radius: 5px; background: transparent; box-shadow: none; color: var(--muted); }
      .segmented button.active { background: var(--wine); color: #fff; }
      .opportunity-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; padding: 12px; }
      .opportunity-card {
        position: relative;
        display: grid;
        gap: 8px;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px 12px 16px;
        background:
          linear-gradient(135deg, rgba(180,87,111,.1), rgba(15,103,96,.08)),
          #fffaf8;
      }
      .opportunity-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .opportunity-card strong { color: var(--wine); }
      .score-breakdown { display: grid; gap: 5px; }
      .score-row { display: grid; grid-template-columns: 52px 1fr 26px; gap: 7px; align-items: center; color: var(--muted); font-size: 12px; }
      .score-track { height: 7px; overflow: hidden; border-radius: 999px; background: var(--lace); }
      .score-track span { display: block; height: 100%; width: var(--score); background: linear-gradient(90deg, var(--rose), var(--gold)); }
      .market-card {
        position: relative;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 11px 11px 11px 14px;
        background:
          linear-gradient(90deg, rgba(169,120,44,.11), transparent 30%),
          #fffaf8;
      }
      .market-card::before {
        content: "";
        position: absolute;
        left: 0;
        top: 8px;
        bottom: 8px;
        width: 4px;
        border-radius: 0 999px 999px 0;
        background: linear-gradient(180deg, var(--rose), var(--gold));
      }
      .market-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .price-corridor { display: grid; gap: 5px; margin-top: 2px; }
      .price-corridor-row { display: grid; grid-template-columns: 64px 1fr auto; gap: 8px; align-items: center; color: var(--muted); font-size: 12px; }
      .price-corridor-track { height: 7px; overflow: hidden; border-radius: 999px; background: var(--lace); }
      .price-corridor-track span { display: block; height: 100%; width: var(--score); background: linear-gradient(90deg, var(--gold), var(--rose)); }
      .market-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 0 0 8px; }
      .market-heading h2 { margin: 0; padding: 0; border: 0; background: transparent; }
      .market-heading .segmented { justify-content: flex-end; }
      .premium-tools { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
      .premium-brand-filter { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); font-size: 12px; }
      .premium-brand-filter select { min-height: 32px; border: 1px solid var(--line); border-radius: 6px; padding: 0 8px; background: #fffdfb; color: var(--text); font: inherit; }
      .premium-rate { font: 650 22px/1 Georgia, "Times New Roman", serif; color: var(--wine); white-space: nowrap; }
      .market-form {
        display: grid;
        grid-template-columns: repeat(6, minmax(110px, 1fr));
        gap: 9px;
        padding: 12px;
        border-bottom: 1px solid var(--line);
        background:
          repeating-linear-gradient(90deg, rgba(255,255,255,.38) 0 16px, transparent 16px 32px),
          #fff7f7;
      }
      .market-form label { display: grid; gap: 4px; color: var(--muted); font-size: 12px; }
      .market-form input, .market-form select { min-height: 36px; width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 0 9px; background: #fffdfb; color: var(--text); font: inherit; }
      .market-form .wide { grid-column: span 2; }
      .market-form button { align-self: end; }
      .sample-task-hint {
        display: none;
        gap: 4px;
        margin: 0 12px 12px;
        padding: 10px 12px;
        border: 1px dashed rgba(169,120,44,.32);
        border-radius: 8px;
        background: linear-gradient(90deg, rgba(255,248,236,.82), rgba(255,253,251,.94));
        color: var(--muted);
      }
      .sample-task-hint.show { display: grid; }
      .sample-task-hint strong { color: var(--wine); }
      .sample-preview {
        display: grid;
        grid-template-columns: minmax(160px, .7fr) 1fr;
        gap: 10px;
        align-items: center;
        margin: 0 12px 12px;
        padding: 10px 12px;
        border: 1px dashed rgba(97,27,49,.18);
        border-radius: 8px;
        background: linear-gradient(90deg, rgba(255,247,232,.72), rgba(248,251,250,.9));
      }
      .sample-preview strong { color: var(--wine); font: 650 22px/1 Georgia, "Times New Roman", serif; }
      .sample-preview p { margin: 0; color: var(--muted); }
      .toast { position: fixed; right: 16px; bottom: 16px; max-width: min(440px, calc(100vw - 32px)); padding: 10px 12px; border-radius: 8px; background: #16242d; color: #fff; box-shadow: var(--shadow); opacity: 0; transform: translateY(8px); transition: .16s; pointer-events: none; }
      .toast.show { opacity: 1; transform: translateY(0); }
      @media (max-width: 860px) {
        .topbar, .atelier, .workspace, .market-grid { grid-template-columns: 1fr; }
        .hero-visual { min-height: 160px; }
        .actions { justify-content: flex-start; }
        .preference-stack { justify-items: start; }
        .opportunity-toolbar, .matrix-toolbar, .coverage-grid, .north-star-grid, .north-star-list, .crown-grid, .crown-list, .draft-risk-grid, .draft-risk-card, .style-premium-grid, .style-premium-list, .daily-grid, .run-sheet-grid, .portfolio-grid, .release-grid, .rubric-grid, .playbook-grid, .lookbook-grid, .scorecard-grid, .guardrail-grid, .scenario-grid, .weight-salon, .weight-salon-list, .weight-snapshot, .strategy-grid, .formula-summary, .action-grid, .price-grid, .quality-grid, .alert-grid, .momentum-grid, .identity-grid, .core-watch-grid { grid-template-columns: 1fr; }
        .matrix-tools { justify-content: flex-start; }
        .market-heading, .premium-tools { align-items: flex-start; flex-direction: column; }
        .coverage-card, .sample-preview { grid-template-columns: 1fr; }
        .price-ladder { grid-template-columns: 1fr; }
        .weight-radar-map { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; min-height: 0; padding: 98px 10px 10px; }
        .weight-radar-center { top: 10px; transform: translateX(-50%); min-height: 70px; }
        .weight-radar-node { position: static; width: auto; min-height: 48px; transform: none; }
        .core-watch-price { grid-template-columns: 1fr; }
        .seed-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .weight-draft-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .weight-draft-warning { grid-template-columns: 1fr; }
        .sample-plan-summary, .sample-plan-stats { grid-template-columns: 1fr; }
        .style-compass { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .matrix-row { grid-template-columns: 1fr 1fr; }
        .matrix-row.header { display: none; }
        .identity-card { grid-template-columns: 48px 1fr; }
        .identity-score { grid-column: 1 / -1; text-align: left; }
        .brand-tools { align-items: flex-start; flex-direction: column; }
        .weight-draft-head, .weight-draft-row { grid-template-columns: minmax(92px, 1fr) 44px 44px 50px; }
        .metrics, .watch-grid, .event-list, .item-list, .market-form { grid-template-columns: 1fr; }
        .market-form .wide { grid-column: span 1; }
      }
    </style>
  </head>
  <body>
    <header class="topbar">
      <div>
        <p class="eyebrow" data-i18n="eyebrow">二级市场情报台</p>
        <h1>Lolita Premium Radar</h1>
        <p id="headline" data-i18n="headline">监控日牌上新、预约、再贩与二级市场溢价线索。</p>
        <p id="paths">Loading...</p>
        <div id="styleCompass" class="style-compass" aria-label="Lolita style compass"></div>
      </div>
      <aside class="hero-visual" aria-label="Lolita radar mood">
        <strong data-i18n="heroVisualTitle">蕾丝雷达 · 溢价巡航</strong>
        <div class="hero-pearls">
          <span data-i18n="heroVisualWeight">品牌权重</span>
          <span data-i18n="heroVisualPremium">溢价热度</span>
          <span data-i18n="heroVisualEvidence">样本证据</span>
        </div>
      </aside>
      <div class="actions">
        <div class="preference-stack">
          <div class="language-switch" role="group" aria-label="Language">
            <button type="button" data-language="zh">中文</button>
            <button type="button" data-language="en">EN</button>
          </div>
          <div class="theme-switch" role="group" aria-label="Lolita theme">
            <button type="button" data-theme-control="sweet"><span class="theme-swatch sweet"></span><span data-i18n="themeSweet">Sweet</span></button>
            <button type="button" data-theme-control="classic"><span class="theme-swatch classic"></span><span data-i18n="themeClassic">Classic</span></button>
            <button type="button" data-theme-control="gothic"><span class="theme-swatch gothic"></span><span data-i18n="themeGothic">Gothic</span></button>
          </div>
        </div>
        <button id="checkAllBtn" data-i18n="checkAll">检查全部</button>
        <button id="refreshBtn" class="secondary" data-i18n="refresh">刷新</button>
      </div>
    </header>
    <section class="metrics" id="metrics"></section>
    <section class="panel north-star-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="northStarRadar">北极星雷达</h2>
          <span class="muted" data-i18n="northStarHint">用品牌权重、证据覆盖、发售热度和巡检压力判断今日雷达成熟度</span>
        </div>
      </div>
      <div id="northStarRadar" class="north-star-grid"></div>
    </section>
    <section class="panel crown-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandCrownQueue">品牌皇冠队列</h2>
          <span class="muted" data-i18n="brandCrownHint">把核心品牌、贝壳等款式词、二手溢价和上新命中排成今日重点</span>
        </div>
        <button id="exportCrownCsvBtn" type="button" class="secondary" data-i18n="exportCrownCsv">导出皇冠 CSV</button>
      </div>
      <div id="brandCrownQueue" class="crown-grid"></div>
    </section>
    <section class="panel draft-risk-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="draftRiskRadar">权重草稿风险</h2>
          <span class="muted" data-i18n="draftRiskHint">把未保存的调权风险提前放到首页，保存前先复核</span>
        </div>
      </div>
      <div id="draftRiskRadar" class="draft-risk-grid"></div>
    </section>
    <section class="panel style-premium-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="stylePremiumTape">风格溢价行情</h2>
          <span class="muted" data-i18n="stylePremiumTapeHint">按 Sweet、Classic、Gothic 等风格线查看二级市场溢价和证据状态</span>
        </div>
      </div>
      <div id="stylePremiumTape" class="style-premium-grid"></div>
    </section>
    <section class="panel daily-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="dailyRadarBrief">今日雷达简报</h2>
          <span class="muted" data-i18n="dailyRadarBriefHint">把核心盯盘、权重校准和采样缺口收成今天的行动队列</span>
        </div>
        <button id="exportDailyCsvBtn" type="button" class="secondary" data-i18n="exportDailyCsv">导出简报 CSV</button>
      </div>
      <div id="dailyRadarBrief" class="daily-grid"></div>
    </section>
    <nav class="radar-nav" aria-label="Radar navigation">
      <button type="button" data-radar-jump="northStarRadar" data-i18n="navNorthStar">北极星</button>
      <button type="button" data-radar-jump="brandCrownQueue" data-i18n="navCrown">皇冠</button>
      <button type="button" data-radar-jump="stylePremiumTape" data-i18n="navStylePremium">行情</button>
      <button type="button" data-radar-jump="dailyRadarBrief" data-i18n="navDaily">简报</button>
      <button type="button" data-radar-jump="brandPortfolio" data-i18n="navPortfolio">组合</button>
      <button type="button" data-radar-jump="releaseWatchQueue" data-i18n="navReleaseWatch">发售</button>
      <button type="button" data-radar-jump="brandWeightsPanel" data-i18n="navWeights">权重</button>
      <button type="button" data-radar-jump="brandWeightRubric" data-i18n="navRubric">标尺</button>
      <button type="button" data-radar-jump="brandPlaybook" data-i18n="navPlaybook">作战卡</button>
      <button type="button" data-radar-jump="weightScenarioCompare" data-i18n="navScenarios">情景</button>
      <button type="button" data-radar-jump="resaleRunSheet" data-i18n="navRunSheet">巡检</button>
      <button type="button" data-radar-jump="brandLookbook" data-i18n="navLookbook">造型册</button>
      <button type="button" data-radar-jump="brandWeightScorecard" data-i18n="navScorecard">评分卡</button>
      <button type="button" data-radar-jump="brandWeightGuardrails" data-i18n="navGuardrails">护栏</button>
      <button type="button" data-radar-jump="coreMarketWatch" data-i18n="navCoreWatch">盯盘</button>
      <button type="button" data-radar-jump="brandIdentityMatrix" data-i18n="navIdentity">身份</button>
      <button type="button" data-radar-jump="weightTrajectory" data-i18n="navTrajectory">轨迹</button>
      <button type="button" data-radar-jump="brandWeightFormula" data-i18n="navFormula">配方</button>
      <button type="button" data-radar-jump="brandRadarMatrix" data-i18n="navMatrix">矩阵</button>
      <button type="button" data-radar-jump="marketForm" data-i18n="navPremium">溢价</button>
      <button type="button" data-radar-jump="priceDiscipline" data-i18n="navPricing">价格线</button>
      <button type="button" data-radar-jump="evidenceHealth" data-i18n="navEvidence">证据</button>
      <button type="button" data-radar-jump="samplePlan" data-i18n="navSampling">采样</button>
      <button type="button" data-radar-jump="sources" data-i18n="navSources">监控源</button>
    </nav>
    <section class="panel run-sheet-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="resaleRunSheet">二级市场巡检清单</h2>
          <span class="muted" data-i18n="resaleRunSheetHint">把今日行动、查价任务和补价格锚点收成一张执行表</span>
        </div>
        <button id="exportRunSheetCsvBtn" type="button" class="secondary" data-i18n="exportRunSheetCsv">导出巡检 CSV</button>
      </div>
      <div id="resaleRunSheet" class="run-sheet-grid"></div>
    </section>
    <section class="panel portfolio-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandPortfolio">品牌组合总览</h2>
          <span class="muted" data-i18n="brandPortfolioHint">把证据覆盖、核心缺口、溢价热度和权重偏移合成总览</span>
        </div>
        <button id="exportPortfolioCsvBtn" type="button" class="secondary" data-i18n="exportPortfolioCsv">导出组合 CSV</button>
      </div>
      <div id="brandPortfolio" class="portfolio-grid"></div>
    </section>
    <section class="panel release-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="releaseWatchQueue">新品发售关注队列</h2>
          <span class="muted" data-i18n="releaseWatchHint">把上新/预约/再贩条目接到品牌权重和二手溢价判断</span>
        </div>
        <button id="exportReleaseWatchCsvBtn" type="button" class="secondary" data-i18n="exportReleaseWatchCsv">导出发售 CSV</button>
      </div>
      <div id="releaseWatchQueue" class="release-grid"></div>
    </section>
    <section class="atelier">
      <div class="signal-strip">
        <h2 data-i18n="marketSignal">溢价信号</h2>
        <p class="muted" id="signalSummary"></p>
        <div class="signal-bar" aria-hidden="true"><span id="signalBar" style="--score: 0%"></span></div>
        <div id="statusMix" class="status-list"></div>
        <h2 data-i18n="focusQueue">重点关注队列</h2>
        <div id="focusQueue" class="focus-list"></div>
      </div>
      <div id="brandWeightsPanel" data-radar-anchor="exact">
        <div class="brand-tools">
          <h2 data-i18n="brandWeights">品牌权重</h2>
          <div class="brand-actions">
            <span id="weightDirtyStatus" class="muted" data-i18n="weightsClean">已保存</span>
            <div id="weightScenarios" class="weight-scenarios" role="group" aria-label="Weight scenarios">
              <button type="button" class="secondary" data-weight-scenario="release" data-i18n="scenarioRelease">新品优先</button>
              <button type="button" class="secondary" data-weight-scenario="premium" data-i18n="scenarioPremium">溢价优先</button>
              <button type="button" class="secondary" data-weight-scenario="evidence" data-i18n="scenarioEvidence">补证据优先</button>
            </div>
            <button id="exportWeightsCsvBtn" type="button" class="secondary" data-i18n="exportWeightsCsv">导出权重 CSV</button>
            <button id="resetWeightsBtn" type="button" class="secondary" data-i18n="resetWeights" data-disabled="true" disabled>重置</button>
            <button id="saveWeightsBtn" type="button" class="secondary" data-i18n="saveWeights" data-disabled="true" disabled>保存权重</button>
          </div>
        </div>
        <div id="brandWeightSalon" class="weight-salon"></div>
        <div id="brandStyleLedger" class="brand-style-ledger"></div>
        <div id="brandWeights" class="watch-grid"></div>
        <div id="weightDraftAudit" class="weight-draft-audit empty"></div>
      </div>
    </section>
    <section class="panel rubric-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandWeightRubric">品牌权重标尺</h2>
          <span class="muted" data-i18n="brandWeightRubricHint">把 0-100 权重拆成核心、重点、采样和档案四档</span>
        </div>
      </div>
      <div id="brandWeightRubric" class="rubric-grid"></div>
    </section>
    <section class="panel playbook-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandPlaybook">品牌作战卡</h2>
          <span class="muted" data-i18n="brandPlaybookHint">把权重、证据、款式词和下一步动作合成单品牌执行卡</span>
        </div>
      </div>
      <div id="brandPlaybook" class="playbook-grid"></div>
    </section>
    <section class="panel scenario-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="weightScenarioCompare">品牌权重情景对比</h2>
          <span class="muted" data-i18n="weightScenarioCompareHint">保存前预览新品、溢价和补证据三种权重草稿</span>
        </div>
        <button id="exportScenariosCsvBtn" type="button" class="secondary" data-i18n="exportScenariosCsv">导出情景 CSV</button>
      </div>
      <div id="weightScenarioCompare" class="scenario-grid"></div>
    </section>
    <section class="panel lookbook-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandLookbook">品牌权重造型册</h2>
          <span class="muted" data-i18n="brandLookbookHint">用 Lolita 风格线索解释权重、样本和下一步盯盘动作</span>
        </div>
      </div>
      <div id="brandLookbook" class="lookbook-grid"></div>
    </section>
    <section class="panel scorecard-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandWeightScorecard">品牌权重评分卡</h2>
          <span class="muted" data-i18n="brandWeightScorecardHint">把基线、溢价、证据、款式词和监控入口拆成可审计分项</span>
        </div>
        <button id="exportScorecardsCsvBtn" type="button" class="secondary" data-i18n="exportScorecardsCsv">导出评分卡 CSV</button>
      </div>
      <div id="brandWeightScorecard" class="scorecard-grid"></div>
    </section>
    <section class="panel guardrail-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandWeightGuardrails">品牌权重护栏</h2>
          <span class="muted" data-i18n="brandWeightGuardrailsHint">标记权重、溢价和样本证据不一致的品牌</span>
        </div>
        <button id="exportGuardrailsCsvBtn" type="button" class="secondary" data-i18n="exportGuardrailsCsv">导出护栏 CSV</button>
      </div>
      <div id="brandWeightGuardrails" class="guardrail-grid"></div>
    </section>
    <section class="panel alert-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="marketAlertLine">雷达预警线</h2>
          <span class="muted" data-i18n="marketAlertHint">聚合强溢价样本、品牌热度和核心品牌样本缺口</span>
        </div>
      </div>
      <div id="marketAlertLine" class="alert-grid"></div>
    </section>
    <section class="panel momentum-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="marketMomentum">二手价动量</h2>
          <span class="muted" data-i18n="marketMomentumHint">比较同品牌最新样本与前序均值，判断升温或降温</span>
        </div>
      </div>
      <div id="marketMomentum" class="momentum-grid"></div>
    </section>
    <section class="panel weight-snapshot-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="weightSnapshot">权重画像</h2>
          <span class="muted" data-i18n="weightSnapshotHint">把品牌档位、价格证据和样本缺口放在一起校准</span>
        </div>
      </div>
      <div id="weightSnapshot" class="weight-snapshot"></div>
    </section>
    <section class="panel strategy-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandWeightStrategy">品牌权重策略台</h2>
          <span class="muted" data-i18n="brandWeightStrategyHint">把权重档、溢价证据和草稿变化转成下一步校准动作</span>
        </div>
      </div>
      <div id="brandWeightStrategy" class="strategy-grid"></div>
    </section>
    <section class="panel trajectory-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="weightTrajectory">权重校准轨迹</h2>
          <span class="muted" data-i18n="weightTrajectoryHint">把当前权重、建议目标和证据信心串成可执行路径</span>
        </div>
      </div>
      <div id="weightTrajectory" class="trajectory-grid"></div>
    </section>
    <section class="panel formula-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandWeightFormula">品牌权重配方</h2>
          <span class="muted" data-i18n="brandWeightFormulaHint">拆解基线、溢价、证据和关注入口，给出可审计目标权重</span>
        </div>
      </div>
      <div id="brandWeightFormula" class="formula-grid"></div>
    </section>
    <section class="panel profile-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandWeightProfile">品牌权重构成</h2>
          <span class="muted" data-i18n="brandWeightProfileHint">解释每个品牌权重如何连接溢价、样本证据和下一步动作</span>
        </div>
      </div>
      <div id="brandWeightProfile" class="profile-grid"></div>
    </section>
    <section class="panel identity-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandIdentityMatrix">品牌身份矩阵</h2>
          <span class="muted" data-i18n="brandIdentityHint">把品牌色、视觉母题、溢价证据和关注线索放在一起校准</span>
        </div>
      </div>
      <div id="brandIdentityMatrix" class="identity-grid"></div>
    </section>
    <section class="panel keyword-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandKeywordRadar">热门款式词</h2>
          <span class="muted" data-i18n="brandKeywordHint">把 AP 贝壳这类款式线索接到价格样本录入</span>
        </div>
      </div>
      <div id="brandKeywordRadar" class="keyword-radar"></div>
    </section>
    <section class="panel core-watch-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="coreMarketWatch">核心品牌盯盘台</h2>
          <span class="muted" data-i18n="coreMarketWatchHint">把高权重品牌、代表款式词、二级市场搜索和补样本入口放在一屏</span>
        </div>
        <div class="brand-actions">
          <button id="exportCoreWatchCsvBtn" type="button" class="secondary" data-i18n="exportCoreWatchCsv">导出盯盘 CSV</button>
        </div>
      </div>
      <div id="coreMarketWatch" class="core-watch-grid"></div>
    </section>
    <section class="panel seed-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="premiumSeedRadar">溢价关注种子</h2>
          <span class="muted" data-i18n="premiumSeedHint">没有足够二手价样本前，先把高权重品牌和代表款式词排进采样队列</span>
        </div>
        <div class="brand-actions">
          <button id="exportPremiumSeedsCsvBtn" type="button" class="secondary" data-i18n="exportPremiumSeedsCsv">导出种子 CSV</button>
        </div>
      </div>
      <div id="premiumSeedSummary" class="seed-summary"></div>
      <div id="premiumSeedRadar" class="seed-grid"></div>
    </section>
    <section class="panel action-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="marketActionDesk">二级市场行动台</h2>
          <span class="muted" data-i18n="marketActionHint">把高权重款式词转成搜索和补样本任务</span>
        </div>
        <button id="exportMarketActionsCsvBtn" type="button" class="secondary" data-i18n="exportMarketActionsCsv">导出行动 CSV</button>
      </div>
      <div id="marketActionDesk" class="action-grid"></div>
    </section>
    <section class="panel price-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="priceDiscipline">价格纪律线</h2>
          <span class="muted" data-i18n="priceDisciplineHint">把品牌权重转成追价上限，并标出二手均价是否过热</span>
        </div>
      </div>
      <div id="priceDiscipline" class="price-grid"></div>
    </section>
    <section class="panel quality-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="evidenceHealth">证据健康</h2>
          <span class="muted" data-i18n="evidenceHealthHint">检查样本是否有来源、链接、日期和备注</span>
        </div>
      </div>
      <div id="evidenceHealth" class="quality-grid"></div>
    </section>
    <section class="panel pattern-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="patternPremiumRadar">款式溢价雷达</h2>
          <span class="muted" data-i18n="patternPremiumHint">把热门款式词和已录二手价样本连起来</span>
        </div>
      </div>
      <div id="patternPremiumRadar" class="pattern-grid"></div>
    </section>
    <section class="panel matrix-board">
      <div class="toolbar matrix-toolbar">
        <div>
          <h2 data-i18n="brandRadarMatrix">品牌雷达矩阵</h2>
          <span class="muted" data-i18n="matrixHint">把权重、溢价、样本和动作放在一起看</span>
        </div>
        <div class="matrix-tools">
          <div id="matrixFilters" class="segmented" role="group" aria-label="Matrix filter">
            <button type="button" data-matrix-filter="all" data-i18n="matrixFilterAll">全部</button>
            <button type="button" data-matrix-filter="focus" data-i18n="matrixFilterFocus">焦点品牌</button>
            <button type="button" data-matrix-filter="lead" data-i18n="matrixFilterLead">重点</button>
            <button type="button" data-matrix-filter="needs_samples" data-i18n="matrixFilterNeedsSamples">缺样本</button>
            <button type="button" data-matrix-filter="core" data-i18n="matrixFilterCore">核心</button>
          </div>
          <label class="matrix-sort">
            <span data-i18n="matrixSortLabel">排序</span>
            <select id="matrixSort">
              <option value="score" data-i18n="matrixSortScore">雷达分</option>
              <option value="premium" data-i18n="matrixSortPremium">均值溢价</option>
              <option value="weight" data-i18n="matrixSortWeight">品牌权重</option>
              <option value="samples" data-i18n="matrixSortSamples">样本数</option>
              <option value="delta" data-i18n="matrixSortDelta">草稿变化</option>
            </select>
          </label>
        </div>
      </div>
      <div id="brandRadarMatrix" class="radar-matrix"></div>
    </section>
    <section class="panel coverage-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="sampleCoverage">样本覆盖</h2>
          <span class="muted" data-i18n="sampleCoverageHint">判断雷达分背后的价格证据厚度</span>
        </div>
      </div>
      <div id="sampleCoverage" class="coverage-grid"></div>
    </section>
    <section class="panel sample-plan-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="samplePlan">样本采集计划</h2>
          <span class="muted" data-i18n="samplePlanHint">按品牌权重和证据缺口安排下一批二手价样本</span>
        </div>
        <div class="brand-actions">
          <button id="exportSamplePlanCsvBtn" type="button" class="secondary" data-i18n="exportSamplePlanCsv">导出采样 CSV</button>
        </div>
      </div>
      <div id="samplePlan" class="sample-plan-grid"></div>
    </section>
    <section class="panel tuning-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="weightTuning">权重校准建议</h2>
          <span class="muted" data-i18n="weightTuningHint">把溢价、样本和当前权重翻译成下一步动作</span>
        </div>
        <div class="brand-actions">
          <span id="tuningBatchSummary" class="muted"></span>
          <button id="applyTuningBatchBtn" type="button" class="secondary" data-disabled="true" disabled data-i18n="tuningApplyAll">全部套用为草稿</button>
        </div>
      </div>
      <div id="weightTuning" class="tuning-grid"></div>
    </section>
    <section class="panel opportunity-board">
      <div class="toolbar opportunity-toolbar">
        <div>
          <h2 data-i18n="opportunityRadar">机会雷达</h2>
          <span class="muted" data-i18n="opportunityHint">基于品牌权重与二手溢价生成关注建议</span>
          <div id="opportunitySummary" class="opportunity-summary"></div>
        </div>
        <div id="opportunityFilters" class="segmented" role="group" aria-label="Opportunity filter">
          <button type="button" data-opportunity-filter="all" data-i18n="filterAll">全部</button>
          <button type="button" data-opportunity-filter="lead" data-i18n="filterLead">重点</button>
          <button type="button" data-opportunity-filter="watch" data-i18n="filterWatch">观察</button>
          <button type="button" data-opportunity-filter="collect_samples" data-i18n="filterSamples">补样本</button>
          <button type="button" data-opportunity-filter="cooldown" data-i18n="filterCooldown">暂缓</button>
        </div>
      </div>
      <div id="opportunityRadar" class="opportunity-grid"></div>
    </section>
    <section class="panel market-board">
      <div class="toolbar">
        <h2 data-i18n="marketPremium">二手溢价观察</h2>
        <span id="marketCount" class="muted"></span>
      </div>
      <form id="marketForm" class="market-form">
        <label>
          <span data-i18n="brandAlias">品牌</span>
          <select id="marketBrand" name="brand_alias" required></select>
        </label>
        <label class="wide">
          <span data-i18n="itemName">款名</span>
          <input id="marketItem" name="item_name" type="text" required placeholder="JSK / OP">
        </label>
        <label>
          <span data-i18n="retailPrice">原价</span>
          <input id="marketRetail" name="retail_price" type="number" min="0" step="0.01" required>
        </label>
        <label>
          <span data-i18n="resalePrice">二手价</span>
          <input id="marketResale" name="resale_price" type="number" min="0" step="0.01" required>
        </label>
        <label>
          <span data-i18n="currency">币种</span>
          <input id="marketCurrency" name="currency" type="text" value="CNY">
        </label>
        <label>
          <span data-i18n="condition">成色</span>
          <input id="marketCondition" name="condition" type="text">
        </label>
        <label>
          <span data-i18n="sourceName">来源</span>
          <input id="marketSource" name="source" type="text" placeholder="xianyu">
        </label>
        <label>
          <span data-i18n="observedAt">日期</span>
          <input id="marketObservedAt" name="observed_at" type="date">
        </label>
        <label class="wide">
          <span data-i18n="sampleUrl">链接</span>
          <input id="marketUrl" name="url" type="url" placeholder="https://">
        </label>
        <label class="wide">
          <span data-i18n="sampleNotes">备注</span>
          <input id="marketNotes" name="notes" type="text">
        </label>
        <button id="addMarketBtn" type="submit" data-i18n="addSample">加入样本</button>
      </form>
      <div id="sampleTaskHint" class="sample-task-hint"></div>
      <div id="samplePreview" class="sample-preview"></div>
      <div class="market-grid">
        <div>
          <h2 data-i18n="premiumByBrand">品牌溢价排行</h2>
          <div id="premiumBrands" class="market-list"></div>
        </div>
        <div>
          <div class="market-heading">
            <h2 data-i18n="premiumRecords">高溢价样本</h2>
            <div class="premium-tools">
              <label class="premium-brand-filter">
                <span data-i18n="premiumBrandFilter">品牌</span>
                <select id="premiumBrandFilter"></select>
              </label>
              <button id="exportPremiumCsvBtn" type="button" class="secondary" data-i18n="exportPremiumCsv" data-disabled="true" disabled>导出 CSV</button>
              <div id="premiumRecordFilters" class="segmented" role="group" aria-label="Premium sample filter">
                <button type="button" data-premium-filter="all" data-i18n="premiumFilterAll">全部</button>
                <button type="button" data-premium-filter="collector" data-i18n="premiumBandCollector">藏品级</button>
                <button type="button" data-premium-filter="hot" data-i18n="premiumBandHot">强溢价</button>
                <button type="button" data-premium-filter="premium" data-i18n="premiumBandPremium">溢价</button>
                <button type="button" data-premium-filter="near_retail" data-i18n="premiumBandNearRetail">近原价</button>
                <button type="button" data-premium-filter="discount" data-i18n="premiumBandDiscount">折价</button>
              </div>
            </div>
          </div>
          <div id="premiumRecords" class="market-list"></div>
        </div>
      </div>
    </section>
    <main class="workspace">
      <section class="panel">
        <h2 data-i18n="sourcesHeading">数据源</h2>
        <div id="sources" class="source-list"></div>
      </section>
      <div class="main-stack">
        <section class="panel">
          <div class="toolbar">
            <h2 data-i18n="recentEvents">最近事件</h2>
            <span id="eventCount" class="muted"></span>
          </div>
          <div id="events" class="event-list"></div>
        </section>
        <section class="panel">
          <div class="toolbar">
            <h2 data-i18n="trackedItemsHeading">跟踪条目</h2>
            <span id="itemCount" class="muted"></span>
          </div>
          <div id="items" class="item-list"></div>
        </section>
      </div>
    </main>
    <div id="toast" class="toast"></div>
    <script>
      const $ = (id) => document.getElementById(id);
      const translations = {
        zh: {
          eyebrow: "二级市场情报台",
          headline: "监控日牌上新、预约、再贩与二级市场溢价线索。",
          heroVisualTitle: "蕾丝雷达 · 溢价巡航",
          heroVisualWeight: "品牌权重",
          heroVisualPremium: "溢价热度",
          heroVisualEvidence: "样本证据",
          navNorthStar: "北极星",
          navCrown: "皇冠",
          navStylePremium: "行情",
          navDaily: "简报",
          navPortfolio: "组合",
          navReleaseWatch: "发售",
          navWeights: "权重",
          navRubric: "标尺",
          navPlaybook: "作战卡",
          navScenarios: "情景",
          navRunSheet: "巡检",
          navLookbook: "造型册",
          navScorecard: "评分卡",
          navGuardrails: "护栏",
          navCoreWatch: "盯盘",
          navIdentity: "身份",
          navTrajectory: "轨迹",
          navFormula: "配方",
          navMatrix: "矩阵",
          navPremium: "溢价",
          navPricing: "价格线",
          navEvidence: "证据",
          navSampling: "采样",
          navSources: "监控源",
          themeSweet: "Sweet",
          themeClassic: "Classic",
          themeGothic: "Gothic",
          themeChanged: "主题已切换",
          checkAll: "检查全部",
          refresh: "刷新",
          sourcesHeading: "监控源",
          recentEvents: "上新动态",
          trackedItemsHeading: "雷达条目",
          northStarRadar: "北极星雷达",
          northStarHint: "用品牌权重、证据覆盖、发售热度和巡检压力判断今日雷达成熟度",
          northStarScore: "北极星分",
          northStarLead: "今日主线",
          northStarMaturity: "雷达成熟度",
          northStarWeightedCoverage: "加权证据覆盖",
          northStarReleaseHeat: "发售热度",
          northStarRunSheetHeat: "巡检压力",
          northStarPremiumHeat: "溢价支撑",
          northStarEvidenceLane: "证据底座",
          northStarReleaseLane: "发售窗口",
          northStarPremiumLane: "溢价确认",
          northStarExecutionLane: "执行压力",
          northStarEvidenceDetail: "高权重品牌需要至少两条价格样本",
          northStarReleaseDetail: "上新/预约/再贩命中品牌权重",
          northStarPremiumDetail: "二级市场样本是否支持追踪",
          northStarExecutionDetail: "今日巡检任务的平均压力",
          brandCrownQueue: "品牌皇冠队列",
          brandCrownHint: "把核心品牌、贝壳等款式词、二手溢价和上新命中排成今日重点",
          crownScore: "皇冠优先",
          crownLead: "首位品牌",
          crownCoreReady: "核心就绪",
          crownKeywordTotal: "款式词",
          crownReleaseSignals: "发售命中",
          crownPremiumBacked: "溢价支撑",
          crownAction: "今日动作",
          crownActionAnchor: "补价格锚点",
          crownActionRelease: "追发售窗口",
          crownActionPremium: "看二手溢价",
          crownActionHold: "维持巡航",
          crownSample: "补样本",
          crownKeywordSample: "补款式",
          crownOpenRelease: "看发售",
          crownNoRows: "暂无皇冠品牌",
          crownConfidence: "置信度",
          crownConfidenceHigh: "证据稳",
          crownConfidenceMedium: "可观察",
          crownConfidenceLow: "待补证据",
          exportCrownCsv: "导出皇冠 CSV",
          exportedCrownCsv: "品牌皇冠队列已导出",
          noCrownCsv: "暂无可导出的皇冠队列",
          draftRiskRadar: "权重草稿风险",
          draftRiskHint: "把未保存的调权风险提前放到首页，保存前先复核",
          draftRiskScore: "草稿风险分",
          draftRiskClean: "暂无未保存调权",
          draftRiskCleanHint: "当前品牌权重与已保存配置一致",
          draftRiskNoOpen: "暂无高风险",
          draftRiskNoOpenHint: "草稿已有变更，但未触发保存前拦截项",
          draftRiskReview: "查看权重",
          draftRiskChanged: "变更",
          draftRiskOpen: "风险",
          draftRiskMaxMove: "最大移动",
          stylePremiumTape: "风格溢价行情",
          stylePremiumTapeHint: "按 Sweet、Classic、Gothic 等风格线查看二级市场溢价和证据状态",
          stylePremiumLead: "领跑行情",
          stylePremiumHeat: "行情热度",
          stylePremiumAvg: "均溢价",
          stylePremiumWeighted: "权重热度",
          stylePremiumSamples: "样本",
          stylePremiumSpread: "均价差",
          stylePremiumAction: "今日动作",
          stylePremiumEvidence: "证据覆盖",
          stylePremiumPremiumSignals: "溢价品牌",
          stylePremiumCollect: "补价格样本",
          stylePremiumTrack: "追溢价",
          stylePremiumWatch: "持续盯价",
          stylePremiumReview: "折价复核",
          stylePremiumHold: "等待信号",
          dailyRadarBrief: "今日雷达简报",
          dailyRadarBriefHint: "把核心盯盘、权重校准和采样缺口收成今天的行动队列",
          dailyLead: "主线",
          dailyActions: "行动",
          dailyActionLanes: "行动分组",
          dailySampleGaps: "样本缺口",
          dailyAvgPriority: "均值优先",
          dailyJump: "查看",
          dailySample: "补样本",
          dailyKeyword: "补款式",
          dailyLaneAll: "全部行动",
          dailyNoActions: "暂无今日行动",
          dailyNoFilteredActions: "当前分组暂无行动",
          dailyKindCore: "核心盯盘",
          dailyKindScorecard: "权重评分",
          dailyKindSampling: "采样计划",
          exportDailyCsv: "导出简报 CSV",
          exportedDailyCsv: "今日雷达简报已导出",
          noDailyCsv: "暂无可导出的今日简报",
          resaleRunSheet: "二级市场巡检清单",
          resaleRunSheetHint: "把今日行动、查价任务和补价格锚点收成一张执行表",
          runSheetTasks: "巡检任务",
          runSheetAnchorGaps: "待补锚点",
          runSheetSearches: "查价搜索",
          runSheetSamples: "补样本",
          runSheetDaily: "今日行动",
          runSheetRelease: "发售关注",
          runSheetMarket: "查价任务",
          runSheetPrice: "价格锚点",
          runSheetGo: "执行",
          runSheetSample: "补样本",
          runSheetNoRows: "暂无巡检任务",
          exportRunSheetCsv: "导出巡检 CSV",
          exportedRunSheetCsv: "巡检清单已导出",
          noRunSheetCsv: "暂无可导出的巡检任务",
          brandPortfolio: "品牌组合总览",
          brandPortfolioHint: "把证据覆盖、核心缺口、溢价热度和权重偏移合成总览",
          portfolioHealth: "组合健康",
          portfolioCoverage: "证据覆盖",
          portfolioCoreGaps: "核心缺口",
          portfolioHeat: "溢价热度",
          portfolioDrift: "权重偏移",
          portfolioActions: "待处理",
          portfolioEvidenceLane: "证据覆盖",
          portfolioCoreLane: "核心缺口",
          portfolioPremiumLane: "溢价热度",
          portfolioDriftLane: "权重偏移",
          portfolioEvidenceHint: "样本不足会让溢价和权重判断不稳",
          portfolioCoreHint: "核心品牌缺价格锚点时，先补原价/二手价",
          portfolioPremiumHint: "强溢价品牌需要持续查价和确认款式词",
          portfolioDriftHint: "目标权重与当前权重偏离时，先审计再保存",
          portfolioReview: "查看",
          portfolioSample: "补样本",
          exportPortfolioCsv: "导出组合 CSV",
          exportedPortfolioCsv: "品牌组合总览已导出",
          noPortfolioCsv: "暂无可导出的组合总览",
          releaseWatchQueue: "新品发售关注队列",
          releaseWatchHint: "把上新/预约/再贩条目接到品牌权重和二手溢价判断",
          releaseWatchScore: "发售关注分",
          releaseWatchSignals: "发售信号",
          releaseWatchBrands: "匹配品牌",
          releaseWatchTopScore: "最高分",
          releaseWatchPremium: "溢价支持",
          releaseWatchNoRows: "暂无匹配到品牌权重的上新条目",
          releaseWatchMatched: "匹配词",
          releaseWatchSource: "来源",
          releaseWatchOpen: "打开原页",
          releaseWatchSample: "补价格样本",
          exportReleaseWatchCsv: "导出发售 CSV",
          exportedReleaseWatchCsv: "发售关注队列已导出",
          noReleaseWatchCsv: "暂无可导出的发售关注",
          releaseActionSample: "先补价格锚点",
          releaseActionTrackPremium: "跟踪二手溢价",
          releaseActionWatchDrop: "盯发售窗口",
          releaseActionReview: "复核发售信号",
          marketSignal: "溢价信号",
          brandWeights: "品牌权重",
          saveWeights: "保存权重",
          resetWeights: "重置",
          exportWeightsCsv: "导出权重 CSV",
          exportedWeightsCsv: "品牌权重已导出",
          noWeightsCsv: "暂无可导出的品牌权重",
          weightsClean: "已保存",
          weightsDirty: "项未保存",
          weightsRisk: "项风险",
          scenarioRelease: "新品优先",
          scenarioPremium: "溢价优先",
          scenarioEvidence: "补证据优先",
          scenarioApplied: "已生成权重情景草稿",
          exportScenariosCsv: "导出情景 CSV",
          exportedScenariosCsv: "权重情景已导出",
          noScenariosCsv: "暂无可导出的权重情景",
          weightScenarioCompare: "品牌权重情景对比",
          weightScenarioCompareHint: "保存前预览新品、溢价和补证据三种权重草稿",
          scenarioAvgTarget: "均值目标",
          scenarioChanged: "变动",
          scenarioRaised: "上调",
          scenarioLowered: "下调",
          scenarioApplyDraft: "套用情景",
          scenarioTopMoves: "重点变化",
          scenarioNoMoves: "暂无变化",
          weightDraftAudit: "草稿审计",
          weightDraftClean: "暂无权重草稿变更",
          weightDraftChanged: "项变更",
          weightDraftSaved: "原",
          weightDraftCurrent: "新",
          weightDraftDelta: "变化",
          weightDraftAvgDelta: "均值变化",
          weightDraftRaised: "上调",
          weightDraftLowered: "下调",
          weightDraftMaxMove: "最大变化",
          weightDraftRiskCoreDown: "核心下调",
          weightDraftRiskThinRaise: "缺样本上调",
          weightDraftRiskLargeMove: "大幅变化",
          weightDraftRiskArchiveJump: "档案升权",
          weightDraftRiskCoreDownHint: "核心品牌被明显下调，保存前复核发售和溢价证据",
          weightDraftRiskThinRaiseHint: "样本不足却上调，建议先补原价和二手价",
          weightDraftRiskLargeMoveHint: "变化幅度较大，适合先作为情景草稿观察",
          weightDraftRiskArchiveJumpHint: "档案品牌升到观察档，确认是否有新溢价或新发售信号",
          brandWeightRubric: "品牌权重标尺",
          brandWeightRubricHint: "把 0-100 权重拆成核心、重点、采样和档案四档",
          rubricRange: "权重区间",
          rubricAvgWeight: "均权",
          rubricAvgPremium: "均值溢价",
          rubricSampleGaps: "样本缺口",
          rubricBrands: "品牌数",
          rubricCore: "核心发售",
          rubricCoreHint: "AP、BABY、AATP 这类优先看新品、预约、再贩和二手锚点",
          rubricLead: "重点盯盘",
          rubricLeadHint: "溢价或上新信号足够时升级，先看款式词和样本厚度",
          rubricSeed: "采样种子",
          rubricSeedHint: "适合先补原价/二手价样本，确认是否有异常溢价",
          rubricArchive: "档案低频",
          rubricArchiveHint: "低频观察，除非出现强溢价或明确发售信号再上调",
          rubricNoBrands: "暂无品牌",
          rubricReviewWeights: "查看权重",
          rubricSampleGap: "补首个缺口",
          brandPlaybook: "品牌作战卡",
          brandPlaybookHint: "把权重、证据、款式词和下一步动作合成单品牌执行卡",
          playbookAction: "下一步",
          playbookPrimaryTerm: "主搜词",
          playbookTarget: "目标权重",
          playbookSample: "补样本",
          playbookKeyword: "补款式",
          playbookApply: "套用目标",
          playbookNoRows: "暂无品牌作战卡",
          playbookActionAnchor: "先补价格锚点",
          playbookActionPair: "补第二条样本",
          playbookActionTrack: "追踪溢价价差",
          playbookActionRaise: "准备上调权重",
          playbookActionCool: "复核降温",
          playbookActionHold: "维持观察",
          playbookReasonCore: "核心高权重",
          playbookReasonThin: "样本偏薄",
          playbookReasonPremium: "溢价支撑",
          playbookReasonDiscount: "折价复核",
          playbookReasonTarget: "目标权重变化",
          playbookReasonKeyword: "款式词明确",
          draftPreview: "草稿预览",
          scoreDelta: "变化",
          weightsReset: "品牌权重已重置",
          weightsSaved: "品牌权重已保存",
          styleFamilySweet: "Sweet 印花线",
          styleFamilyClassic: "Classic 古典线",
          styleFamilyGothic: "Gothic 暗色线",
          styleFamilyRelease: "Release 上新线",
          styleFamilyArt: "Art Print 艺术线",
          styleBrands: "个品牌",
          styleAvgWeight: "均权",
          styleLeader: "领跑",
          styleWeightTotal: "总权重",
          styleCoreShare: "核心",
          styleKeywords: "风格款式词",
          styleNoKeywords: "暂无款式词",
          brandWeightSalon: "品牌权重沙龙",
          brandWeightSalonHint: "按 Lolita 风格线复核草稿权重、配方目标和证据缺口",
          salonLead: "领跑风格",
          salonAvgDraft: "草稿均权",
          salonTargetAvg: "配方均权",
          salonEvidenceGap: "证据缺口",
          salonCoreShare: "核心占比",
          salonPremiumSignals: "溢价信号",
          salonMove: "移动",
          salonActionCollect: "先补证据",
          salonActionLift: "可上调",
          salonActionTrim: "可降温",
          salonActionHold: "维持观察",
          brandLookbook: "品牌权重造型册",
          brandLookbookHint: "用 Lolita 风格线索解释权重、样本和下一步盯盘动作",
          lookbookLead: "主推",
          lookbookCore: "核心",
          lookbookGaps: "缺样本",
          lookbookAvgFit: "平均契合",
          lookbookFit: "契合",
          lookbookSample: "补样本",
          lookbookKeyword: "补款式",
          lookbookActionAnchor: "补价格锚点",
          lookbookActionTrack: "追踪溢价",
          lookbookActionReview: "复核折价",
          lookbookActionWatch: "继续观察",
          lookbookNoRows: "暂无品牌造型册",
          brandWeightScorecard: "品牌权重评分卡",
          brandWeightScorecardHint: "把基线、溢价、证据、款式词和监控入口拆成可审计分项",
          scorecardTop: "最高目标",
          scorecardAligned: "已对齐",
          scorecardCollect: "待补证据",
          scorecardAvgConfidence: "平均置信度",
          scorecardCurrent: "当前",
          scorecardTarget: "目标",
          scorecardVerdict: "结论",
          scorecardNoRows: "暂无权重评分卡",
          exportScorecardsCsv: "导出评分卡 CSV",
          exportedScorecardsCsv: "权重评分卡已导出",
          noScorecardsCsv: "暂无可导出的权重评分卡",
          brandWeightGuardrails: "品牌权重护栏",
          brandWeightGuardrailsHint: "标记权重、溢价和样本证据不一致的品牌",
          guardrailRiskScore: "护栏风险",
          guardrailOpen: "待复核",
          guardrailAvgConfidence: "均值置信",
          guardrailCoverage: "证据覆盖",
          guardrailCritical: "强复核",
          guardrailWatch: "观察",
          guardrailStable: "稳定",
          guardrailTarget: "护栏目标",
          guardrailNoRows: "当前权重、溢价和样本证据基本匹配",
          guardrailCoreGap: "核心缺锚点",
          guardrailUnderweighted: "溢价低权重",
          guardrailOverweighted: "折价高权重",
          guardrailArchiveHot: "档案升温",
          guardrailReasonCoreGap: "高权重品牌缺少成对价格样本，先补原价和二手价",
          guardrailReasonUnderweighted: "已有溢价样本支撑，但权重还没有跟上",
          guardrailReasonOverweighted: "高权重品牌出现折价均值，保存前需要复核",
          guardrailReasonArchiveHot: "低权重品牌出现正溢价，适合进入观察池",
          guardrailActionSample: "补样本",
          guardrailActionApply: "套用目标",
          exportGuardrailsCsv: "导出护栏 CSV",
          exportedGuardrailsCsv: "权重护栏已导出",
          noGuardrailsCsv: "暂无可导出的权重护栏",
          brandRadarMatrix: "品牌雷达矩阵",
          matrixHint: "把权重、溢价、样本和动作放在一起看",
          matrixBrand: "品牌",
          matrixScore: "雷达分",
          matrixWeight: "权重",
          matrixPremium: "均值溢价",
          matrixSamples: "样本",
          matrixAction: "动作",
          matrixFilterAll: "全部",
          matrixFilterFocus: "焦点品牌",
          matrixFilterLead: "重点",
          matrixFilterNeedsSamples: "缺样本",
          matrixFilterCore: "核心",
          matrixSortLabel: "排序",
          matrixSortScore: "雷达分",
          matrixSortPremium: "均值溢价",
          matrixSortWeight: "品牌权重",
          matrixSortSamples: "样本数",
          matrixSortDelta: "草稿变化",
          opportunityRadar: "机会雷达",
          opportunityHint: "基于品牌权重与二手溢价生成关注建议",
          filterAll: "全部",
          filterLead: "重点",
          filterWatch: "观察",
          filterSamples: "补样本",
          filterCooldown: "暂缓",
          focusQueue: "重点关注队列",
          marketAlertLine: "雷达预警线",
          marketAlertHint: "聚合强溢价样本、品牌热度和核心品牌样本缺口",
          alertTotal: "预警项",
          alertCritical: "强预警",
          alertWatch: "观察预警",
          alertSampleGap: "缺样本",
          alertScore: "预警分",
          noAlerts: "暂无预警项",
          marketMomentum: "二手价动量",
          marketMomentumHint: "比较同品牌最新样本与前序均值，判断升温或降温",
          momentumTotal: "动量品牌",
          momentumRisingCount: "升温",
          momentumCoolingCount: "降温",
          momentumSteadyCount: "稳定",
          momentumLatest: "最新",
          momentumPrevious: "前序均值",
          momentumDelta: "变化",
          momentumRising: "升温",
          momentumCooling: "降温",
          momentumSteady: "稳定",
          noMomentum: "至少需要同品牌 2 条样本后显示走势",
          marketPremium: "二手溢价观察",
          premiumByBrand: "品牌溢价排行",
          premiumRecords: "高溢价样本",
          premiumBrandFilter: "品牌",
          premiumBrandAll: "全部品牌",
          exportPremiumCsv: "导出 CSV",
          exportedPremiumCsv: "已导出当前筛选样本",
          noPremiumCsv: "暂无可导出的样本",
          premiumFilterAll: "全部",
          premiumBandCollector: "藏品级",
          premiumBandHot: "强溢价",
          premiumBandPremium: "溢价",
          premiumBandNearRetail: "近原价",
          premiumBandDiscount: "折价",
          priceCorridor: "价格走廊",
          retailRange: "原价",
          resaleRange: "二手",
          avgSpread: "均价差",
          metricSources: "数据源",
          metricTrackedItems: "跟踪条目",
          metricEvents: "事件",
          metricLatestEvent: "最新事件",
          metricLatestSource: "最新来源",
          signalSummary: "基于最近事件状态估算关注热度",
          noStatus: "暂无状态数据",
          tierCore: "核心",
          tierWatch: "观察",
          tierArchive: "档案",
          weightLabel: "权重",
          weightBand: "权重档",
          weightIntent: "用途",
          keywordCount: "关键词",
          visualMotif: "视觉",
          weightBandCore: "核心发售",
          weightBandWatch: "重点观察",
          weightBandArchive: "档案采样",
          weightIntentCore: "新品、预约、淘宝上新优先提醒",
          weightIntentWatch: "结合溢价和样本决定是否升级",
          weightIntentArchive: "先补二手样本，异常溢价再上调",
          weightSnapshot: "权重画像",
          weightSnapshotHint: "把品牌档位、价格证据和样本缺口放在一起校准",
          brandWeightProfile: "品牌权重构成",
          brandWeightProfileHint: "解释每个品牌权重如何连接溢价、样本证据和下一步动作",
          brandIdentityMatrix: "品牌身份矩阵",
          brandIdentityHint: "把品牌色、视觉母题、溢价证据和关注线索放在一起校准",
          identityCoverage: "身份覆盖",
          identityCoreCount: "核心身份",
          identityWatchCount: "观察身份",
          identityArchiveCount: "档案身份",
          identityPalette: "色系",
          identityEvidence: "证据",
          identityPremium: "溢价",
          weightAverage: "平均权重",
          weightCoreAverage: "核心均值",
          weightEvidenceCoverage: "证据覆盖",
          weightNeedsEvidence: "待补证据",
          weightDistribution: "权重分布",
          weightCommandDeck: "品牌权重司令塔",
          weightCommandHint: "优先看高权重、低样本和强溢价交叉点",
          weightRadarMap: "品牌权重盘",
          weightRadarCore: "核心",
          weightRadarWatch: "观察",
          weightRadarArchive: "档案",
          profileWeight: "权重",
          profileHeat: "热度",
          profileEvidence: "证据",
          profileKeywords: "款式词",
          profileNoKeywords: "暂无款式词",
          noBrandProfile: "暂无品牌权重画像",
          weightCoreCount: "核心档",
          weightWatchCount: "观察档",
          weightArchiveCount: "档案档",
          weightTopGap: "优先补样本",
          weightNoGap: "样本缺口已清空",
          brandWeightStrategy: "品牌权重策略台",
          brandWeightStrategyHint: "把权重档、溢价证据和草稿变化转成下一步校准动作",
          weightTrajectory: "权重校准轨迹",
          weightTrajectoryHint: "把当前权重、建议目标和证据信心串成可执行路径",
          trajectoryChanged: "待校准",
          trajectoryStable: "已对齐",
          trajectoryAvgTarget: "均值目标",
          trajectoryAvgShift: "均值偏移",
          trajectoryCurrent: "当前",
          trajectoryTarget: "目标",
          trajectoryApply: "套用目标",
          trajectorySample: "补样本",
          trajectoryRaise: "上调轨迹",
          trajectoryLower: "下调轨迹",
          trajectoryCollect: "先补证据",
          trajectoryAligned: "权重对齐",
          trajectoryNoRows: "暂无权重轨迹",
          brandWeightFormula: "品牌权重配方",
          brandWeightFormulaHint: "拆解基线、溢价、证据和关注入口，给出可审计目标权重",
          formulaBase: "基线",
          formulaPremium: "溢价",
          formulaEvidence: "证据",
          formulaKeywords: "款式词",
          formulaWatchability: "入口",
          formulaTarget: "建议目标",
          formulaConfidence: "置信度",
          formulaSummary: "配方贡献总览",
          formulaSummaryHint: "把当前品牌池的目标权重拆成基线、溢价、证据、款式词和监控入口",
          formulaAvgTarget: "均值目标",
          formulaAvgConfidence: "均值置信",
          formulaCollectCount: "待补证据",
          formulaLeadMove: "最大偏移",
          formulaApplyDraft: "套用目标",
          formulaAligned: "已匹配",
          formulaRaise: "建议上调",
          formulaLower: "建议下调",
          formulaNoRows: "暂无权重配方",
          formulaDraftApplied: "已套用配方目标",
          strategyHeat: "策略温度",
          strategyActionable: "待处理动作",
          strategyCoverage: "证据覆盖",
          strategyAvgWeight: "平均权重",
          strategyCoreLane: "核心守门",
          strategyWatchLane: "观察池",
          strategyArchiveLane: "档案池",
          strategyNextMoves: "下一步校准",
          strategyCollect: "先补证据",
          strategyRaise: "建议上调",
          strategyCooldown: "降温复核",
          strategyHold: "维持权重",
          strategyBaseline: "低频观察",
          strategyMonitor: "继续观察",
          strategyReasonCoreGap: "高权重但价格样本不足",
          strategyReasonPremiumRaise: "溢价已有样本支撑",
          strategyReasonDiscountCool: "二手折价，需要复核权重",
          strategyReasonHoldCore: "溢价和样本支撑当前权重",
          strategyReasonArchiveGap: "低权重且证据不足",
          strategyReasonMonitor: "权重、溢价和样本暂时平衡",
          strategyTarget: "目标",
          strategyNoMoves: "暂无校准动作",
          brandKeywordRadar: "热门款式词",
          brandKeywordHint: "把 AP 贝壳这类款式线索接到价格样本录入",
          coreMarketWatch: "核心品牌盯盘台",
          coreMarketWatchHint: "把高权重品牌、代表款式词、二级市场搜索和补样本入口放在一屏",
          coreWatchBrands: "盯盘品牌",
          coreWatchThin: "薄样本",
          coreWatchAvgScore: "均值分",
          coreWatchTerms: "款式线索",
          coreWatchSearch: "搜索入口",
          coreWatchSample: "补样本",
          coreWatchCue: "关注提示",
          coreWatchReasonCore: "核心高权重",
          coreWatchReasonThin: "先补二手样本",
          coreWatchReasonStrongPremium: "强溢价证据",
          coreWatchReasonPositivePremium: "正溢价线索",
          coreWatchReasonDiscount: "折价复核",
          coreWatchReasonKeywordRich: "款式词充足",
          coreWatchReasonWatch: "观察档追踪",
          coreWatchPriceAnchor: "价格锚点",
          coreWatchRetailAnchor: "原价均值",
          coreWatchResaleAnchor: "二手均值",
          coreWatchSpreadAnchor: "均价差",
          coreWatchPriceMissing: "待补价格锚点",
          coreWatchAnchorGaps: "锚点缺口",
          coreWatchAnchorReady: "锚点已建",
          coreWatchPriceStatusReady: "已建价格锚点",
          coreWatchPriceStatusMissing: "待补价格锚点",
          coreWatchActionAnchor: "补价格锚点",
          coreWatchActionPair: "补第二条样本",
          coreWatchActionTrack: "继续追价差",
          coreWatchActionReview: "复核折价",
          coreWatchActionHold: "维持观察",
          exportCoreWatchCsv: "导出盯盘 CSV",
          exportedCoreWatchCsv: "核心盯盘清单已导出",
          noCoreWatchCsv: "暂无可导出的核心盯盘清单",
          noCoreWatch: "暂无核心盯盘品牌",
          premiumSeedRadar: "溢价关注种子",
          premiumSeedHint: "没有足够二手价样本前，先把高权重品牌和代表款式词排进采样队列",
          premiumSeedTerms: "溢价种子词",
          premiumSeedEmpty: "暂无溢价种子",
          premiumSeedIntentCoreGap: "核心品牌缺少二手价证据，优先采样",
          premiumSeedIntentPremium: "已有正溢价线索，继续追踪代表款",
          premiumSeedIntentSeed: "先建立原价/二手价样本底座",
          premiumSeedIntentWatch: "样本可继续扩展，观察价格走向",
          exportPremiumSeedsCsv: "导出种子 CSV",
          exportedPremiumSeedsCsv: "溢价种子已导出",
          noPremiumSeedsCsv: "暂无可导出的溢价种子",
          premiumSeedTaskCount: "种子任务",
          premiumSeedCoreGaps: "核心缺口",
          premiumSeedTopSeed: "第一优先",
          premiumSeedAvgScore: "平均种子分",
          premiumSeedStageSeed: "先建样本",
          premiumSeedStagePair: "补第二条",
          premiumSeedStageExpand: "扩样本",
          premiumSeedStageWatch: "继续观察",
          marketKeywords: "二级市场词",
          noMarketKeywords: "暂无热门款式词",
          keywordSampleReady: "已填入款式词，可补价格样本",
          patternPremiumRadar: "款式溢价雷达",
          patternPremiumHint: "把热门款式词和已录二手价样本连起来",
          noPatternPremium: "暂无款式词雷达数据",
          patternSample: "补这个款",
          marketActionDesk: "二级市场行动台",
          marketActionHint: "把高权重款式词转成搜索和补样本任务",
          actionTotal: "待办款式",
          actionNeedsSamples: "待补样本",
          actionWithSamples: "已有样本",
          actionSearch: "搜索入口",
          actionQuery: "搜索词",
          actionGoofish: "闲鱼",
          actionTaobao: "淘宝",
          actionMercari: "Mercari",
          actionYahoo: "雅虎拍卖",
          exportMarketActionsCsv: "导出行动 CSV",
          exportedMarketActionsCsv: "二级市场行动清单已导出",
          noMarketActionsCsv: "暂无可导出的二级市场行动",
          priceDiscipline: "价格纪律线",
          priceDisciplineHint: "把品牌权重转成追价上限，并标出二手均价是否过热",
          priceDisciplineCeiling: "追价上限",
          priceDisciplineObserved: "二手均价",
          priceDisciplineGap: "价差空间",
          priceDisciplineRows: "价格线",
          priceDisciplineRoom: "可继续追",
          priceDisciplineNear: "接近上限",
          priceDisciplineHot: "过热复核",
          priceDisciplineSample: "先补锚点",
          priceDisciplineMissing: "待补锚点",
          priceDisciplineNoRows: "暂无足够价格锚点生成纪律线",
          priceDisciplineSampleAction: "补价格样本",
          evidenceHealth: "证据健康",
          evidenceHealthHint: "检查样本是否有来源、链接、日期和备注",
          qualityScore: "质量分",
          qualityLinked: "有链接",
          qualitySourced: "有来源",
          qualityDated: "有日期",
          qualityNoted: "有备注",
          qualityWeak: "待补强",
          weightTuning: "权重校准建议",
          weightTuningHint: "把溢价、样本和当前权重翻译成下一步动作",
          noWeightTuning: "暂无校准建议",
          tuningTarget: "建议权重",
          tuningReason: "理由",
          tuningCollect: "补价格样本",
          tuningRaise: "考虑上调",
          tuningHold: "维持观察",
          tuningCool: "降温复核",
          tuningBaseline: "低频采样",
          tuningCollectReason: "权重较高但样本不足，先补原价和二手价",
          tuningRaiseReason: "溢价已有样本支撑，权重可向核心档靠近",
          tuningHoldReason: "权重、溢价和样本暂时匹配",
          tuningCoolReason: "二手折价或热度不足，保存权重前先复核",
          tuningBaselineReason: "低权重且样本不足，保持低频观察",
          tuningAddSample: "去补样本",
          tuningApplyDraft: "套用为草稿",
          tuningApplyAll: "全部套用为草稿",
          tuningBatchReady: "条可套用",
          tuningBatchEmpty: "暂无可套用",
          tuningBatchApplied: "已批量套用权重草稿",
          tuningDraftApplied: "已套用建议权重",
          tuningSampleReady: "已选中品牌，可补价格样本",
          sampleCoverage: "样本覆盖",
          sampleCoverageHint: "判断雷达分背后的价格证据厚度",
          samplePlan: "样本采集计划",
          samplePlanHint: "按品牌权重和证据缺口安排下一批二手价样本",
          samplePlanTarget: "目标",
          samplePlanMissing: "缺口",
          samplePlanProgress: "采样进度",
          samplePlanNoRows: "当前没有待采样品牌",
          samplePlanSeed: "补首样本",
          samplePlanPair: "补配对样本",
          samplePlanRoundout: "补齐目标",
          samplePlanComplete: "观察复核",
          samplePlanCritical: "核心缺口",
          samplePlanWatch: "重点补样",
          samplePlanBackfill: "档案补样",
          samplePlanDone: "已达标",
          samplePlanSampleReady: "已选中采样品牌",
          exportSamplePlanCsv: "导出采样 CSV",
          exportedSamplePlanCsv: "采样计划已导出",
          noSamplePlanCsv: "暂无可导出的采样计划",
          samplePlanCompletion: "完成率",
          samplePlanOpenBrands: "待采品牌",
          samplePlanCoreGaps: "核心缺口",
          samplePlanTotalMissing: "总缺口",
          samplePlanAvgPriority: "均值优先分",
          coverageReady: "充分",
          coverageThin: "偏薄",
          coverageMissing: "缺样本",
          coverageProgress: "覆盖率",
          coveragePriority: "优先补样本",
          coverageGoal: "目标 2 个样本起步，5 个样本更稳",
          radarScore: "雷达分",
          observed: "已捕捉",
          noFocusQueue: "暂无关注队列",
          noMarket: "暂无价格样本",
          noOpportunity: "暂无机会雷达数据",
          samples: "样本",
          avgPremium: "均值",
          maxPremium: "最高",
          priorityScore: "权重修正分",
          premiumPoints: "溢价",
          brandPoints: "品牌",
          samplePoints: "样本",
          retailPrice: "原价",
          resalePrice: "二手价",
          brandAlias: "品牌",
          itemName: "款名",
          currency: "币种",
          condition: "成色",
          sourceName: "来源",
          observedAt: "日期",
          sampleUrl: "链接",
          sampleNotes: "备注",
          evidence: "证据",
          noEvidence: "暂无匹配证据",
          addSample: "加入样本",
          sampleAdded: "价格样本已加入",
          samplePreview: "样本预览",
          samplePreviewEmpty: "输入原价和二手价后预览溢价",
          sampleTaskAnchorTitle: "价格锚点任务",
          sampleTaskAnchorHint: "填写原价、二手价和来源后保存样本",
          coreWatchTaskSource: "core-watch",
          coreWatchTaskNotePrefix: "核心盯盘",
          sampleSpread: "差价",
          sampleScore: "单样本分",
          sampleSignalStrong: "强溢价样本",
          sampleSignalPositive: "正溢价样本",
          sampleSignalDiscount: "折价样本",
          sampleSignalNeutral: "接近原价",
          premiumBand: {
            collector: "藏品级",
            hot: "强溢价",
            premium: "溢价",
            near_retail: "近原价",
            discount: "折价",
          },
          alertKind: {
            sample_spike: "样本异动",
            brand_heat: "品牌升温",
            sample_gap: "样本缺口",
          },
          alertSeverity: {
            critical: "强预警",
            watch: "观察",
            sample_gap: "补样本",
          },
          alertReason: {
            weak_evidence_spike: "强溢价但证据偏弱",
            collector_premium: "藏品级溢价样本",
            hot_premium: "强溢价样本",
            weighted_spike: "品牌权重修正后触发",
            premium_watch: "溢价观察",
            brand_hot_average: "品牌均值升温",
            core_needs_samples: "核心品牌缺价格样本",
          },
          opportunityBand: {
            lead: "重点盯新款",
            watch: "持续观察",
            collect_samples: "补价格样本",
            cooldown: "暂缓",
          },
          weightRole: {
            release_priority: "新品/预约优先提醒",
            premium_watch: "溢价变化重点观察",
            evidence_sampling: "低频补样本",
          },
          evidenceLevel: {
            ready: "证据充分",
            thin: "证据偏薄",
            missing: "缺价格样本",
          },
          reasonCode: {
            core_brand: "核心品牌",
            watch_brand: "观察品牌",
            needs_samples: "样本不足",
            sample_supported: "样本支撑",
            strong_premium: "强溢价",
            positive_premium: "正溢价",
            discounted_resale: "二手折价",
            baseline: "基础权重",
          },
          noSources: "暂无配置数据源。",
          noEvents: "暂无事件。运行检查后会先建立基线。",
          noItems: "暂无跟踪条目。",
          noKeywords: "未设置关键词",
          enabled: "启用",
          disabled: "停用",
          checkSource: "检查此源",
          disabledButton: "已停用",
          allSources: "全部源",
          checked: "已检查",
          refreshed: "已刷新",
          undated: "无日期",
          seen: "最后看到",
          eventType: {
            new_item: "新条目",
            update: "更新",
          },
          status: {
            new_arrival: "新品上新",
            preorder: "预约",
            restock: "再贩/补货",
            shop_news: "店铺消息",
          },
          sourceType: {
            metamorphose: "Metamorphose 官方新闻",
            generic_page: "通用页面",
          },
          brandStyle: {
            "sweet print": "甜系原创印花",
            "classic sweet": "经典甜系",
            "gothic prince": "哥特王子系",
            "release/restock": "上新/再贩",
            gothic: "哥特",
            classic: "古典",
            "art print": "艺术印花",
          },
        },
        en: {
          eyebrow: "Secondary Market Desk",
          headline: "Track Japanese brand releases, preorders, restocks, and resale-premium signals.",
          heroVisualTitle: "Lace Radar · Premium Watch",
          heroVisualWeight: "brand weight",
          heroVisualPremium: "premium heat",
          heroVisualEvidence: "sample evidence",
          navNorthStar: "North Star",
          navCrown: "Crown",
          navStylePremium: "Tape",
          navDaily: "Brief",
          navPortfolio: "Portfolio",
          navReleaseWatch: "Release",
          navWeights: "Weights",
          navRubric: "Rubric",
          navPlaybook: "Playbook",
          navScenarios: "Scenarios",
          navRunSheet: "Run Sheet",
          navLookbook: "Lookbook",
          navScorecard: "Scorecard",
          navGuardrails: "Guardrails",
          navCoreWatch: "Watch",
          navIdentity: "Identity",
          navTrajectory: "Trajectory",
          navFormula: "Formula",
          navMatrix: "Matrix",
          navPremium: "Premium",
          navPricing: "Pricing",
          navEvidence: "Evidence",
          navSampling: "Sampling",
          navSources: "Sources",
          themeSweet: "Sweet",
          themeClassic: "Classic",
          themeGothic: "Gothic",
          themeChanged: "theme changed",
          checkAll: "Check All",
          refresh: "Refresh",
          sourcesHeading: "Watch Sources",
          recentEvents: "Release Feed",
          trackedItemsHeading: "Radar Items",
          northStarRadar: "North Star Radar",
          northStarHint: "Judge today's radar maturity from brand weights, evidence coverage, release heat, and run-sheet pressure",
          northStarScore: "north-star score",
          northStarLead: "today's lead",
          northStarMaturity: "radar maturity",
          northStarWeightedCoverage: "weighted evidence",
          northStarReleaseHeat: "release heat",
          northStarRunSheetHeat: "run-sheet pressure",
          northStarPremiumHeat: "premium-backed",
          northStarEvidenceLane: "evidence base",
          northStarReleaseLane: "release window",
          northStarPremiumLane: "premium check",
          northStarExecutionLane: "execution pressure",
          northStarEvidenceDetail: "High-weight brands need at least two price samples",
          northStarReleaseDetail: "Release, preorder, and restock items matched brand weights",
          northStarPremiumDetail: "Whether resale samples support active tracking",
          northStarExecutionDetail: "Average pressure across today's run sheet",
          brandCrownQueue: "Brand Crown Queue",
          brandCrownHint: "Rank core brands, shell-style terms, resale premium, and release hits into today's focus",
          crownScore: "crown priority",
          crownLead: "lead brand",
          crownCoreReady: "core ready",
          crownKeywordTotal: "style terms",
          crownReleaseSignals: "release hits",
          crownPremiumBacked: "premium-backed",
          crownAction: "today's action",
          crownActionAnchor: "add price anchor",
          crownActionRelease: "track release window",
          crownActionPremium: "check resale premium",
          crownActionHold: "hold cruise",
          crownSample: "add sample",
          crownKeywordSample: "add term sample",
          crownOpenRelease: "view release",
          crownNoRows: "No crown brands yet",
          crownConfidence: "confidence",
          crownConfidenceHigh: "evidence ready",
          crownConfidenceMedium: "watchable",
          crownConfidenceLow: "needs evidence",
          exportCrownCsv: "export crown CSV",
          exportedCrownCsv: "brand crown queue exported",
          noCrownCsv: "no crown queue to export",
          draftRiskRadar: "Weight Draft Risk",
          draftRiskHint: "Bring unsaved weight-change risks to the first screen before saving",
          draftRiskScore: "draft risk score",
          draftRiskClean: "no unsaved weight changes",
          draftRiskCleanHint: "Current brand weights match the saved configuration",
          draftRiskNoOpen: "no open risk",
          draftRiskNoOpenHint: "The draft has changes, but no save-blocking warnings",
          draftRiskReview: "review weights",
          draftRiskChanged: "changed",
          draftRiskOpen: "risks",
          draftRiskMaxMove: "largest move",
          stylePremiumTape: "Style Premium Tape",
          stylePremiumTapeHint: "Read resale premium and evidence by Sweet, Classic, Gothic, and other style lines",
          stylePremiumLead: "lead tape",
          stylePremiumHeat: "tape heat",
          stylePremiumAvg: "avg premium",
          stylePremiumWeighted: "weighted heat",
          stylePremiumSamples: "samples",
          stylePremiumSpread: "avg spread",
          stylePremiumAction: "today action",
          stylePremiumEvidence: "evidence",
          stylePremiumPremiumSignals: "premium brands",
          stylePremiumCollect: "collect prices",
          stylePremiumTrack: "track premium",
          stylePremiumWatch: "keep watching",
          stylePremiumReview: "review discount",
          stylePremiumHold: "wait signal",
          dailyRadarBrief: "Daily Radar Brief",
          dailyRadarBriefHint: "Turn core watch, weight tuning, and sample gaps into today's action queue",
          dailyLead: "lead",
          dailyActions: "actions",
          dailyActionLanes: "action lanes",
          dailySampleGaps: "sample gaps",
          dailyAvgPriority: "avg priority",
          dailyJump: "view",
          dailySample: "add sample",
          dailyKeyword: "add pattern",
          dailyLaneAll: "all actions",
          dailyNoActions: "No daily actions yet",
          dailyNoFilteredActions: "No actions in this lane",
          dailyKindCore: "core watch",
          dailyKindScorecard: "weight score",
          dailyKindSampling: "sampling plan",
          exportDailyCsv: "export brief CSV",
          exportedDailyCsv: "daily radar brief exported",
          noDailyCsv: "no daily brief to export",
          resaleRunSheet: "Resale Run Sheet",
          resaleRunSheetHint: "Merge daily actions, search tasks, and price-anchor gaps into one execution sheet",
          runSheetTasks: "tasks",
          runSheetAnchorGaps: "anchor gaps",
          runSheetSearches: "search tasks",
          runSheetSamples: "samples",
          runSheetDaily: "daily action",
          runSheetRelease: "release watch",
          runSheetMarket: "market search",
          runSheetPrice: "price anchor",
          runSheetGo: "open",
          runSheetSample: "add sample",
          runSheetNoRows: "No run-sheet tasks yet",
          exportRunSheetCsv: "export run sheet CSV",
          exportedRunSheetCsv: "run sheet exported",
          noRunSheetCsv: "no run-sheet tasks to export",
          brandPortfolio: "Brand Portfolio Overview",
          brandPortfolioHint: "Summarize evidence coverage, core gaps, premium heat, and weight drift",
          portfolioHealth: "portfolio health",
          portfolioCoverage: "evidence coverage",
          portfolioCoreGaps: "core gaps",
          portfolioHeat: "premium heat",
          portfolioDrift: "weight drift",
          portfolioActions: "open actions",
          portfolioEvidenceLane: "evidence coverage",
          portfolioCoreLane: "core gaps",
          portfolioPremiumLane: "premium heat",
          portfolioDriftLane: "weight drift",
          portfolioEvidenceHint: "Thin samples make premium and weight calls unstable",
          portfolioCoreHint: "When core brands lack anchors, collect paired retail/resale prices first",
          portfolioPremiumHint: "Hot-premium brands need continued checks and term confirmation",
          portfolioDriftHint: "When target and current weights diverge, audit before saving",
          portfolioReview: "view",
          portfolioSample: "add sample",
          exportPortfolioCsv: "export portfolio CSV",
          exportedPortfolioCsv: "brand portfolio overview exported",
          noPortfolioCsv: "no portfolio overview to export",
          releaseWatchQueue: "Release Watch Queue",
          releaseWatchHint: "Connect release, preorder, and restock items to brand weights and resale premium evidence",
          releaseWatchScore: "release score",
          releaseWatchSignals: "release signals",
          releaseWatchBrands: "matched brands",
          releaseWatchTopScore: "top score",
          releaseWatchPremium: "premium-backed",
          releaseWatchNoRows: "No release items matched brand weights yet",
          releaseWatchMatched: "matched terms",
          releaseWatchSource: "source",
          releaseWatchOpen: "open source",
          releaseWatchSample: "add price sample",
          exportReleaseWatchCsv: "export release CSV",
          exportedReleaseWatchCsv: "release watch queue exported",
          noReleaseWatchCsv: "no release watch rows to export",
          releaseActionSample: "collect price anchor",
          releaseActionTrackPremium: "track resale premium",
          releaseActionWatchDrop: "watch release window",
          releaseActionReview: "review release signal",
          marketSignal: "Premium Signal",
          brandWeights: "Brand Weights",
          saveWeights: "Save Weights",
          resetWeights: "Reset",
          exportWeightsCsv: "export weights CSV",
          exportedWeightsCsv: "brand weights exported",
          noWeightsCsv: "no brand weights to export",
          weightsClean: "saved",
          weightsDirty: "unsaved",
          weightsRisk: "risks",
          scenarioRelease: "Release first",
          scenarioPremium: "Premium first",
          scenarioEvidence: "Evidence first",
          scenarioApplied: "weight scenario draft applied",
          exportScenariosCsv: "export scenarios CSV",
          exportedScenariosCsv: "weight scenarios exported",
          noScenariosCsv: "no weight scenarios to export",
          weightScenarioCompare: "Brand Weight Scenario Compare",
          weightScenarioCompareHint: "Preview release, premium, and evidence-first drafts before saving",
          scenarioAvgTarget: "avg target",
          scenarioChanged: "changed",
          scenarioRaised: "raised",
          scenarioLowered: "lowered",
          scenarioApplyDraft: "apply scenario",
          scenarioTopMoves: "top moves",
          scenarioNoMoves: "no changes",
          weightDraftAudit: "draft audit",
          weightDraftClean: "no unsaved weight changes",
          weightDraftChanged: "changed",
          weightDraftSaved: "saved",
          weightDraftCurrent: "draft",
          weightDraftDelta: "delta",
          weightDraftAvgDelta: "avg delta",
          weightDraftRaised: "raised",
          weightDraftLowered: "lowered",
          weightDraftMaxMove: "largest move",
          weightDraftRiskCoreDown: "core lowered",
          weightDraftRiskThinRaise: "thin evidence raise",
          weightDraftRiskLargeMove: "large move",
          weightDraftRiskArchiveJump: "archive promoted",
          weightDraftRiskCoreDownHint: "core brand is lowered; review release and premium evidence before saving",
          weightDraftRiskThinRaiseHint: "weight rises with thin samples; collect retail and resale evidence first",
          weightDraftRiskLargeMoveHint: "large shift is better treated as a scenario draft first",
          weightDraftRiskArchiveJumpHint: "archive brand moves into watch tier; confirm premium or release signals",
          brandWeightRubric: "Brand Weight Rubric",
          brandWeightRubricHint: "Split the 0-100 weight scale into core, priority, sampling, and archive lanes",
          rubricRange: "weight range",
          rubricAvgWeight: "avg weight",
          rubricAvgPremium: "avg premium",
          rubricSampleGaps: "sample gaps",
          rubricBrands: "brands",
          rubricCore: "core release",
          rubricCoreHint: "Prioritize AP, BABY, AATP-style releases, preorders, restocks, and resale anchors",
          rubricLead: "priority watch",
          rubricLeadHint: "Promote when premium or release evidence strengthens; check terms and sample depth first",
          rubricSeed: "sample seed",
          rubricSeedHint: "Collect paired retail/resale samples before deciding whether the brand deserves more weight",
          rubricArchive: "archive low-frequency",
          rubricArchiveHint: "Watch lightly unless strong premium or clear release signals justify a raise",
          rubricNoBrands: "No brands",
          rubricReviewWeights: "view weights",
          rubricSampleGap: "sample first gap",
          brandPlaybook: "Brand Playbook",
          brandPlaybookHint: "Combine weight, evidence, pattern terms, and next action into per-brand execution cards",
          playbookAction: "next action",
          playbookPrimaryTerm: "primary term",
          playbookTarget: "target weight",
          playbookSample: "add sample",
          playbookKeyword: "add pattern",
          playbookApply: "apply target",
          playbookNoRows: "No brand playbook yet",
          playbookActionAnchor: "add price anchor first",
          playbookActionPair: "add second sample",
          playbookActionTrack: "track premium spread",
          playbookActionRaise: "prepare weight raise",
          playbookActionCool: "cooldown review",
          playbookActionHold: "hold watch",
          playbookReasonCore: "core high weight",
          playbookReasonThin: "thin evidence",
          playbookReasonPremium: "premium supported",
          playbookReasonDiscount: "discount review",
          playbookReasonTarget: "target shift",
          playbookReasonKeyword: "clear pattern terms",
          draftPreview: "draft preview",
          scoreDelta: "delta",
          weightsReset: "brand weights reset",
          weightsSaved: "brand weights saved",
          styleFamilySweet: "Sweet prints",
          styleFamilyClassic: "Classic archive",
          styleFamilyGothic: "Gothic line",
          styleFamilyRelease: "Release watch",
          styleFamilyArt: "Art prints",
          styleBrands: "brands",
          styleAvgWeight: "avg weight",
          styleLeader: "lead",
          styleWeightTotal: "total weight",
          styleCoreShare: "core",
          styleKeywords: "style terms",
          styleNoKeywords: "no terms yet",
          brandWeightSalon: "Brand Weight Salon",
          brandWeightSalonHint: "Review draft weights, formula targets, and evidence gaps by Lolita style line",
          salonLead: "lead style",
          salonAvgDraft: "draft avg",
          salonTargetAvg: "formula avg",
          salonEvidenceGap: "evidence gaps",
          salonCoreShare: "core share",
          salonPremiumSignals: "premium signals",
          salonMove: "move",
          salonActionCollect: "collect evidence",
          salonActionLift: "lift line",
          salonActionTrim: "cool line",
          salonActionHold: "hold watch",
          brandLookbook: "Brand Weight Lookbook",
          brandLookbookHint: "Explain weights, samples, and next watch moves through Lolita style cues",
          lookbookLead: "lead",
          lookbookCore: "core",
          lookbookGaps: "sample gaps",
          lookbookAvgFit: "avg fit",
          lookbookFit: "fit",
          lookbookSample: "add sample",
          lookbookKeyword: "add pattern",
          lookbookActionAnchor: "add price anchor",
          lookbookActionTrack: "track premium",
          lookbookActionReview: "review discount",
          lookbookActionWatch: "keep watching",
          lookbookNoRows: "No brand lookbook yet",
          brandWeightScorecard: "Brand Weight Scorecard",
          brandWeightScorecardHint: "Split baseline, premium, evidence, pattern terms, and watch links into auditable parts",
          scorecardTop: "top target",
          scorecardAligned: "aligned",
          scorecardCollect: "needs evidence",
          scorecardAvgConfidence: "avg confidence",
          scorecardCurrent: "current",
          scorecardTarget: "target",
          scorecardVerdict: "verdict",
          scorecardNoRows: "No weight scorecards yet",
          exportScorecardsCsv: "export scorecards CSV",
          exportedScorecardsCsv: "weight scorecards exported",
          noScorecardsCsv: "no weight scorecards to export",
          brandWeightGuardrails: "Brand Weight Guardrails",
          brandWeightGuardrailsHint: "Flag mismatches between weight, premium, and sample evidence",
          guardrailRiskScore: "guardrail risk",
          guardrailOpen: "to review",
          guardrailAvgConfidence: "avg confidence",
          guardrailCoverage: "evidence coverage",
          guardrailCritical: "critical",
          guardrailWatch: "watch",
          guardrailStable: "stable",
          guardrailTarget: "guardrail target",
          guardrailNoRows: "Weights, premium, and sample evidence are currently aligned",
          guardrailCoreGap: "core anchor gap",
          guardrailUnderweighted: "premium underweighted",
          guardrailOverweighted: "discount overweighted",
          guardrailArchiveHot: "archive heating",
          guardrailReasonCoreGap: "high-weight brand lacks paired retail/resale evidence",
          guardrailReasonUnderweighted: "premium is sample-supported but the weight has not caught up",
          guardrailReasonOverweighted: "high-weight brand shows discounted resale averages",
          guardrailReasonArchiveHot: "low-weight brand has positive premium and should enter the watch pool",
          guardrailActionSample: "add sample",
          guardrailActionApply: "apply target",
          exportGuardrailsCsv: "export guardrails CSV",
          exportedGuardrailsCsv: "weight guardrails exported",
          noGuardrailsCsv: "no weight guardrails to export",
          brandRadarMatrix: "Brand Radar Matrix",
          matrixHint: "Weight, premium, samples, and action in one view",
          matrixBrand: "brand",
          matrixScore: "score",
          matrixWeight: "weight",
          matrixPremium: "avg premium",
          matrixSamples: "samples",
          matrixAction: "action",
          matrixFilterAll: "All",
          matrixFilterFocus: "Focus brands",
          matrixFilterLead: "Lead",
          matrixFilterNeedsSamples: "Needs samples",
          matrixFilterCore: "Core",
          matrixSortLabel: "sort",
          matrixSortScore: "radar score",
          matrixSortPremium: "avg premium",
          matrixSortWeight: "brand weight",
          matrixSortSamples: "samples",
          matrixSortDelta: "draft delta",
          opportunityRadar: "Opportunity Radar",
          opportunityHint: "Attention suggestions from brand weight and resale premium",
          filterAll: "All",
          filterLead: "Lead",
          filterWatch: "Watch",
          filterSamples: "Samples",
          filterCooldown: "Cooldown",
          focusQueue: "Focus Queue",
          marketAlertLine: "Radar Alert Line",
          marketAlertHint: "Combine premium spikes, brand heat, and core-brand sample gaps",
          alertTotal: "alerts",
          alertCritical: "critical",
          alertWatch: "watch",
          alertSampleGap: "sample gaps",
          alertScore: "alert score",
          noAlerts: "No alerts yet",
          marketMomentum: "Resale Momentum",
          marketMomentumHint: "Compare each brand's latest sample with its previous average",
          momentumTotal: "momentum brands",
          momentumRisingCount: "rising",
          momentumCoolingCount: "cooling",
          momentumSteadyCount: "steady",
          momentumLatest: "latest",
          momentumPrevious: "previous avg",
          momentumDelta: "delta",
          momentumRising: "rising",
          momentumCooling: "cooling",
          momentumSteady: "steady",
          noMomentum: "Add at least 2 samples for one brand to show momentum",
          marketPremium: "Resale Premium Watch",
          premiumByBrand: "Premium by Brand",
          premiumRecords: "High-Premium Samples",
          premiumBrandFilter: "brand",
          premiumBrandAll: "all brands",
          exportPremiumCsv: "export CSV",
          exportedPremiumCsv: "filtered samples exported",
          noPremiumCsv: "no samples to export",
          premiumFilterAll: "All",
          premiumBandCollector: "Collector",
          premiumBandHot: "Hot",
          premiumBandPremium: "Premium",
          premiumBandNearRetail: "Near retail",
          premiumBandDiscount: "Discount",
          priceCorridor: "Price corridor",
          retailRange: "retail",
          resaleRange: "resale",
          avgSpread: "avg spread",
          metricSources: "Sources",
          metricTrackedItems: "Tracked Items",
          metricEvents: "Events",
          metricLatestEvent: "Latest Event",
          metricLatestSource: "Latest Source",
          signalSummary: "Estimated attention heat from recent event statuses",
          noStatus: "No status data yet",
          tierCore: "core",
          tierWatch: "watch",
          tierArchive: "archive",
          weightLabel: "weight",
          weightBand: "band",
          weightIntent: "intent",
          keywordCount: "keywords",
          visualMotif: "visual",
          weightBandCore: "core release",
          weightBandWatch: "watch priority",
          weightBandArchive: "archive sampling",
          weightIntentCore: "prioritize release, preorder, and Taobao alerts",
          weightIntentWatch: "promote when premium and samples support it",
          weightIntentArchive: "collect resale samples before raising weight",
          weightSnapshot: "Weight Profile",
          weightSnapshotHint: "Calibrate brand tiers, price evidence, and sample gaps together",
          brandWeightProfile: "Brand Weight Composition",
          brandWeightProfileHint: "Explain how each brand weight connects premium, evidence, and next action",
          brandIdentityMatrix: "Brand Identity Matrix",
          brandIdentityHint: "Calibrate palette, motif, premium evidence, and attention cues together",
          identityCoverage: "identity coverage",
          identityCoreCount: "core identities",
          identityWatchCount: "watch identities",
          identityArchiveCount: "archive identities",
          identityPalette: "palette",
          identityEvidence: "evidence",
          identityPremium: "premium",
          weightAverage: "average weight",
          weightCoreAverage: "core average",
          weightEvidenceCoverage: "evidence coverage",
          weightNeedsEvidence: "needs evidence",
          weightDistribution: "weight distribution",
          weightCommandDeck: "Brand Weight Command",
          weightCommandHint: "Prioritize where high weight, thin samples, and premium heat intersect",
          weightRadarMap: "brand weight radar",
          weightRadarCore: "core",
          weightRadarWatch: "watch",
          weightRadarArchive: "archive",
          profileWeight: "weight",
          profileHeat: "heat",
          profileEvidence: "evidence",
          profileKeywords: "pattern terms",
          profileNoKeywords: "no pattern terms",
          noBrandProfile: "No brand weight profile yet",
          weightCoreCount: "core tier",
          weightWatchCount: "watch tier",
          weightArchiveCount: "archive tier",
          weightTopGap: "sample next",
          weightNoGap: "sample gaps cleared",
          brandWeightStrategy: "Brand Weight Strategy",
          brandWeightStrategyHint: "Turn tiers, premium evidence, and draft changes into tuning moves",
          weightTrajectory: "Weight Trajectory",
          weightTrajectoryHint: "Connect current weight, target, and evidence confidence into an executable path",
          trajectoryChanged: "to tune",
          trajectoryStable: "aligned",
          trajectoryAvgTarget: "avg target",
          trajectoryAvgShift: "avg shift",
          trajectoryCurrent: "current",
          trajectoryTarget: "target",
          trajectoryApply: "apply target",
          trajectorySample: "add sample",
          trajectoryRaise: "raise path",
          trajectoryLower: "lower path",
          trajectoryCollect: "collect evidence first",
          trajectoryAligned: "weight aligned",
          trajectoryNoRows: "No weight trajectory yet",
          brandWeightFormula: "Brand Weight Formula",
          brandWeightFormulaHint: "Break down baseline, premium, evidence, and watch links into auditable target weights",
          formulaBase: "base",
          formulaPremium: "premium",
          formulaEvidence: "evidence",
          formulaKeywords: "terms",
          formulaWatchability: "watch links",
          formulaTarget: "target",
          formulaConfidence: "confidence",
          formulaSummary: "Formula Contribution Overview",
          formulaSummaryHint: "Split the current brand pool target weights into base, premium, evidence, terms, and watch-link inputs",
          formulaAvgTarget: "avg target",
          formulaAvgConfidence: "avg confidence",
          formulaCollectCount: "needs evidence",
          formulaLeadMove: "largest drift",
          formulaApplyDraft: "apply target",
          formulaAligned: "aligned",
          formulaRaise: "raise",
          formulaLower: "lower",
          formulaNoRows: "No weight formulas yet",
          formulaDraftApplied: "formula target applied",
          strategyHeat: "strategy heat",
          strategyActionable: "open moves",
          strategyCoverage: "evidence coverage",
          strategyAvgWeight: "average weight",
          strategyCoreLane: "core gate",
          strategyWatchLane: "watch pool",
          strategyArchiveLane: "archive pool",
          strategyNextMoves: "next tuning moves",
          strategyCollect: "collect evidence",
          strategyRaise: "raise suggested",
          strategyCooldown: "cooldown review",
          strategyHold: "hold weight",
          strategyBaseline: "low-frequency watch",
          strategyMonitor: "keep watching",
          strategyReasonCoreGap: "high weight with thin price evidence",
          strategyReasonPremiumRaise: "premium is supported by samples",
          strategyReasonDiscountCool: "discounted resale asks for review",
          strategyReasonHoldCore: "premium and samples support this weight",
          strategyReasonArchiveGap: "low weight and thin evidence",
          strategyReasonMonitor: "weight, premium, and samples are balanced",
          strategyTarget: "target",
          strategyNoMoves: "No tuning moves yet",
          brandKeywordRadar: "Hot Pattern Keywords",
          brandKeywordHint: "Connect item-level signals such as AP shell to price-sample entry",
          coreMarketWatch: "Core Brand Watch Desk",
          coreMarketWatchHint: "Group high-weight brands, signature terms, market search, and sampling actions in one view",
          coreWatchBrands: "watch brands",
          coreWatchThin: "thin samples",
          coreWatchAvgScore: "avg score",
          coreWatchTerms: "pattern cues",
          coreWatchSearch: "search",
          coreWatchSample: "add sample",
          coreWatchCue: "watch cue",
          coreWatchReasonCore: "core high weight",
          coreWatchReasonThin: "collect resale samples",
          coreWatchReasonStrongPremium: "strong premium evidence",
          coreWatchReasonPositivePremium: "positive premium signal",
          coreWatchReasonDiscount: "discount review",
          coreWatchReasonKeywordRich: "rich pattern terms",
          coreWatchReasonWatch: "watch-tier tracking",
          coreWatchPriceAnchor: "price anchor",
          coreWatchRetailAnchor: "avg retail",
          coreWatchResaleAnchor: "avg resale",
          coreWatchSpreadAnchor: "avg spread",
          coreWatchPriceMissing: "price anchor needed",
          coreWatchAnchorGaps: "anchor gaps",
          coreWatchAnchorReady: "anchors ready",
          coreWatchPriceStatusReady: "price anchor ready",
          coreWatchPriceStatusMissing: "price anchor needed",
          coreWatchActionAnchor: "add price anchor",
          coreWatchActionPair: "add second sample",
          coreWatchActionTrack: "track spread",
          coreWatchActionReview: "review discount",
          coreWatchActionHold: "hold watch",
          exportCoreWatchCsv: "export watch CSV",
          exportedCoreWatchCsv: "core watch checklist exported",
          noCoreWatchCsv: "no core watch checklist to export",
          noCoreWatch: "No core watch brands yet",
          premiumSeedRadar: "Premium Watch Seeds",
          premiumSeedHint: "Before samples are thick enough, queue high-weight brands and signature terms for price collection",
          premiumSeedTerms: "premium seed terms",
          premiumSeedEmpty: "No premium seeds yet",
          premiumSeedIntentCoreGap: "core brand lacks resale evidence; collect samples first",
          premiumSeedIntentPremium: "positive premium signal exists; keep tracking signature pieces",
          premiumSeedIntentSeed: "build the retail/resale sample baseline first",
          premiumSeedIntentWatch: "expand samples and watch price direction",
          exportPremiumSeedsCsv: "export seed CSV",
          exportedPremiumSeedsCsv: "premium seeds exported",
          noPremiumSeedsCsv: "no premium seeds to export",
          premiumSeedTaskCount: "seed tasks",
          premiumSeedCoreGaps: "core gaps",
          premiumSeedTopSeed: "top seed",
          premiumSeedAvgScore: "avg seed score",
          premiumSeedStageSeed: "seed sample",
          premiumSeedStagePair: "add second",
          premiumSeedStageExpand: "expand samples",
          premiumSeedStageWatch: "keep watching",
          marketKeywords: "market terms",
          noMarketKeywords: "No hot pattern keywords yet",
          keywordSampleReady: "keyword filled for price sample",
          patternPremiumRadar: "Pattern Premium Radar",
          patternPremiumHint: "Connect hot pattern terms to recorded resale-price samples",
          noPatternPremium: "No pattern premium radar data yet",
          patternSample: "sample this pattern",
          marketActionDesk: "Market Action Desk",
          marketActionHint: "Turn high-weight pattern terms into search and sample tasks",
          actionTotal: "pattern tasks",
          actionNeedsSamples: "need samples",
          actionWithSamples: "sampled",
          actionSearch: "search",
          actionQuery: "query",
          actionGoofish: "Goofish",
          actionTaobao: "Taobao",
          actionMercari: "Mercari",
          actionYahoo: "Yahoo JP",
          exportMarketActionsCsv: "export actions CSV",
          exportedMarketActionsCsv: "market action checklist exported",
          noMarketActionsCsv: "no market actions to export",
          priceDiscipline: "Price Discipline",
          priceDisciplineHint: "Turn brand weights into chase ceilings and flag overheated resale averages",
          priceDisciplineCeiling: "chase ceiling",
          priceDisciplineObserved: "avg resale",
          priceDisciplineGap: "price room",
          priceDisciplineRows: "price lines",
          priceDisciplineRoom: "room to chase",
          priceDisciplineNear: "near ceiling",
          priceDisciplineHot: "overheated",
          priceDisciplineSample: "anchor first",
          priceDisciplineMissing: "anchor needed",
          priceDisciplineNoRows: "Not enough price anchors for discipline lines yet",
          priceDisciplineSampleAction: "add price sample",
          evidenceHealth: "Evidence Health",
          evidenceHealthHint: "Check whether samples have source, link, date, and notes",
          qualityScore: "quality score",
          qualityLinked: "linked",
          qualitySourced: "sourced",
          qualityDated: "dated",
          qualityNoted: "noted",
          qualityWeak: "needs work",
          weightTuning: "Weight Tuning",
          weightTuningHint: "Turn premium, sample count, and current weight into next actions",
          noWeightTuning: "No tuning suggestions yet",
          tuningTarget: "target weight",
          tuningReason: "reason",
          tuningCollect: "collect samples",
          tuningRaise: "consider raising",
          tuningHold: "hold",
          tuningCool: "cooldown review",
          tuningBaseline: "low-frequency sampling",
          tuningCollectReason: "high weight but thin samples; collect retail and resale prices first",
          tuningRaiseReason: "premium is sample-supported, so the weight can move toward core",
          tuningHoldReason: "weight, premium, and sample count are currently aligned",
          tuningCoolReason: "discounted resale or weak heat; review before saving this weight",
          tuningBaselineReason: "low weight and thin samples; keep low-frequency watch",
          tuningAddSample: "add sample",
          tuningApplyDraft: "apply draft",
          tuningApplyAll: "apply all drafts",
          tuningBatchReady: "ready to apply",
          tuningBatchEmpty: "nothing to apply",
          tuningBatchApplied: "draft weights applied",
          tuningDraftApplied: "draft weight applied",
          tuningSampleReady: "brand selected for price sample",
          sampleCoverage: "Sample Coverage",
          sampleCoverageHint: "Show how much price evidence sits behind radar scores",
          samplePlan: "Sample Collection Plan",
          samplePlanHint: "Queue the next resale-price samples by brand weight and evidence gaps",
          samplePlanTarget: "target",
          samplePlanMissing: "missing",
          samplePlanProgress: "sample progress",
          samplePlanNoRows: "No brands need sampling right now",
          samplePlanSeed: "seed first sample",
          samplePlanPair: "add paired sample",
          samplePlanRoundout: "round out target",
          samplePlanComplete: "review",
          samplePlanCritical: "core gap",
          samplePlanWatch: "watch gap",
          samplePlanBackfill: "archive backfill",
          samplePlanDone: "covered",
          samplePlanSampleReady: "sampling brand selected",
          exportSamplePlanCsv: "export sampling CSV",
          exportedSamplePlanCsv: "sample plan exported",
          noSamplePlanCsv: "no sample plan to export",
          samplePlanCompletion: "completion",
          samplePlanOpenBrands: "open brands",
          samplePlanCoreGaps: "core gaps",
          samplePlanTotalMissing: "missing total",
          samplePlanAvgPriority: "avg priority",
          coverageReady: "ready",
          coverageThin: "thin",
          coverageMissing: "missing",
          coverageProgress: "coverage",
          coveragePriority: "sample next",
          coverageGoal: "2 samples to start, 5 samples for steadier signals",
          radarScore: "radar score",
          observed: "observed",
          noFocusQueue: "No focus queue yet",
          noMarket: "No price samples yet",
          noOpportunity: "No opportunity radar data yet",
          samples: "samples",
          avgPremium: "avg",
          maxPremium: "max",
          priorityScore: "weighted score",
          premiumPoints: "premium",
          brandPoints: "brand",
          samplePoints: "samples",
          retailPrice: "retail",
          resalePrice: "resale",
          brandAlias: "brand",
          itemName: "item",
          currency: "currency",
          condition: "condition",
          sourceName: "source",
          observedAt: "date",
          sampleUrl: "link",
          sampleNotes: "notes",
          evidence: "evidence",
          noEvidence: "No matching evidence yet",
          addSample: "Add Sample",
          sampleAdded: "price sample added",
          samplePreview: "Sample Preview",
          samplePreviewEmpty: "Enter retail and resale prices to preview premium",
          sampleTaskAnchorTitle: "Price anchor task",
          sampleTaskAnchorHint: "Enter retail, resale, and source before saving the sample",
          coreWatchTaskSource: "core-watch",
          coreWatchTaskNotePrefix: "core watch",
          sampleSpread: "spread",
          sampleScore: "sample score",
          sampleSignalStrong: "strong premium sample",
          sampleSignalPositive: "positive premium sample",
          sampleSignalDiscount: "discounted sample",
          sampleSignalNeutral: "near retail",
          premiumBand: {
            collector: "collector",
            hot: "hot premium",
            premium: "premium",
            near_retail: "near retail",
            discount: "discount",
          },
          alertKind: {
            sample_spike: "sample spike",
            brand_heat: "brand heat",
            sample_gap: "sample gap",
          },
          alertSeverity: {
            critical: "critical",
            watch: "watch",
            sample_gap: "sample",
          },
          alertReason: {
            weak_evidence_spike: "strong premium with weak evidence",
            collector_premium: "collector-level premium sample",
            hot_premium: "hot premium sample",
            weighted_spike: "triggered after brand weighting",
            premium_watch: "premium watch",
            brand_hot_average: "brand average is heating up",
            core_needs_samples: "core brand needs price samples",
          },
          opportunityBand: {
            lead: "track releases",
            watch: "watch",
            collect_samples: "collect samples",
            cooldown: "cooldown",
          },
          weightRole: {
            release_priority: "release/preorder priority",
            premium_watch: "premium-watch priority",
            evidence_sampling: "low-frequency sampling",
          },
          evidenceLevel: {
            ready: "evidence ready",
            thin: "thin evidence",
            missing: "missing samples",
          },
          reasonCode: {
            core_brand: "core brand",
            watch_brand: "watch brand",
            needs_samples: "needs samples",
            sample_supported: "sample supported",
            strong_premium: "strong premium",
            positive_premium: "positive premium",
            discounted_resale: "discounted resale",
            baseline: "baseline weight",
          },
          noSources: "No sources configured.",
          noEvents: "No events yet. Run a check to build the baseline.",
          noItems: "No tracked items yet.",
          noKeywords: "no keywords",
          enabled: "enabled",
          disabled: "disabled",
          checkSource: "Check Source",
          disabledButton: "Disabled",
          allSources: "all sources",
          checked: "checked",
          refreshed: "refreshed",
          undated: "undated",
          seen: "last seen",
          eventType: {
            new_item: "new item",
            update: "update",
          },
          status: {
            new_arrival: "new arrival",
            preorder: "preorder",
            restock: "restock",
            shop_news: "shop news",
          },
          sourceType: {
            metamorphose: "Metamorphose news",
            generic_page: "Generic page",
          },
          brandStyle: {
            "sweet print": "sweet print",
            "classic sweet": "classic sweet",
            "gothic prince": "gothic prince",
            "release/restock": "release/restock",
            gothic: "gothic",
            classic: "classic",
            "art print": "art print",
          },
        },
      };
      let currentState = null;
      let currentLanguage = localStorage.getItem("radarLanguage") || "zh";
      let currentTheme = localStorage.getItem("radarTheme") || "classic";
      let activeOpportunityFilter = "all";
      let activeMatrixFilter = "all";
      let activeMatrixSort = "score";
      let activePremiumFilter = "all";
      let activePremiumBrandFilter = "all";
      let activeDailyLane = "all";
      let previewingDraftWeights = false;
      if (!translations[currentLanguage]) currentLanguage = "zh";
      if (!["sweet", "classic", "gothic"].includes(currentTheme)) currentTheme = "classic";

      async function api(path, options = {}) {
        const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
        const payload = await response.json();
        if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
        return payload;
      }

      async function loadState() {
        currentState = await api("/api/state");
        render(currentState);
      }

      function render(state) {
        $("paths").textContent = `${state.config_path} · ${state.db_path}`;
        const counts = state.counts || {};
        $("metrics").innerHTML = [
          [t("metricSources"), `${counts.enabled_sources || 0}/${counts.sources || 0}`],
          [t("metricTrackedItems"), counts.items || 0],
          [t("metricEvents"), counts.events || 0],
          [t("metricLatestEvent"), valueLabel("eventType", state.events?.[0]?.event_type) || "-"],
          [t("metricLatestSource"), state.events?.[0]?.source || "-"],
        ].map(([label, value]) => `<article class="metric"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></article>`).join("");
        renderMarketForm(state.brand_weights || []);
        renderSamplePreview();
        renderBrandWeights(state.brand_weights || []);
        renderStyleCompass();
        renderBrandKeywordRadar(state.brand_weights || []);
        renderFocusQueue(state.focus_queue || []);
        renderMarketAlertLine(state.market_alerts || {});
        renderMarketMomentum(state.market?.momentum || []);
        renderBrandRadarViews();
        renderOpportunityRadar(state.opportunity_radar || []);
        renderMarketSignal(state.events || [], state.items || []);
        renderMarketPremium(state.market || {});
        renderMarketActionDesk(state.market?.patterns || []);
        renderEvidenceHealth(state.market?.summary?.quality || {});
        renderPatternPremiumRadar(state.market?.patterns || []);
        $("sources").innerHTML = state.sources.length ? state.sources.map(renderSource).join("") : `<div class="row">${escapeHtml(t("noSources"))}</div>`;
        $("eventCount").textContent = shownText(state.events.length);
        $("events").innerHTML = state.events.length ? state.events.map(renderEvent).join("") : `<div class="row">${escapeHtml(t("noEvents"))}</div>`;
        $("itemCount").textContent = shownText(state.items.length);
        $("items").innerHTML = state.items.length ? state.items.map(renderItem).join("") : `<div class="row">${escapeHtml(t("noItems"))}</div>`;
      }

      function renderBrandWeights(weights) {
        $("brandWeights").innerHTML = weights.map((brand) => `<article class="brand-chip ${brandThemeClass(brand)}" data-tier="${escapeHtml(brand.tier || "")}" style="${escapeHtml(brandVisualStyle(brand))}">
          <header class="brand-chip-header">
            <div class="brand-cameo" aria-hidden="true">
              <strong>${escapeHtml(brand.alias)}</strong>
              <span>${escapeHtml(brand.weight)}</span>
            </div>
            <div class="brand-title">
              <strong>${escapeHtml(brand.name)}</strong>
              <span>${escapeHtml(brand.alias)}</span>
            </div>
          </header>
          <div class="brand-ribbon">
            <span>${escapeHtml(tierLabel(brand.tier))}</span>
            <span>${escapeHtml(styleLabel(brand.style))}</span>
            ${brand.visual?.palette ? `<span>${escapeHtml(brand.visual.palette)}</span>` : ""}
          </div>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${Number(brand.weight) || 0}%"></span></div>
          ${brandIdentityHtml(brand)}
          <label class="weight-control">
            <span data-weight-label>${escapeHtml(t("weightLabel"))} ${escapeHtml(brand.weight)}</span>
            <input type="range" min="0" max="100" step="1" value="${escapeHtml(brand.weight)}" data-original-weight="${escapeHtml(brand.weight)}" data-brand-weight="${escapeHtml(brand.alias)}">
          </label>
          <div class="brand-keywords" aria-label="${escapeHtml(t("marketKeywords"))}">
            ${brandKeywordPearlsHtml(brand)}
          </div>
          <div class="weight-insight" data-weight-insight="${escapeHtml(brand.alias)}">
            ${brandWeightInsightHtml(brand, brand.weight)}
          </div>
        </article>`).join("");
        updateWeightDirtyState();
      }

      function renderDailyRadarBrief(rows) {
        const target = $("dailyRadarBrief");
        if (!target) return;
        const allActions = dailyRadarActions(rows);
        const laneOptions = dailyRadarLanes(allActions);
        const validLanes = new Set(["all", ...laneOptions.map((lane) => lane.label)]);
        if (!validLanes.has(activeDailyLane)) activeDailyLane = "all";
        const actions = activeDailyLane === "all" ? allActions : allActions.filter((entry) => entry.daily_label === activeDailyLane);
        const stats = dailyRadarStats(actions, rows);
        const laneButtons = [
          { label: "dailyLaneAll", key: "all", count: allActions.length, avgScore: dailyAverageScore(allActions) },
          ...laneOptions.map((lane) => ({ ...lane, key: lane.label })),
        ];
        target.innerHTML = allActions.length ? `
          <article class="daily-brief">
            <strong>${escapeHtml(stats.lead)}</strong>
            <p>${escapeHtml(t("dailyLead"))} · ${escapeHtml(t("dailyAvgPriority"))} ${escapeHtml(stats.avgPriority)}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.avgPriority)}%"></span></div>
            <div class="daily-stats">
              <article class="daily-stat"><strong>${escapeHtml(actions.length)}</strong><span>${escapeHtml(t("dailyActions"))}</span></article>
              <article class="daily-stat"><strong>${escapeHtml(stats.sampleGaps)}</strong><span>${escapeHtml(t("dailySampleGaps"))}</span></article>
            </div>
            <div class="daily-lanes" aria-label="${escapeHtml(t("dailyActionLanes"))}">
              ${laneButtons.map((lane) => `<button type="button" class="daily-lane ${activeDailyLane === lane.key ? "active" : ""}" data-daily-lane="${escapeHtml(lane.key)}" aria-pressed="${activeDailyLane === lane.key ? "true" : "false"}"><strong>${escapeHtml(t(lane.label))}</strong><span>${escapeHtml(lane.count)} · ${escapeHtml(lane.avgScore)}</span></button>`).join("")}
            </div>
            <p>${escapeHtml(t("dailyRadarBriefHint"))}</p>
          </article>
          <div class="daily-list">
            ${actions.length ? actions.map(dailyRadarActionHtml).join("") : `<div class="row">${escapeHtml(t("dailyNoFilteredActions"))}</div>`}
          </div>
        ` : `<div class="row">${escapeHtml(t("dailyNoActions"))}</div>`;
      }

      function dailyRadarActions(rows) {
        const actions = [];
        coreMarketWatchRows(rows).slice(0, 3).forEach((entry) => {
          const nextAction = coreWatchNextAction(entry);
          const term = (entry.watch_terms || [])[0] || "";
          actions.push({
            ...entry,
            daily_kind: "dailyKindCore",
            daily_label: nextAction.label,
            daily_detail: `${t("samples")} ${entry.sample_count}/${entry.target_samples} · ${t("avgPremium")} ${formatPercent(entry.avg_premium_rate)}`,
            daily_score: Number(entry.watch_score) || Number(entry.priority_score) || Number(entry.brand_weight) || 0,
            daily_target: "coreMarketWatch",
            daily_sample: entry.alias,
            daily_keyword: term,
            daily_tone: nextAction.tone,
            daily_rank: nextAction.label === "coreWatchActionAnchor" ? 5 : 4,
          });
        });
        brandWeightScorecardRows(rows, Number.POSITIVE_INFINITY).slice(0, 4).forEach((entry) => {
          actions.push({
            ...entry,
            daily_kind: "dailyKindScorecard",
            daily_label: scorecardVerdictLabel(entry.scorecard_verdict),
            daily_detail: `${t("scorecardCurrent")} ${entry.brand_weight} · ${t("scorecardTarget")} ${entry.target_weight} · ${t("formulaConfidence")} ${entry.confidence}%`,
            daily_score: Math.max(Number(entry.confidence) || 0, Number(entry.brand_weight) || 0),
            daily_target: "brandWeightScorecard",
            daily_sample: Number(entry.sample_count) < 2 ? entry.alias : "",
            daily_tone: scorecardVerdictPill(entry.scorecard_verdict),
            daily_rank: scorecardRank(entry.scorecard_verdict),
          });
        });
        buildSamplePlanRows(rows).slice(0, 4).forEach((entry) => {
          const keyword = (entry.market_keywords || [])[0] || "";
          actions.push({
            ...entry,
            daily_kind: "dailyKindSampling",
            daily_label: samplePlanActionLabel(entry.next_action),
            daily_detail: `${t("samplePlanMissing")} ${entry.missing_samples} · ${t("samplePlanProgress")} ${entry.sample_count}/${entry.target_samples}`,
            daily_score: Number(entry.priority_score) || 0,
            daily_target: "samplePlan",
            daily_sample: entry.alias,
            daily_keyword: keyword,
            daily_tone: samplePlanPill(entry.urgency),
            daily_rank: samplePlanRank(entry.urgency),
          });
        });
        return actions
          .sort((a, b) => (
            (Number(b.daily_rank) || 0) - (Number(a.daily_rank) || 0)
            || (Number(b.daily_score) || 0) - (Number(a.daily_score) || 0)
            || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
          ))
          .slice(0, 4);
      }

      function dailyRadarStats(actions, rows) {
        const sampleGaps = (rows || []).filter((entry) => Number(entry.sample_count) < sampleTarget(entry.brand_weight, entry.tier)).length;
        const avgPriority = dailyAverageScore(actions);
        return {
          lead: actions[0]?.alias || "-",
          sampleGaps,
          avgPriority,
        };
      }

      function dailyAverageScore(actions) {
        return (actions || []).length
          ? Math.round(actions.reduce((sum, entry) => sum + (Number(entry.daily_score) || 0), 0) / actions.length)
          : 0;
      }

      function dailyRadarLanes(actions) {
        const byLabel = new Map();
        (actions || []).forEach((entry) => {
          const label = entry.daily_label || "dailyNoActions";
          const bucket = byLabel.get(label) || { label, count: 0, totalScore: 0 };
          bucket.count += 1;
          bucket.totalScore += Number(entry.daily_score) || 0;
          byLabel.set(label, bucket);
        });
        return Array.from(byLabel.values())
          .map((lane) => ({ ...lane, avgScore: lane.count ? Math.round(lane.totalScore / lane.count) : 0 }))
          .sort((a, b) => (b.count - a.count) || (b.avgScore - a.avgScore))
          .slice(0, 4);
      }

      function dailyRadarActionHtml(entry) {
        return `<article class="daily-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)} · ${escapeHtml(entry.name)}</strong>
              <p>${escapeHtml(t(entry.daily_kind))} · ${escapeHtml(t(entry.daily_label))}</p>
            </div>
            <span class="pill ${escapeHtml(entry.daily_tone || "off")}">${escapeHtml(Math.round(Number(entry.daily_score) || 0))}</span>
          </header>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(Math.round(Number(entry.daily_score) || 0))}%"></span></div>
          <p>${escapeHtml(entry.daily_detail || "")}</p>
          <div class="daily-card-actions">
            <button type="button" class="secondary" data-daily-jump="${escapeHtml(entry.daily_target)}">${escapeHtml(t("dailyJump"))}</button>
            ${entry.daily_sample ? `<button type="button" class="secondary" data-daily-sample="${escapeHtml(entry.daily_sample)}">${escapeHtml(t("dailySample"))}</button>` : ""}
            ${entry.daily_keyword ? `<button type="button" data-daily-keyword-brand="${escapeHtml(entry.alias)}" data-daily-keyword="${escapeHtml(entry.daily_keyword)}">${escapeHtml(t("dailyKeyword"))}</button>` : ""}
          </div>
        </article>`;
      }

      function renderBrandStyleLedger(rows = brandStyleLedgerRows()) {
        const target = $("brandStyleLedger");
        if (!target) return;
        const lanes = brandStyleLedgerLanes(rows);
        target.innerHTML = lanes.map((lane) => `<article class="style-ledger-card" data-style-family="${escapeHtml(lane.family)}" style="${escapeHtml(styleFamilyVisualStyle(lane.family))}">
          <header>
            <div>
              <strong>${escapeHtml(t(styleFamilyLabelKey(lane.family)))}</strong>
              <p class="muted">${escapeHtml(lane.count)} ${escapeHtml(t("styleBrands"))} · ${escapeHtml(t("styleAvgWeight"))} ${escapeHtml(lane.avgWeight)}</p>
            </div>
            <div class="style-ledger-score">${escapeHtml(lane.avgWeight)}</div>
          </header>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(lane.avgWeight)}%"></span></div>
          <p class="muted">${escapeHtml(t("styleLeader"))} ${escapeHtml(lane.leaders || "-")}</p>
          <div class="style-ledger-meta">
            <span>${escapeHtml(t("styleWeightTotal"))} ${escapeHtml(lane.totalWeight)}</span>
            <span>${escapeHtml(t("styleCoreShare"))} ${escapeHtml(lane.coreCount)}/${escapeHtml(lane.count)}</span>
          </div>
          <div class="style-ledger-keywords" aria-label="${escapeHtml(t("styleKeywords"))}">
            ${lane.keywords.length ? lane.keywords.map((keyword) => `<span>${escapeHtml(keyword)}</span>`).join("") : `<span>${escapeHtml(t("styleNoKeywords"))}</span>`}
          </div>
        </article>`).join("");
      }

      function renderBrandWeightSalon(rows = buildBrandRadarMatrix()) {
        const target = $("brandWeightSalon");
        if (!target) return;
        const lanes = brandWeightSalonRows(rows);
        const lead = [...lanes].sort((a, b) => (
          (Number(b.score) || 0) - (Number(a.score) || 0)
          || (Number(b.avgDraft) || 0) - (Number(a.avgDraft) || 0)
        ))[0] || {};
        target.innerHTML = `
          <article class="weight-salon-brief" style="${escapeHtml(styleFamilyVisualStyle(lead.family || "sweet"))}">
            <strong>${escapeHtml(t("brandWeightSalon"))}</strong>
            <p>${escapeHtml(t("brandWeightSalonHint"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(lead.score || 0)}%"></span></div>
            <div class="weight-salon-stats">
              <span><strong>${escapeHtml(t(styleFamilyLabelKey(lead.family || "sweet")))}</strong>${escapeHtml(t("salonLead"))}</span>
              <span><strong>${escapeHtml(lead.avgDraft || 0)}</strong>${escapeHtml(t("salonAvgDraft"))}</span>
              <span><strong>${escapeHtml(lead.avgTarget || 0)}</strong>${escapeHtml(t("salonTargetAvg"))}</span>
              <span><strong>${escapeHtml(lead.evidenceGaps || 0)}</strong>${escapeHtml(t("salonEvidenceGap"))}</span>
            </div>
          </article>
          <div class="weight-salon-list">
            ${lanes.map((lane) => `<article class="weight-salon-card" data-weight-salon="${escapeHtml(lane.family)}" style="${escapeHtml(styleFamilyVisualStyle(lane.family))}">
              <header>
                <div>
                  <strong>${escapeHtml(t(styleFamilyLabelKey(lane.family)))}</strong>
                  <p>${escapeHtml(lane.count)} ${escapeHtml(t("styleBrands"))} · ${escapeHtml(t("styleLeader"))} ${escapeHtml(lane.leaders || "-")}</p>
                </div>
                <span class="pill ${escapeHtml(salonActionPill(lane.action))}">${escapeHtml(t(salonActionLabel(lane.action)))}</span>
              </header>
              <div class="weight-salon-track">
                <span><strong>${escapeHtml(lane.avgDraft)}</strong>${escapeHtml(t("salonAvgDraft"))}</span>
                <strong>${escapeHtml(formatDelta(lane.move))}</strong>
                <span><strong>${escapeHtml(lane.avgTarget)}</strong>${escapeHtml(t("salonTargetAvg"))}</span>
              </div>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(lane.score)}%"></span></div>
              <div class="weight-salon-meta">
                <span>${escapeHtml(t("salonCoreShare"))} ${escapeHtml(lane.coreCount)}/${escapeHtml(lane.count)}</span>
                <span>${escapeHtml(t("salonEvidenceGap"))} ${escapeHtml(lane.evidenceGaps)}</span>
                <span>${escapeHtml(t("salonPremiumSignals"))} ${escapeHtml(lane.premiumSignals)}</span>
                <span>${escapeHtml(t("salonMove"))} ${escapeHtml(formatDelta(lane.move))}</span>
              </div>
            </article>`).join("")}
          </div>
        `;
      }

      function brandWeightSalonRows(rows) {
        const families = ["sweet", "classic", "gothic", "release", "art"];
        const formulas = new Map(buildBrandWeightFormula(rows, Number.POSITIVE_INFINITY).map((entry) => [entry.alias, entry]));
        return families.map((family) => {
          const members = (rows || [])
            .filter((brand) => brandStyleFamily(brand) === family)
            .sort((a, b) => (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0) || String(a.alias).localeCompare(String(b.alias)));
          const totalDraft = members.reduce((sum, brand) => sum + (Number(brand.brand_weight) || Number(brand.weight) || 0), 0);
          const totalTarget = members.reduce((sum, brand) => {
            const formula = formulas.get(brand.alias);
            return sum + (Number(formula?.target_weight) || Number(brand.brand_weight) || Number(brand.weight) || 0);
          }, 0);
          const count = members.length;
          const avgDraft = count ? Math.round(totalDraft / count) : 0;
          const avgTarget = count ? Math.round(totalTarget / count) : 0;
          const evidenceGaps = members.filter((brand) => Number(brand.sample_count) < sampleTarget(brand.brand_weight, brand.tier)).length;
          const evidenceReady = count ? Math.round((count - evidenceGaps) / count * 100) : 0;
          const premiumSignals = members.filter((brand) => Number(brand.avg_premium_rate) >= 0.25).length;
          const lane = {
            family,
            count,
            avgDraft,
            avgTarget,
            move: avgTarget - avgDraft,
            coreCount: members.filter((brand) => brand.tier === "core" || Number(brand.brand_weight) >= 90).length,
            evidenceGaps,
            premiumSignals,
            leaders: members.slice(0, 2).map((brand) => brand.alias).join(" / "),
            score: clampScore(avgDraft * .34 + avgTarget * .44 + evidenceReady * .22),
          };
          lane.action = salonAction(lane);
          return lane;
        });
      }

      function salonAction(lane) {
        if ((Number(lane.evidenceGaps) || 0) > 0 && Number(lane.avgDraft) >= 70) return "collect";
        if (Number(lane.move) >= 4) return "lift";
        if (Number(lane.move) <= -4) return "trim";
        return "hold";
      }

      function salonActionLabel(action) {
        return {
          collect: "salonActionCollect",
          lift: "salonActionLift",
          trim: "salonActionTrim",
          hold: "salonActionHold",
        }[action] || "salonActionHold";
      }

      function salonActionPill(action) {
        if (action === "collect") return "gold";
        if (action === "lift") return "rose";
        if (action === "trim") return "warn";
        return "off";
      }

      function renderStyleCompass(rows = brandStyleLedgerRows()) {
        const target = $("styleCompass");
        if (!target) return;
        const normalizedRows = (rows || []).map((entry) => ({
          ...entry,
          weight: Number(entry.weight ?? entry.brand_weight) || 0,
        }));
        const lanes = brandStyleLedgerLanes(normalizedRows);
        target.innerHTML = lanes.map((lane) => {
          const keyword = lane.keywords[0] || t("styleNoKeywords");
          return `<article class="style-compass-card" data-style-compass="${escapeHtml(lane.family)}" style="${escapeHtml(styleFamilyVisualStyle(lane.family))}">
            <strong>${escapeHtml(t(styleFamilyLabelKey(lane.family)))}</strong>
            <span>${escapeHtml(lane.count)} ${escapeHtml(t("styleBrands"))} · ${escapeHtml(t("styleAvgWeight"))} ${escapeHtml(lane.avgWeight)}</span>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(lane.avgWeight)}%"></span></div>
            <div class="style-compass-foot">
              <b>${escapeHtml(lane.leaders || "-")}</b>
              <small>${escapeHtml(keyword)}</small>
            </div>
          </article>`;
        }).join("");
      }

      function renderBrandLookbook(rows) {
        const target = $("brandLookbook");
        if (!target) return;
        const entries = brandLookbookRows(rows);
        const stats = brandLookbookStats(entries);
        target.innerHTML = entries.length ? `
          <article class="lookbook-brief">
            <strong>${escapeHtml(stats.lead)}</strong>
            <p>${escapeHtml(t("lookbookLead"))} · ${escapeHtml(t("lookbookAvgFit"))} ${escapeHtml(stats.avgFit)}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.avgFit)}%"></span></div>
            <div class="lookbook-stats">
              <article class="lookbook-stat"><strong>${escapeHtml(stats.core)}</strong><span>${escapeHtml(t("lookbookCore"))}</span></article>
              <article class="lookbook-stat"><strong>${escapeHtml(stats.gaps)}</strong><span>${escapeHtml(t("lookbookGaps"))}</span></article>
            </div>
            <p>${escapeHtml(t("brandLookbookHint"))}</p>
          </article>
          <div class="lookbook-rail">
            ${entries.map(brandLookbookCardHtml).join("")}
          </div>
        ` : `<div class="row">${escapeHtml(t("lookbookNoRows"))}</div>`;
      }

      function brandLookbookRows(rows) {
        return [...(rows || [])]
          .map((entry) => ({
            ...entry,
            lookbook_fit: lookbookFitScore(entry),
            lookbook_action: lookbookAction(entry),
          }))
          .sort((a, b) => (
            (Number(b.lookbook_fit) || 0) - (Number(a.lookbook_fit) || 0)
            || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
            || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0)
          ))
          .slice(0, 6);
      }

      function brandLookbookStats(entries) {
        const total = entries.length || 0;
        return {
          lead: entries[0]?.alias || "-",
          core: entries.filter((entry) => Number(entry.brand_weight) >= 90).length,
          gaps: entries.filter((entry) => Number(entry.sample_count) < 2).length,
          avgFit: total ? Math.round(entries.reduce((sum, entry) => sum + (Number(entry.lookbook_fit) || 0), 0) / total) : 0,
        };
      }

      function brandLookbookCardHtml(entry) {
        const motif = entry.visual?.motif || styleLabel(entry.style);
        const palette = entry.visual?.palette || styleLabel(entry.style);
        const cue = entry.visual?.radar_cue || "";
        const keyword = (entry.market_keywords || [])[0] || "";
        const action = entry.lookbook_action || lookbookAction(entry);
        return `<article class="lookbook-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <header>
            <div class="lookbook-cameo" aria-hidden="true">${escapeHtml(entry.alias)}</div>
            <div>
              <strong>${escapeHtml(entry.name)}</strong>
              <p>${escapeHtml(palette)} · ${escapeHtml(motif)}</p>
            </div>
          </header>
          <div class="lookbook-fit">
            <span>${escapeHtml(t("lookbookFit"))}</span>
            <div class="score-track" aria-hidden="true"><span style="--score: ${escapeHtml(entry.lookbook_fit)}%"></span></div>
            <strong>${escapeHtml(entry.lookbook_fit)}</strong>
          </div>
          <div class="lookbook-tags">
            <span>${escapeHtml(tierLabel(entry.tier))}</span>
            <span>${escapeHtml(t(action.label))}</span>
            <span>${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))}</span>
            <span>${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)}</span>
          </div>
          ${cue ? `<p>${escapeHtml(cue)}</p>` : ""}
          <div class="lookbook-actions">
            <button type="button" class="secondary" data-lookbook-sample="${escapeHtml(entry.alias)}">${escapeHtml(t("lookbookSample"))}</button>
            ${keyword ? `<button type="button" data-lookbook-keyword-brand="${escapeHtml(entry.alias)}" data-lookbook-keyword="${escapeHtml(keyword)}">${escapeHtml(t("lookbookKeyword"))}</button>` : ""}
          </div>
        </article>`;
      }

      function lookbookFitScore(entry) {
        const weight = Number(entry.brand_weight) || 0;
        const premium = Math.max(0, Number(entry.avg_premium_rate) || 0);
        const evidence = Math.min(100, (Number(entry.sample_count) || 0) * 22);
        const identity = entry.visual?.motif && entry.visual?.palette && (entry.market_keywords || []).length ? 10 : 0;
        return clampScore(Math.round(weight * .54 + Math.min(24, premium * 48) + evidence * .12 + identity));
      }

      function lookbookAction(entry) {
        const samples = Number(entry.sample_count) || 0;
        const weight = Number(entry.brand_weight) || 0;
        const premium = Number(entry.avg_premium_rate) || 0;
        if (samples < 2 && weight >= 85) return { label: "lookbookActionAnchor" };
        if (samples >= 2 && premium >= 0.25) return { label: "lookbookActionTrack" };
        if (samples >= 2 && premium < -0.05) return { label: "lookbookActionReview" };
        return { label: "lookbookActionWatch" };
      }

      function renderBrandWeightScorecard(rows) {
        const target = $("brandWeightScorecard");
        if (!target) return;
        const cards = brandWeightScorecardRows(rows);
        const stats = brandWeightScorecardStats(cards);
        target.innerHTML = cards.length ? `
          <article class="scorecard-brief">
            <strong>${escapeHtml(stats.topTarget)}</strong>
            <p>${escapeHtml(t("scorecardTop"))} · ${escapeHtml(t("scorecardAvgConfidence"))} ${escapeHtml(stats.avgConfidence)}%</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.avgConfidence)}%"></span></div>
            <div class="scorecard-stats">
              <article class="scorecard-stat"><strong>${escapeHtml(stats.aligned)}</strong><span>${escapeHtml(t("scorecardAligned"))}</span></article>
              <article class="scorecard-stat"><strong>${escapeHtml(stats.collect)}</strong><span>${escapeHtml(t("scorecardCollect"))}</span></article>
            </div>
            <p>${escapeHtml(t("brandWeightScorecardHint"))}</p>
          </article>
          <div class="scorecard-list">
            ${cards.map(brandWeightScorecardHtml).join("")}
          </div>
        ` : `<div class="row">${escapeHtml(t("scorecardNoRows"))}</div>`;
      }

      function brandWeightScorecardRows(rows, limit = 6) {
        return buildBrandWeightFormula(rows, Number.POSITIVE_INFINITY).map((entry) => ({
          ...entry,
          scorecard_verdict: scorecardVerdict(entry),
        })).sort((a, b) => (
          scorecardRank(b.scorecard_verdict) - scorecardRank(a.scorecard_verdict)
          || Math.abs(Number(b.delta) || 0) - Math.abs(Number(a.delta) || 0)
          || (Number(b.confidence) || 0) - (Number(a.confidence) || 0)
          || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
        )).slice(0, limit);
      }

      function brandWeightScorecardStats(cards) {
        const total = cards.length || 0;
        return {
          topTarget: cards[0]?.alias || "-",
          aligned: cards.filter((entry) => entry.scorecard_verdict === "aligned").length,
          collect: cards.filter((entry) => entry.scorecard_verdict === "collect").length,
          avgConfidence: total ? Math.round(cards.reduce((sum, entry) => sum + (Number(entry.confidence) || 0), 0) / total) : 0,
        };
      }

      function brandWeightScorecardHtml(entry) {
        return `<article class="scorecard-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)} · ${escapeHtml(entry.name)}</strong>
              <p>${escapeHtml(t("scorecardVerdict"))} · ${escapeHtml(t(scorecardVerdictLabel(entry.scorecard_verdict)))}</p>
            </div>
            <span class="pill ${scorecardVerdictPill(entry.scorecard_verdict)}">${escapeHtml(t(formulaLabel(entry.delta)))}</span>
          </header>
          <div class="scorecard-score">
            <div>
              <strong>${escapeHtml(entry.brand_weight)}</strong>
              <p>${escapeHtml(t("scorecardCurrent"))}</p>
            </div>
            <div class="score-track" aria-hidden="true"><span style="--score: ${escapeHtml(Math.max(Number(entry.brand_weight) || 0, Number(entry.target_weight) || 0))}%"></span></div>
            <div>
              <strong>${escapeHtml(entry.target_weight)}</strong>
              <p>${escapeHtml(t("scorecardTarget"))}</p>
            </div>
          </div>
          <div class="scorecard-parts">
            ${formulaPartBar(t("formulaBase"), entry.parts.base, 90)}
            ${formulaPartBar(t("formulaPremium"), entry.parts.premium, 16)}
            ${formulaPartBar(t("formulaEvidence"), entry.parts.evidence, 8)}
            ${formulaPartBar(t("formulaKeywords"), entry.parts.keywords, 4)}
            ${formulaPartBar(t("formulaWatchability"), entry.parts.watchability, 4)}
          </div>
          <p>${escapeHtml(t("formulaConfidence"))} ${escapeHtml(entry.confidence)}% · ${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)}</p>
          <div class="scorecard-actions">
            ${Number(entry.delta) ? `<button type="button" class="secondary" data-scorecard-apply="${escapeHtml(entry.alias)}" data-scorecard-target="${escapeHtml(entry.target_weight)}">${escapeHtml(t("formulaApplyDraft"))}</button>` : ""}
            ${Number(entry.sample_count) < 2 ? `<button type="button" class="secondary" data-scorecard-sample="${escapeHtml(entry.alias)}">${escapeHtml(t("tuningAddSample"))}</button>` : ""}
          </div>
        </article>`;
      }

      function scorecardVerdict(entry) {
        if (Number(entry.sample_count) < 2 && Number(entry.brand_weight) >= 70) return "collect";
        if (Number(entry.delta) >= 5) return "raise";
        if (Number(entry.delta) <= -5) return "lower";
        return "aligned";
      }

      function scorecardRank(verdict) {
        return { collect: 4, raise: 3, lower: 2, aligned: 1 }[verdict] || 0;
      }

      function scorecardVerdictLabel(verdict) {
        return {
          collect: "trajectoryCollect",
          raise: "formulaRaise",
          lower: "formulaLower",
          aligned: "formulaAligned",
        }[verdict] || "formulaAligned";
      }

      function scorecardVerdictPill(verdict) {
        if (verdict === "collect") return "gold";
        if (verdict === "raise") return "rose";
        if (verdict === "lower") return "warn";
        return "off";
      }

      function brandStyleLedgerRows() {
        const draftWeights = new Map(Array.from(document.querySelectorAll("[data-brand-weight]")).map((input) => [input.dataset.brandWeight, Number(input.value) || 0]));
        return (currentState?.brand_weights || []).map((brand) => ({
          ...brand,
          weight: clampScore(draftWeights.get(brand.alias) ?? brand.weight),
        }));
      }

      function brandStyleLedgerLanes(rows) {
        const families = ["sweet", "classic", "gothic", "release", "art"];
        return families.map((family) => {
          const members = (rows || [])
            .filter((brand) => brandStyleFamily(brand) === family)
            .sort((a, b) => (Number(b.weight) || 0) - (Number(a.weight) || 0) || String(a.alias).localeCompare(String(b.alias)));
          const totalWeight = members.reduce((sum, brand) => sum + (Number(brand.weight) || 0), 0);
          const avgWeight = members.length ? Math.round(totalWeight / members.length) : 0;
          const keywords = uniqueValues(members.flatMap((brand) => brand.market_keywords || []).filter(Boolean)).slice(0, 4);
          return {
            family,
            count: members.length,
            avgWeight,
            totalWeight,
            coreCount: members.filter((brand) => brand.tier === "core" || Number(brand.weight) >= 90).length,
            leaders: members.slice(0, 2).map((brand) => brand.alias).join(" / "),
            keywords,
          };
        });
      }

      function brandStyleFamily(brand) {
        const style = String(brand?.style || "").toLowerCase();
        if (style.includes("gothic") || style.includes("prince")) return "gothic";
        if (style.includes("release") || style.includes("restock")) return "release";
        if (style.includes("art")) return "art";
        if (style.includes("classic")) return "classic";
        return "sweet";
      }

      function styleFamilyLabelKey(family) {
        return {
          sweet: "styleFamilySweet",
          classic: "styleFamilyClassic",
          gothic: "styleFamilyGothic",
          release: "styleFamilyRelease",
          art: "styleFamilyArt",
        }[family] || "styleFamilySweet";
      }

      function styleFamilyVisualStyle(family) {
        return {
          sweet: "--brand-accent: #c45f82; --brand-paper: #fff5f8;",
          classic: "--brand-accent: #a9782c; --brand-paper: #fff8ec;",
          gothic: "--brand-accent: #611b31; --brand-paper: #fff3f5;",
          release: "--brand-accent: #0f6760; --brand-paper: #f1fbf8;",
          art: "--brand-accent: #426a70; --brand-paper: #f1fbfb;",
        }[family] || "";
      }

      function uniqueValues(values) {
        return [...new Set((values || []).map((value) => String(value || "").trim()).filter(Boolean))];
      }

      function brandKeywordPearlsHtml(brand) {
        const terms = (brand.market_keywords || brand.keywords || []).slice(0, 3);
        return terms.length
          ? terms.map((term) => `<span>${escapeHtml(term)}</span>`).join("")
          : `<span>${escapeHtml(t("noMarketKeywords"))}</span>`;
      }

      function brandThemeClass(brand) {
        const style = `${brand.style || ""} ${brand.tier || ""}`.toLowerCase();
        if (style.includes("gothic") || style.includes("prince")) return "theme-gothic";
        if (style.includes("classic")) return "theme-classic";
        if (style.includes("release") || style.includes("restock")) return "theme-mint";
        return "theme-sweet";
      }

      function brandVisualStyle(brand) {
        const accent = cssHexColor(brand.visual?.accent);
        const paper = cssHexColor(brand.visual?.paper);
        const styles = [
          accent ? `--brand-accent: ${accent}` : "",
          paper ? `--brand-paper: ${paper}` : "",
        ].filter(Boolean);
        return styles.length ? `${styles.join("; ")};` : "";
      }

      function cssHexColor(value) {
        const text = String(value || "").trim();
        return /^#[0-9a-fA-F]{6}$/.test(text) ? text : "";
      }

      function brandIdentityHtml(brand) {
        const visual = brand.visual || {};
        const motif = visual.motif || styleLabel(brand.style);
        const cue = visual.radar_cue || "";
        return `<div class="brand-identity">
          <span>${escapeHtml(t("visualMotif"))} · ${escapeHtml(motif)}</span>
          ${cue ? `<p>${escapeHtml(cue)}</p>` : ""}
        </div>`;
      }

      function brandWeightInsightHtml(brand, weightValue) {
        const weight = clampScore(weightValue ?? brand.weight);
        const band = weightBandKey(weight);
        const intent = weightIntentKey(weight);
        return `
          <p><strong>${escapeHtml(t("weightBand"))}</strong> ${escapeHtml(t(band))}</p>
          <p><strong>${escapeHtml(t("weightIntent"))}</strong> ${escapeHtml(t(intent))}</p>
          <p><strong>${escapeHtml(t("keywordCount"))}</strong> ${escapeHtml((brand.keywords || []).length)}</p>
        `;
      }

      function weightBandKey(weight) {
        if (clampScore(weight) >= 90) return "weightBandCore";
        if (clampScore(weight) >= 70) return "weightBandWatch";
        return "weightBandArchive";
      }

      function weightIntentKey(weight) {
        if (clampScore(weight) >= 90) return "weightIntentCore";
        if (clampScore(weight) >= 70) return "weightIntentWatch";
        return "weightIntentArchive";
      }

      function renderBrandKeywordRadar(weights) {
        const brands = [...weights].sort((a, b) => (Number(b.weight) || 0) - (Number(a.weight) || 0));
        $("brandKeywordRadar").innerHTML = brands.map((brand) => {
          const terms = brand.market_keywords || [];
          return `<article class="keyword-card" style="${escapeHtml(brandVisualStyle(brand))}">
            <header>
              <div>
                <strong>${escapeHtml(brand.alias)}</strong>
                <p class="muted">${escapeHtml(brand.name)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(brand.weight)} · ${escapeHtml(brand.visual?.motif || styleLabel(brand.style))}</p>
              </div>
              <span class="pill ${brand.weight >= 90 ? "rose" : ""}">${escapeHtml(t("marketKeywords"))} ${escapeHtml(terms.length)}</span>
            </header>
            <div class="keyword-chips">
              ${terms.length ? terms.map((term) => `<button type="button" class="secondary" data-keyword-brand="${escapeHtml(brand.alias)}" data-keyword-term="${escapeHtml(term)}">${escapeHtml(term)}</button>`).join("") : `<span class="muted">${escapeHtml(t("noMarketKeywords"))}</span>`}
            </div>
          </article>`;
        }).join("");
      }

      function renderPremiumSeedRadar(rows) {
        const seeds = premiumSeedRows(rows);
        renderPremiumSeedSummary(seeds);
        $("premiumSeedRadar").innerHTML = seeds.length ? seeds.map((entry) => `<article class="seed-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)}</strong>
              <p class="muted">${escapeHtml(entry.name)} · ${escapeHtml(styleLabel(entry.style))}</p>
            </div>
            <div>
              <div class="seed-score">${escapeHtml(entry.seed_score)}</div>
              <span class="pill ${premiumSeedStagePill(entry.seed_stage)}">${escapeHtml(t(premiumSeedStageLabel(entry.seed_stage)))}</span>
            </div>
          </header>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(entry.seed_score)}%"></span></div>
          <p class="muted">${escapeHtml(t(premiumSeedIntentKey(entry)))} · ${escapeHtml(entry.visual?.radar_cue || "")}</p>
          <div class="seed-meta">
            <span>${escapeHtml(t("weightLabel"))} ${escapeHtml(entry.brand_weight)}</span>
            <span>${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)}</span>
            <span>${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))}</span>
            ${previewingDraftWeights && hasScoreDelta(entry.score_delta) ? `<span>${escapeHtml(t("scoreDelta"))} ${escapeHtml(formatDelta(entry.score_delta))}</span>` : ""}
          </div>
          <div class="seed-keywords" aria-label="${escapeHtml(t("premiumSeedTerms"))}">
            ${entry.seed_terms.length ? entry.seed_terms.map((term) => `<button type="button" data-premium-seed-brand="${escapeHtml(entry.alias)}" data-premium-seed-keyword="${escapeHtml(term)}">${escapeHtml(term)}</button>`).join("") : `<span class="muted">${escapeHtml(t("styleNoKeywords"))}</span>`}
          </div>
          <div class="seed-links">
            ${(entry.watch_urls || []).slice(0, 3).map((link) => safeUrl(link.url) ? `<a href="${escapeHtml(safeUrl(link.url))}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>` : "").join("")}
          </div>
        </article>`).join("") : `<div class="row">${escapeHtml(t("premiumSeedEmpty"))}</div>`;
      }

      function renderPremiumSeedSummary(seeds) {
        const stats = premiumSeedStats(seeds);
        $("premiumSeedSummary").innerHTML = [
          [stats.taskCount, "premiumSeedTaskCount"],
          [stats.coreGaps, "premiumSeedCoreGaps"],
          [stats.topSeed, "premiumSeedTopSeed"],
          [stats.avgScore, "premiumSeedAvgScore"],
        ].map(([value, label]) => `<article class="seed-summary-card"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(t(label))}</span></article>`).join("");
      }

      function premiumSeedStats(seeds) {
        const rows = seeds || [];
        const taskCount = rows.reduce((sum, entry) => sum + (entry.seed_terms || []).length, 0);
        const scores = rows.map((entry) => Number(entry.seed_score) || 0);
        const top = [...rows].sort((a, b) => (Number(b.seed_score) || 0) - (Number(a.seed_score) || 0) || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0))[0];
        return {
          taskCount,
          coreGaps: rows.filter((entry) => Number(entry.brand_weight) >= 90 && Number(entry.sample_count) < 2).length,
          topSeed: top ? `${top.alias} ${top.seed_terms?.[0] || ""}`.trim() : "-",
          avgScore: scores.length ? Math.round(scores.reduce((sum, value) => sum + value, 0) / scores.length) : 0,
        };
      }

      function premiumSeedRows(rows) {
        return (rows || []).map((entry) => {
          const keywordCount = (entry.market_keywords || []).length;
          const sampleCount = Number(entry.sample_count) || 0;
          const premium = Number(entry.avg_premium_rate) || 0;
          const seedScore = clampScore(
            (Number(entry.brand_weight) || 0) * .52
            + Math.max(0, premium) * 35
            + Math.min(18, keywordCount * 3)
            + (sampleCount < 2 ? 16 : sampleCount < 5 ? 8 : 0)
          );
          return {
            ...entry,
            seed_score: seedScore,
            seed_stage: premiumSeedStage(sampleCount),
            seed_terms: (entry.market_keywords || []).slice(0, 4),
          };
        }).filter((entry) => (entry.seed_terms || []).length || Number(entry.brand_weight) >= 70)
          .sort((a, b) => (
            (Number(b.seed_score) || 0) - (Number(a.seed_score) || 0)
            || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
            || String(a.alias).localeCompare(String(b.alias))
          )).slice(0, 6);
      }

      function premiumSeedIntentKey(entry) {
        const samples = Number(entry.sample_count) || 0;
        const premium = Number(entry.avg_premium_rate) || 0;
        const weight = Number(entry.brand_weight) || 0;
        if (samples < 2 && weight >= 90) return "premiumSeedIntentCoreGap";
        if (premium >= 0.25) return "premiumSeedIntentPremium";
        if (samples < 2) return "premiumSeedIntentSeed";
        return "premiumSeedIntentWatch";
      }

      function premiumSeedStage(sampleCount) {
        const count = Number(sampleCount) || 0;
        if (count <= 0) return "seed";
        if (count < 2) return "pair";
        if (count < 5) return "expand";
        return "watch";
      }

      function premiumSeedStageLabel(stage) {
        return {
          seed: "premiumSeedStageSeed",
          pair: "premiumSeedStagePair",
          expand: "premiumSeedStageExpand",
          watch: "premiumSeedStageWatch",
        }[stage] || "premiumSeedStageSeed";
      }

      function premiumSeedStagePill(stage) {
        if (stage === "seed") return "rose";
        if (stage === "pair" || stage === "expand") return "gold";
        return "";
      }

      function renderMarketForm(weights) {
        const select = $("marketBrand");
        const current = select.value;
        select.innerHTML = weights.map((brand) => `<option value="${escapeHtml(brand.alias)}">${escapeHtml(brand.alias)} · ${escapeHtml(brand.name)}</option>`).join("");
        if (current && Array.from(select.options).some((option) => option.value === current)) {
          select.value = current;
        }
      }

      function renderSamplePreview() {
        const retail = Number($("marketRetail").value) || 0;
        const resale = Number($("marketResale").value) || 0;
        const currency = $("marketCurrency").value || "";
        const alias = $("marketBrand").value;
        const brand = brandByAlias(alias) || {};
        if (retail <= 0 || resale <= 0) {
          $("samplePreview").innerHTML = `<div><strong>${escapeHtml(t("samplePreview"))}</strong></div><p>${escapeHtml(t("samplePreviewEmpty"))}</p>`;
          return;
        }
        const premiumRate = (resale - retail) / retail;
        const spread = resale - retail;
        const breakdown = premiumScoreBreakdown(premiumRate, Number(brand.weight) || 50, 1);
        const score = opportunityPriorityScore(breakdown);
        const band = premiumBand(premiumRate);
        $("samplePreview").innerHTML = `
          <div>
            <strong>${escapeHtml(formatPercent(premiumRate))}</strong>
            <p>${escapeHtml(sampleSignalLabel(premiumRate))} · ${escapeHtml(valueLabel("premiumBand", band))}</p>
          </div>
          <p>${escapeHtml(alias)} · ${escapeHtml(t("sampleSpread"))} ${escapeHtml(formatMoney(spread, currency))} · ${escapeHtml(t("sampleScore"))} ${escapeHtml(score)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(brand.weight || 50)}</p>
        `;
      }

      function renderFocusQueue(queue) {
        $("focusQueue").innerHTML = queue.length ? queue.map((brand) => `<article class="focus-card">
          <header>
            <strong>${escapeHtml(brand.alias)}</strong>
            <span class="pill rose">${escapeHtml(t("radarScore"))} ${escapeHtml(brand.score)}</span>
          </header>
          <p class="muted">${escapeHtml(brand.name)}</p>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${Number(brand.score) || 0}%"></span></div>
          <p class="muted">${escapeHtml(t("weightLabel"))} ${escapeHtml(brand.weight)} · ${escapeHtml(tierLabel(brand.tier))} · ${escapeHtml(t("observed"))} ${escapeHtml(brand.item_count)}/${escapeHtml(brand.event_count)}</p>
        </article>`).join("") : `<div class="row">${escapeHtml(t("noFocusQueue"))}</div>`;
      }

      function renderMarketAlertLine(alertsState) {
        const summary = alertsState.summary || {};
        const alerts = alertsState.alerts || [];
        $("marketAlertLine").innerHTML = `
          <article class="alert-brief">
            <strong>${escapeHtml(summary.total || 0)}</strong>
            <p class="muted">${escapeHtml(t("alertTotal"))}</p>
            <div class="coverage-stats">
              <article class="coverage-stat"><strong>${escapeHtml(summary.critical || 0)}</strong><span class="muted">${escapeHtml(t("alertCritical"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(summary.watch || 0)}</strong><span class="muted">${escapeHtml(t("alertWatch"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(summary.sample_gap || 0)}</strong><span class="muted">${escapeHtml(t("alertSampleGap"))}</span></article>
            </div>
          </article>
          <div class="alert-list">
            ${alerts.length ? alerts.map(renderMarketAlert).join("") : `<div class="row">${escapeHtml(t("noAlerts"))}</div>`}
          </div>
        `;
      }

      function renderMarketAlert(alert) {
        const pill = alert.severity === "critical" ? "rose" : alert.severity === "sample_gap" ? "gold" : "";
        const details = [
          `${t("alertScore")} ${alert.score || 0}`,
          `${t("avgPremium")} ${formatPercent(alert.premium_rate)}`,
          alert.sample_count !== undefined ? `${t("samples")} ${alert.sample_count}` : "",
          alert.quality_score !== undefined ? `${t("qualityScore")} ${alert.quality_score}` : "",
        ].filter(Boolean).join(" · ");
        return `<article class="alert-card">
          <header>
            <div>
              <strong>${escapeHtml(alert.alias)} · ${escapeHtml(alert.title || "-")}</strong>
              <p class="muted">${escapeHtml(valueLabel("alertKind", alert.kind))} · ${escapeHtml(valueLabel("alertReason", alert.reason))}</p>
            </div>
            <span class="pill ${pill}">${escapeHtml(valueLabel("alertSeverity", alert.severity))}</span>
          </header>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(alert.score || 0)}%"></span></div>
          <p class="muted">${escapeHtml(details)}</p>
        </article>`;
      }

      function renderMarketMomentum(momentum) {
        const rows = momentum || [];
        const counts = {
          rising: rows.filter((row) => row.direction === "rising").length,
          cooling: rows.filter((row) => row.direction === "cooling").length,
          steady: rows.filter((row) => row.direction === "steady").length,
        };
        const topScore = rows.length ? Math.max(...rows.map((row) => Number(row.priority_score) || 0)) : 0;
        $("marketMomentum").innerHTML = `
          <article class="momentum-brief">
            <strong>${escapeHtml(topScore)}</strong>
            <p>${escapeHtml(t("priorityScore"))} · ${escapeHtml(rows.length)} ${escapeHtml(t("momentumTotal"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(topScore)}%"></span></div>
            <div class="coverage-stats">
              <article class="coverage-stat"><strong>${escapeHtml(counts.rising)}</strong><span class="muted">${escapeHtml(t("momentumRisingCount"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(counts.cooling)}</strong><span class="muted">${escapeHtml(t("momentumCoolingCount"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(counts.steady)}</strong><span class="muted">${escapeHtml(t("momentumSteadyCount"))}</span></article>
            </div>
          </article>
          <div class="momentum-list">
            ${rows.length ? rows.map((row) => `<article class="momentum-card">
              <header>
                <div>
                  <strong>${escapeHtml(row.brand_alias)} · ${escapeHtml(row.latest_item || "-")}</strong>
                  <p>${escapeHtml([row.source, row.observed_at].filter(Boolean).join(" · ") || t("undated"))}</p>
                </div>
                <span class="pill ${momentumPill(row.direction)}">${escapeHtml(momentumDirection(row.direction))}</span>
              </header>
              <div class="momentum-delta">${escapeHtml(formatDeltaPercent(row.delta))}</div>
              <div class="score-breakdown">
                ${momentumBar(t("momentumLatest"), row.latest_premium_rate)}
                ${momentumBar(t("momentumPrevious"), row.previous_premium_rate)}
              </div>
              <p>${escapeHtml(t("samples"))} ${escapeHtml(row.sample_count)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(row.brand_weight)} · ${escapeHtml(t("priorityScore"))} ${escapeHtml(row.priority_score)}</p>
            </article>`).join("") : `<div class="row">${escapeHtml(t("noMomentum"))}</div>`}
          </div>
        `;
      }

      function momentumBar(label, value) {
        return `<div class="profile-row">
          <span>${escapeHtml(label)}</span>
          <div class="score-track" aria-hidden="true"><span style="--score: ${escapeHtml(percentScore(value))}%"></span></div>
          <span>${escapeHtml(formatPercent(value))}</span>
        </div>`;
      }

      function renderOpportunityRadar(opportunities) {
        renderOpportunitySummary(opportunities);
        syncOpportunityFilterButtons();
        const visible = activeOpportunityFilter === "all" ? opportunities : opportunities.filter((entry) => entry.band === activeOpportunityFilter);
        $("opportunityRadar").innerHTML = visible.length ? visible.map((entry) => `<article class="opportunity-card">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)}</strong>
              <p class="muted">${escapeHtml(entry.name)}</p>
            </div>
            <span class="pill ${opportunityPill(entry.band)}">${escapeHtml(valueLabel("opportunityBand", entry.band))}</span>
          </header>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${Number(entry.priority_score) || 0}%"></span></div>
          <p class="muted">${previewingDraftWeights ? `<span class="pill gold">${escapeHtml(t("draftPreview"))}</span> · ` : ""}${escapeHtml(t("priorityScore"))} ${escapeHtml(entry.priority_score)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(entry.brand_weight)}${previewingDraftWeights && hasScoreDelta(entry.score_delta) ? ` · ${escapeHtml(t("scoreDelta"))} ${escapeHtml(formatDelta(entry.score_delta))}` : ""}</p>
          ${renderScoreBreakdown(entry.score_breakdown)}
          <p class="muted">${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)}</p>
          <p class="muted">${escapeHtml(reasonLabels(entry.reason_codes).join(" · "))}</p>
        </article>`).join("") : `<div class="row">${escapeHtml(t("noOpportunity"))}</div>`;
      }

      function renderBrandRadarMatrix(rows) {
        syncMatrixControls();
        const visible = filterMatrixRows(sortMatrixRows(rows));
        $("brandRadarMatrix").innerHTML = visible.length ? [
          `<div class="matrix-row header" aria-hidden="true">
            <span>${escapeHtml(t("matrixBrand"))}</span>
            <span>${escapeHtml(t("matrixScore"))}</span>
            <span>${escapeHtml(t("matrixWeight"))}</span>
            <span>${escapeHtml(t("matrixPremium"))}</span>
            <span>${escapeHtml(t("matrixSamples"))}</span>
            <span>${escapeHtml(t("matrixAction"))}</span>
          </div>`,
          ...visible.map((entry) => `<article class="matrix-row">
            <div class="matrix-brand">
              <strong>${escapeHtml(entry.alias)}</strong>
              <span>${escapeHtml(entry.name)}</span>
            </div>
            <div class="matrix-score">
              <strong>${escapeHtml(entry.priority_score)}${previewingDraftWeights && hasScoreDelta(entry.score_delta) ? ` ${escapeHtml(formatDelta(entry.score_delta))}` : ""}</strong>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${Number(entry.priority_score) || 0}%"></span></div>
            </div>
            <span>${escapeHtml(entry.brand_weight)}</span>
            <span>${escapeHtml(formatPercent(entry.avg_premium_rate))}</span>
            <span>${escapeHtml(entry.sample_count)}</span>
            <div class="matrix-action">
              <span class="pill ${opportunityPill(entry.band)}">${escapeHtml(valueLabel("opportunityBand", entry.band))}</span>
              <span class="muted">${escapeHtml(matrixActionReason(entry))}</span>
            </div>
          </article>`),
        ].join("") : `<div class="row">${escapeHtml(t("noOpportunity"))}</div>`;
      }

      function matrixActionReason(entry) {
        const labels = reasonLabels(entry.reason_codes || []).filter(Boolean);
        if (labels.length) return labels.slice(0, 2).join(" · ");
        return valueLabel("weightRole", entry.weight_role) || tierLabel(entry.tier);
      }

      function renderSampleCoverage(rows) {
        const coverage = sampleCoverage(rows);
        const priority = rows
          .filter((entry) => Number(entry.sample_count) < 2)
          .sort((a, b) => (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0))
          .slice(0, 5);
        $("sampleCoverage").innerHTML = `
          <div class="coverage-meter">
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${coverage.percent}%"></span></div>
            <p class="muted">${escapeHtml(t("coverageProgress"))} ${escapeHtml(coverage.percent)}% · ${escapeHtml(t("coverageGoal"))}</p>
            <div class="coverage-stats">
              <article class="coverage-stat"><strong>${escapeHtml(coverage.ready)}</strong><span class="muted">${escapeHtml(t("coverageReady"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(coverage.thin)}</strong><span class="muted">${escapeHtml(t("coverageThin"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(coverage.missing)}</strong><span class="muted">${escapeHtml(t("coverageMissing"))}</span></article>
            </div>
          </div>
          <div class="coverage-list">
            ${priority.length ? priority.map((entry) => `<article class="coverage-card">
              <div>
                <strong>${escapeHtml(entry.alias)}</strong>
                <p class="muted">${escapeHtml(entry.name)} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(entry.brand_weight)}</p>
              </div>
              <button type="button" class="secondary" data-coverage-sample="${escapeHtml(entry.alias)}">${escapeHtml(t("tuningAddSample"))}</button>
            </article>`).join("") : `<div class="row">${escapeHtml(t("noOpportunity"))}</div>`}
          </div>
        `;
      }

      function sampleCoverage(rows) {
        const total = rows.length || 1;
        const ready = rows.filter((entry) => Number(entry.sample_count) >= 5).length;
        const thin = rows.filter((entry) => Number(entry.sample_count) >= 2 && Number(entry.sample_count) < 5).length;
        const missing = rows.filter((entry) => Number(entry.sample_count) < 2).length;
        return {
          ready,
          thin,
          missing,
          percent: Math.round(((ready + thin) / total) * 100),
        };
      }

      function renderSamplePlan(rows) {
        const plan = buildSamplePlanRows(rows);
        $("samplePlan").innerHTML = plan.length ? `${samplePlanSummaryHtml(plan, rows)}
          ${plan.map((entry) => `<article class="sample-plan-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)}</strong>
              <p class="muted">${escapeHtml(entry.name)}</p>
            </div>
            <span class="pill ${samplePlanPill(entry.urgency)}">${escapeHtml(t(samplePlanUrgencyLabel(entry.urgency)))}</span>
          </header>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(samplePlanProgress(entry))}%"></span></div>
          <p class="muted">${escapeHtml(t("samplePlanProgress"))} ${escapeHtml(entry.sample_count)}/${escapeHtml(entry.target_samples)} · ${escapeHtml(t("samplePlanMissing"))} ${escapeHtml(entry.missing_samples)} · ${escapeHtml(t("priorityScore"))} ${escapeHtml(entry.priority_score)}</p>
          <div class="sample-plan-keywords">
            ${(entry.market_keywords || []).length ? entry.market_keywords.map((keyword) => `<button type="button" data-sample-plan-keyword="${escapeHtml(keyword)}" data-sample-plan-keyword-brand="${escapeHtml(entry.alias)}">${escapeHtml(keyword)}</button>`).join("") : `<span>${escapeHtml(t("profileNoKeywords"))}</span>`}
          </div>
          <div class="sample-plan-links search-links">
            ${(entry.watch_urls || []).slice(0, 3).map((link) => safeUrl(link.url) ? `<a href="${escapeHtml(safeUrl(link.url))}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>` : "").join("")}
          </div>
          <button type="button" class="secondary" data-sample-plan="${escapeHtml(entry.alias)}">${escapeHtml(t(samplePlanActionLabel(entry.next_action)))}</button>
        </article>`).join("")}` : `<div class="row">${escapeHtml(t("samplePlanNoRows"))}</div>`;
      }

      function samplePlanSummaryHtml(plan, rows) {
        const stats = samplePlanStats(plan, rows);
        return `<article class="sample-plan-summary">
          <div class="sample-plan-hero">
            <strong>${escapeHtml(stats.completion)}%</strong>
            <p class="muted">${escapeHtml(t("samplePlanCompletion"))} · ${escapeHtml(stats.sampled)}/${escapeHtml(stats.target)} ${escapeHtml(t("samples"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.completion)}%"></span></div>
          </div>
          <div class="sample-plan-stats">
            <article class="sample-plan-stat"><strong>${escapeHtml(stats.openBrands)}</strong><span class="muted">${escapeHtml(t("samplePlanOpenBrands"))}</span></article>
            <article class="sample-plan-stat"><strong>${escapeHtml(stats.coreGaps)}</strong><span class="muted">${escapeHtml(t("samplePlanCoreGaps"))}</span></article>
            <article class="sample-plan-stat"><strong>${escapeHtml(stats.missing)}</strong><span class="muted">${escapeHtml(t("samplePlanTotalMissing"))}</span></article>
            <article class="sample-plan-stat"><strong>${escapeHtml(stats.avgPriority)}</strong><span class="muted">${escapeHtml(t("samplePlanAvgPriority"))}</span></article>
          </div>
        </article>`;
      }

      function samplePlanStats(plan, rows) {
        const allTargets = (rows || []).reduce((sum, entry) => sum + sampleTarget(Number(entry.brand_weight) || 0, entry.tier), 0);
        const sampled = (rows || []).reduce((sum, entry) => sum + Math.min(Number(entry.sample_count) || 0, sampleTarget(Number(entry.brand_weight) || 0, entry.tier)), 0);
        const missing = (plan || []).reduce((sum, entry) => sum + (Number(entry.missing_samples) || 0), 0);
        const priorities = (plan || []).map((entry) => Number(entry.priority_score) || 0);
        return {
          target: allTargets,
          sampled,
          missing,
          completion: allTargets ? Math.round(sampled / allTargets * 100) : 0,
          openBrands: (plan || []).filter((entry) => Number(entry.missing_samples) > 0).length,
          coreGaps: (plan || []).filter((entry) => entry.urgency === "critical").length,
          avgPriority: priorities.length ? Math.round(priorities.reduce((sum, value) => sum + value, 0) / priorities.length) : 0,
        };
      }

      function buildSamplePlanRows(rows) {
        return (rows || []).map((entry) => {
          const weight = Number(entry.brand_weight) || 0;
          const sampleCount = Number(entry.sample_count) || 0;
          const target = sampleTarget(weight, entry.tier);
          const missing = Math.max(0, target - sampleCount);
          const premium = Number(entry.avg_premium_rate) || 0;
          return {
            ...entry,
            target_samples: target,
            missing_samples: missing,
            urgency: samplePlanUrgency(missing, weight, sampleCount),
            next_action: samplePlanAction(missing, sampleCount),
            priority_score: samplePlanScore(weight, missing, premium, sampleCount),
          };
        }).filter((entry) => entry.missing_samples > 0 || Number(entry.avg_premium_rate) >= 0.25)
          .sort((a, b) => (
            samplePlanRank(b.urgency) - samplePlanRank(a.urgency)
            || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0)
            || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
          )).slice(0, 9);
      }

      function sampleTarget(weight, tier) {
        if (tier === "core" || Number(weight) >= 90) return 5;
        if (tier === "watch" || Number(weight) >= 70) return 3;
        return 2;
      }

      function samplePlanUrgency(missing, weight, sampleCount) {
        if (Number(missing) <= 0) return "complete";
        if (Number(weight) >= 90 && Number(sampleCount) < 2) return "critical";
        if (Number(weight) >= 70) return "watch";
        return "backfill";
      }

      function samplePlanAction(missing, sampleCount) {
        if (Number(missing) <= 0) return "complete";
        if (Number(sampleCount) <= 0) return "seed";
        if (Number(sampleCount) < 2) return "pair";
        return "roundout";
      }

      function samplePlanScore(weight, missing, premium, sampleCount) {
        const seedBonus = Number(sampleCount) === 0 && Number(weight) >= 90 ? 10 : 0;
        return clampScore((Number(weight) || 0) * .5 + Math.max(0, Number(missing) || 0) * 8 + Math.max(0, Number(premium) || 0) * 35 + seedBonus);
      }

      function samplePlanProgress(entry) {
        return Math.min(100, Math.round((Number(entry.sample_count) || 0) / Math.max(1, Number(entry.target_samples) || 1) * 100));
      }

      function samplePlanRank(urgency) {
        return { critical: 4, watch: 3, backfill: 2, complete: 1 }[urgency] || 0;
      }

      function samplePlanPill(urgency) {
        if (urgency === "critical") return "rose";
        if (urgency === "watch") return "gold";
        if (urgency === "backfill") return "off";
        return "";
      }

      function samplePlanUrgencyLabel(urgency) {
        return {
          critical: "samplePlanCritical",
          watch: "samplePlanWatch",
          backfill: "samplePlanBackfill",
          complete: "samplePlanDone",
        }[urgency] || "samplePlanWatch";
      }

      function samplePlanActionLabel(action) {
        return {
          seed: "samplePlanSeed",
          pair: "samplePlanPair",
          roundout: "samplePlanRoundout",
          complete: "samplePlanComplete",
        }[action] || "samplePlanSeed";
      }

      function renderWeightSnapshot(rows) {
        const stats = weightSnapshotStats(rows);
        const radarNodes = weightRadarNodes(rows);
        const lanes = [
          ["weightCoreCount", stats.core.count, stats.core.avg],
          ["weightWatchCount", stats.watch.count, stats.watch.avg],
          ["weightArchiveCount", stats.archive.count, stats.archive.avg],
        ];
        $("weightSnapshot").innerHTML = `
          <article class="weight-hero">
            <strong>${escapeHtml(stats.average)}</strong>
            <p>${escapeHtml(t("weightAverage"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.average)}%"></span></div>
            <span class="weight-command-title">${escapeHtml(t("weightCommandDeck"))}</span>
            <p>${escapeHtml(t("weightCommandHint"))}</p>
          </article>
          <div class="weight-radar-map" aria-label="${escapeHtml(t("weightRadarMap"))}">
            <div class="weight-radar-center">
              <strong>${escapeHtml(stats.core.count)}/${escapeHtml(stats.watch.count)}/${escapeHtml(stats.archive.count)}</strong>
              <span>${escapeHtml(t("weightRadarCore"))} · ${escapeHtml(t("weightRadarWatch"))} · ${escapeHtml(t("weightRadarArchive"))}</span>
            </div>
            ${radarNodes.map((entry) => `<div class="weight-radar-node" style="${escapeHtml(brandVisualStyle(entry))} --x: ${escapeHtml(entry.radar_x)}%; --y: ${escapeHtml(entry.radar_y)}%;">
              <b>${escapeHtml(entry.alias)}</b>
              <em>${escapeHtml(entry.brand_weight)}</em>
              <span>${escapeHtml(entry.sample_count)}/${escapeHtml(entry.target_samples)} ${escapeHtml(t("samples"))}</span>
            </div>`).join("")}
          </div>
          <div class="weight-command-panel">
            <div class="weight-metrics">
              <article class="weight-metric"><strong>${escapeHtml(stats.core.avg)}</strong><span>${escapeHtml(t("weightCoreAverage"))}</span></article>
              <article class="weight-metric"><strong>${escapeHtml(stats.evidencePercent)}%</strong><span>${escapeHtml(t("weightEvidenceCoverage"))}</span></article>
              <article class="weight-metric"><strong>${escapeHtml(stats.needsEvidence)}</strong><span>${escapeHtml(t("weightNeedsEvidence"))}</span></article>
              <article class="weight-metric"><strong>${escapeHtml(stats.total)}</strong><span>${escapeHtml(t("weightDistribution"))}</span></article>
            </div>
            <div class="weight-lanes">
              ${lanes.map(([label, count, avg]) => `<article class="weight-lane">
                <strong>${escapeHtml(count)}</strong>
                <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(avg)}%"></span></div>
                <span>${escapeHtml(t(label))}</span>
              </article>`).join("")}
              <p class="muted">${escapeHtml(t("weightTopGap"))}</p>
              <div class="weight-gaps">
                ${stats.gaps.length ? stats.gaps.map((entry) => `<article class="weight-gap-card">
                  <div>
                    <strong>${escapeHtml(entry.alias)}</strong>
                    <p>${escapeHtml(entry.name)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(entry.brand_weight)} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)}</p>
                  </div>
                  <button type="button" class="secondary" data-weight-sample="${escapeHtml(entry.alias)}">${escapeHtml(t("tuningAddSample"))}</button>
                </article>`).join("") : `<div class="row">${escapeHtml(t("weightNoGap"))}</div>`}
              </div>
            </div>
          </div>
        `;
      }

      function renderBrandWeightStrategy(rows) {
        const stats = weightStrategyStats(rows);
        const lanes = [
          ["strategyCoreLane", stats.core],
          ["strategyWatchLane", stats.watch],
          ["strategyArchiveLane", stats.archive],
        ];
        const moves = brandWeightStrategyMoves(rows);
        $("brandWeightStrategy").innerHTML = `
          <article class="strategy-brief">
            <strong>${escapeHtml(stats.heat)}</strong>
            <p>${escapeHtml(t("strategyHeat"))} · ${escapeHtml(t("strategyAvgWeight"))} ${escapeHtml(stats.average)}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.heat)}%"></span></div>
            <div class="coverage-stats">
              <article class="coverage-stat"><strong>${escapeHtml(stats.actionable)}</strong><span class="muted">${escapeHtml(t("strategyActionable"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(stats.coverage)}%</strong><span class="muted">${escapeHtml(t("strategyCoverage"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(stats.total)}</strong><span class="muted">${escapeHtml(t("weightDistribution"))}</span></article>
            </div>
          </article>
          <div class="strategy-lanes">
            ${lanes.map(([label, lane]) => `<article class="strategy-lane">
              <strong>${escapeHtml(lane.count)}</strong>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(lane.avg)}%"></span></div>
              <span class="muted">${escapeHtml(t(label))}</span>
            </article>`).join("")}
          </div>
          <div class="strategy-list">
            <p class="muted">${escapeHtml(t("strategyNextMoves"))}</p>
            ${moves.length ? moves.map((move) => `<article class="strategy-card">
              <header>
                <div>
                  <strong>${escapeHtml(move.alias)}</strong>
                  <p>${escapeHtml(move.name)}</p>
                </div>
                <span class="pill ${strategyPill(move.action)}">${escapeHtml(t(move.label))}</span>
              </header>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(move.priority_score)}%"></span></div>
              <div class="score-breakdown">
                ${profileBar(t("weightLabel"), move.brand_weight, 100)}
                ${profileBar(t("strategyTarget"), move.target_weight, 100)}
              </div>
              <p>${escapeHtml(t(move.reason))} · ${escapeHtml(t("samples"))} ${escapeHtml(move.sample_count)} · ${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(move.avg_premium_rate))}</p>
            </article>`).join("") : `<div class="row">${escapeHtml(t("strategyNoMoves"))}</div>`}
          </div>
        `;
      }

      function renderBrandWeightProfile(rows) {
        const visible = [...rows]
          .sort((a, b) => (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0) || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0))
          .slice(0, 9);
        $("brandWeightProfile").innerHTML = visible.length ? visible.map((entry) => {
          const keywords = (entry.market_keywords || []).slice(0, 4);
          return `<article class="profile-card" style="${escapeHtml(brandVisualStyle(entry))}">
            <header>
              <div>
                <strong>${escapeHtml(entry.alias)}</strong>
                <p class="muted">${escapeHtml(entry.name)}</p>
              </div>
              <span class="pill ${profilePill(entry)}">${escapeHtml(valueLabel("weightRole", entry.weight_role))}</span>
            </header>
            <div class="profile-score">
              <strong>${escapeHtml(entry.brand_weight)}</strong>
              <div>
                <p class="muted">${escapeHtml(t("priorityScore"))} ${escapeHtml(entry.priority_score)} · ${escapeHtml(valueLabel("evidenceLevel", entry.evidence_level))}</p>
                <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(entry.priority_score)}%"></span></div>
              </div>
            </div>
            <div class="profile-bars">
              ${profileBar(t("profileWeight"), entry.brand_weight, 100)}
              ${profileBar(t("profileHeat"), entry.score_breakdown?.premium_points || 0, 55)}
              ${profileBar(t("profileEvidence"), entry.evidence_score || 0, 100)}
            </div>
            ${brandIdentityHtml(entry)}
            <p class="muted">${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)} · ${escapeHtml(tierLabel(entry.tier))}</p>
            <div class="profile-keywords" aria-label="${escapeHtml(t("profileKeywords"))}">
              ${keywords.length ? keywords.map((keyword) => `<span>${escapeHtml(keyword)}</span>`).join("") : `<span>${escapeHtml(t("profileNoKeywords"))}</span>`}
            </div>
          </article>`;
        }).join("") : `<div class="row">${escapeHtml(t("noBrandProfile"))}</div>`;
      }

      function renderBrandWeightGuardrails(rows) {
        const allRisks = brandWeightGuardrailRows(rows, Number.POSITIVE_INFINITY);
        const risks = allRisks.slice(0, 6);
        const stats = brandWeightGuardrailStats(allRisks, rows);
        $("brandWeightGuardrails").innerHTML = `
          <article class="guardrail-brief">
            <strong>${escapeHtml(stats.riskScore)}</strong>
            <p>${escapeHtml(t("guardrailRiskScore"))} · ${escapeHtml(stats.open)} ${escapeHtml(t("guardrailOpen"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.riskScore)}%"></span></div>
            <div class="guardrail-stats">
              <article class="guardrail-stat"><strong>${escapeHtml(stats.critical)}</strong><span>${escapeHtml(t("guardrailCritical"))}</span></article>
              <article class="guardrail-stat"><strong>${escapeHtml(stats.watch)}</strong><span>${escapeHtml(t("guardrailWatch"))}</span></article>
              <article class="guardrail-stat"><strong>${escapeHtml(stats.avgConfidence)}%</strong><span>${escapeHtml(t("guardrailAvgConfidence"))}</span></article>
              <article class="guardrail-stat"><strong>${escapeHtml(stats.coverage)}%</strong><span>${escapeHtml(t("guardrailCoverage"))}</span></article>
            </div>
          </article>
          <div class="guardrail-lanes">
            ${guardrailLaneRows(allRisks, rows).map((lane) => `<article class="guardrail-lane">
              <strong>${escapeHtml(lane.count)}</strong>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(lane.score)}%"></span></div>
              <span class="muted">${escapeHtml(t(lane.label))}</span>
            </article>`).join("")}
          </div>
          <div class="guardrail-list">
            ${risks.length ? risks.map(brandWeightGuardrailHtml).join("") : `<div class="row">${escapeHtml(t("guardrailNoRows"))}</div>`}
          </div>
        `;
      }

      function brandWeightGuardrailRows(rows, limit = 6) {
        const formulaByAlias = new Map(buildBrandWeightFormula(rows, Number.POSITIVE_INFINITY).map((entry) => [entry.alias, entry]));
        return (rows || []).map((entry) => {
          const weight = Number(entry.brand_weight) || 0;
          const sampleCount = Number(entry.sample_count) || 0;
          const premium = Number(entry.avg_premium_rate) || 0;
          const formula = formulaByAlias.get(entry.alias) || {};
          let guardrail = null;
          if (sampleCount < 2 && weight >= 90) {
            guardrail = { label: "guardrailCoreGap", reason: "guardrailReasonCoreGap", severity: "critical", rank: 5, target_weight: weight };
          } else if (sampleCount >= 2 && premium >= 0.35 && weight < 85) {
            guardrail = { label: "guardrailUnderweighted", reason: "guardrailReasonUnderweighted", severity: "critical", rank: 4, target_weight: Math.max(Number(formula.target_weight) || weight, Math.min(90, weight + 10)) };
          } else if (sampleCount >= 2 && premium < -0.05 && weight >= 80) {
            guardrail = { label: "guardrailOverweighted", reason: "guardrailReasonOverweighted", severity: "watch", rank: 3, target_weight: Math.min(Number(formula.target_weight) || weight, Math.max(60, weight - 10)) };
          } else if (sampleCount >= 2 && premium >= 0.25 && weight < 70) {
            guardrail = { label: "guardrailArchiveHot", reason: "guardrailReasonArchiveHot", severity: "watch", rank: 2, target_weight: Math.max(70, Number(formula.target_weight) || weight) };
          }
          if (!guardrail) return null;
          const confidence = Number(formula.confidence) || formulaConfidence(entry);
          const targetWeight = clampScore(guardrail.target_weight);
          return {
            ...entry,
            ...guardrail,
            confidence,
            target_weight: targetWeight,
            risk_score: clampScore(Math.round((Number(guardrail.rank) || 0) * 18 + (100 - confidence) * .25 + Math.max(0, 2 - sampleCount) * 8)),
          };
        }).filter(Boolean)
          .sort((a, b) => (
            (Number(b.rank) || 0) - (Number(a.rank) || 0)
            || (Number(b.risk_score) || 0) - (Number(a.risk_score) || 0)
            || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
          ))
          .slice(0, limit);
      }

      function brandWeightGuardrailStats(risks, rows) {
        const total = (rows || []).length || 0;
        const open = (risks || []).length;
        const critical = risks.filter((entry) => entry.severity === "critical").length;
        const watch = risks.filter((entry) => entry.severity === "watch").length;
        const avgConfidence = open ? Math.round(risks.reduce((sum, entry) => sum + (Number(entry.confidence) || 0), 0) / open) : 100;
        const coverage = total ? Math.round((rows || []).filter((entry) => Number(entry.sample_count) >= 2).length / total * 100) : 0;
        return {
          open,
          critical,
          watch,
          avgConfidence,
          coverage,
          riskScore: clampScore(Math.round(critical * 28 + watch * 14 + Math.max(0, 70 - avgConfidence) * .4)),
        };
      }

      function guardrailLaneRows(risks, rows) {
        const counts = countBy(risks || [], "severity");
        const total = (rows || []).length || 0;
        const stable = Math.max(0, total - (risks || []).length);
        return [
          { label: "guardrailCritical", count: counts.critical || 0, score: Math.min(100, (counts.critical || 0) * 34) },
          { label: "guardrailWatch", count: counts.watch || 0, score: Math.min(100, (counts.watch || 0) * 24) },
          { label: "guardrailStable", count: stable, score: total ? Math.round(stable / total * 100) : 0 },
        ];
      }

      function brandWeightGuardrailHtml(entry) {
        return `<article class="guardrail-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)} · ${escapeHtml(entry.name)}</strong>
              <p>${escapeHtml(t(entry.label))} · ${escapeHtml(t(entry.reason))}</p>
            </div>
            <span class="pill ${guardrailPill(entry.severity)}">${escapeHtml(t(entry.severity === "critical" ? "guardrailCritical" : "guardrailWatch"))}</span>
          </header>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(entry.risk_score)}%"></span></div>
          <div class="score-breakdown">
            ${profileBar(t("weightLabel"), entry.brand_weight, 100)}
            ${profileBar(t("guardrailTarget"), entry.target_weight, 100)}
            ${profileBar(t("formulaConfidence"), entry.confidence, 100)}
          </div>
          <p>${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)} · ${escapeHtml(tierLabel(entry.tier))}</p>
          <div class="guardrail-actions">
            ${Number(entry.sample_count) < 2 ? `<button type="button" class="secondary" data-guardrail-sample="${escapeHtml(entry.alias)}">${escapeHtml(t("guardrailActionSample"))}</button>` : ""}
            ${Number(entry.target_weight) !== Number(entry.brand_weight) ? `<button type="button" class="secondary" data-guardrail-apply="${escapeHtml(entry.alias)}" data-guardrail-target="${escapeHtml(entry.target_weight)}">${escapeHtml(t("guardrailActionApply"))}</button>` : ""}
          </div>
        </article>`;
      }

      function guardrailPill(severity) {
        return severity === "critical" ? "rose" : "gold";
      }

      function profileBar(label, value, max) {
        const number = Number(value) || 0;
        const width = Math.min(100, Math.round(number / max * 100));
        return `<div class="profile-row">
          <span>${escapeHtml(label)}</span>
          <div class="score-track" aria-hidden="true"><span style="--score: ${escapeHtml(width)}%"></span></div>
          <span>${escapeHtml(number)}</span>
        </div>`;
      }

      function profilePill(entry) {
        if (entry.weight_role === "release_priority") return "rose";
        if (entry.evidence_level === "missing") return "gold";
        if (entry.evidence_level === "ready") return "";
        return "off";
      }

      function weightSnapshotStats(rows) {
        const total = rows.length || 0;
        const average = total ? Math.round(rows.reduce((sum, row) => sum + (Number(row.brand_weight) || 0), 0) / total) : 0;
        const group = (predicate) => {
          const matches = rows.filter(predicate);
          const avg = matches.length ? Math.round(matches.reduce((sum, row) => sum + (Number(row.brand_weight) || 0), 0) / matches.length) : 0;
          return { count: matches.length, avg };
        };
        const evidenceRows = rows.filter((entry) => Number(entry.sample_count) >= 2);
        const gaps = rows
          .filter((entry) => Number(entry.sample_count) < 2)
          .sort((a, b) => (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0))
          .slice(0, 3);
        return {
          total,
          average,
          core: group((entry) => Number(entry.brand_weight) >= 90),
          watch: group((entry) => Number(entry.brand_weight) >= 70 && Number(entry.brand_weight) < 90),
          archive: group((entry) => Number(entry.brand_weight) < 70),
          evidencePercent: total ? Math.round((evidenceRows.length / total) * 100) : 0,
          needsEvidence: rows.filter((entry) => Number(entry.sample_count) < 2 && Number(entry.brand_weight) >= 70).length,
          gaps,
        };
      }

      function weightRadarNodes(rows) {
        const visible = [...(rows || [])]
          .sort((a, b) => (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0) || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0))
          .slice(0, 7);
        return visible.map((entry, index) => {
          const point = weightRadarPoint(index, visible.length, entry.brand_weight);
          return {
            ...entry,
            target_samples: sampleTarget(entry.brand_weight, entry.tier),
            radar_x: point.x,
            radar_y: point.y,
          };
        });
      }

      function weightRadarPoint(index, total, weight) {
        const count = Math.max(1, Number(total) || 1);
        const angle = -Math.PI / 2 + (index / count) * Math.PI * 2;
        const radius = 23 + clampScore(weight) * .16;
        return {
          x: Math.round((50 + Math.cos(angle) * radius) * 10) / 10,
          y: Math.round((50 + Math.sin(angle) * radius) * 10) / 10,
        };
      }

      function weightStrategyStats(rows) {
        const total = rows.length || 0;
        const average = total ? Math.round(rows.reduce((sum, row) => sum + (Number(row.brand_weight) || 0), 0) / total) : 0;
        const group = (predicate) => {
          const matches = rows.filter(predicate);
          const avg = matches.length ? Math.round(matches.reduce((sum, row) => sum + (Number(row.brand_weight) || 0), 0) / matches.length) : 0;
          return { count: matches.length, avg };
        };
        const coverage = total ? Math.round(rows.filter((entry) => Number(entry.sample_count) >= 2).length / total * 100) : 0;
        const moves = brandWeightStrategyMoves(rows, rows.length || 0);
        const actionable = moves.filter((move) => !["strategyHold", "strategyBaseline", "strategyMonitor"].includes(move.label)).length;
        return {
          total,
          average,
          coverage,
          actionable,
          heat: clampScore(Math.round((average * .45) + (coverage * .35) + (actionable * 8))),
          core: group((entry) => Number(entry.brand_weight) >= 90),
          watch: group((entry) => Number(entry.brand_weight) >= 70 && Number(entry.brand_weight) < 90),
          archive: group((entry) => Number(entry.brand_weight) < 70),
        };
      }

      function brandWeightStrategyMoves(rows, limit = 5) {
        const moves = (rows || []).map((entry) => {
          const weight = Number(entry.brand_weight) || 0;
          const sampleCount = Number(entry.sample_count) || 0;
          const premium = Number(entry.avg_premium_rate) || 0;
          let move = { label: "strategyMonitor", reason: "strategyReasonMonitor", target_weight: weight, rank: 1 };
          if (sampleCount < 2 && weight >= 85) {
            move = { label: "strategyCollect", reason: "strategyReasonCoreGap", target_weight: weight, rank: 5 };
          } else if (sampleCount >= 2 && premium >= 0.35 && weight < 90) {
            move = { label: "strategyRaise", reason: "strategyReasonPremiumRaise", target_weight: Math.min(90, Math.max(70, weight + 10)), rank: 4 };
          } else if (sampleCount >= 2 && premium < -0.05 && weight >= 70) {
            move = { label: "strategyCooldown", reason: "strategyReasonDiscountCool", target_weight: Math.max(60, weight - 10), rank: 3 };
          } else if (sampleCount >= 5 && premium >= 0.25) {
            move = { label: "strategyHold", reason: "strategyReasonHoldCore", target_weight: weight, rank: 2 };
          } else if (sampleCount < 2 && weight < 70) {
            move = { label: "strategyBaseline", reason: "strategyReasonArchiveGap", target_weight: weight, rank: 1 };
          }
          return {
            ...entry,
            ...move,
            action: move.label.replace("strategy", "").toLowerCase(),
          };
        }).sort((a, b) => (
          (Number(b.rank) || 0) - (Number(a.rank) || 0)
          || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0)
          || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
        ));
        return moves.slice(0, limit);
      }

      function renderWeightTrajectory(rows) {
        const allTracks = buildWeightTrajectory(rows, Array.isArray(rows) ? rows.length : 0);
        const tracks = allTracks.slice(0, 7);
        const stats = weightTrajectoryStats(allTracks);
        $("weightTrajectory").innerHTML = `
          <article class="trajectory-brief">
            <strong>${escapeHtml(stats.changed)}</strong>
            <p>${escapeHtml(t("trajectoryChanged"))} · ${escapeHtml(stats.stable)} ${escapeHtml(t("trajectoryStable"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.changeRate)}%"></span></div>
            <div class="coverage-stats">
              <article class="coverage-stat"><strong>${escapeHtml(stats.avgTarget)}</strong><span class="muted">${escapeHtml(t("trajectoryAvgTarget"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(stats.avgShift)}</strong><span class="muted">${escapeHtml(t("trajectoryAvgShift"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(stats.total)}</strong><span class="muted">${escapeHtml(t("weightDistribution"))}</span></article>
            </div>
          </article>
          <div class="trajectory-list">
            ${tracks.length ? tracks.map((entry) => `<article class="trajectory-card" style="${escapeHtml(brandVisualStyle(entry))}">
              <header>
                <div>
                  <strong>${escapeHtml(entry.alias)}</strong>
                  <p>${escapeHtml(entry.name)}</p>
                </div>
                <span class="pill ${trajectoryPill(entry.direction)}">${escapeHtml(t(entry.label))}</span>
              </header>
              <div class="trajectory-path">
                <div class="trajectory-node"><strong>${escapeHtml(entry.brand_weight)}</strong><span>${escapeHtml(t("trajectoryCurrent"))}</span></div>
                <div class="trajectory-line" aria-hidden="true"><span style="--score: ${escapeHtml(entry.path_score)}%"></span></div>
                <div class="trajectory-node"><strong>${escapeHtml(entry.target_weight)}</strong><span>${escapeHtml(t("trajectoryTarget"))}</span></div>
              </div>
              <p>${escapeHtml(t("formulaConfidence"))} ${escapeHtml(entry.confidence)}% · ${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)}</p>
              <div class="trajectory-actions">
                ${entry.delta ? `<button type="button" class="secondary" data-trajectory-apply="${escapeHtml(entry.alias)}" data-trajectory-target="${escapeHtml(entry.target_weight)}">${escapeHtml(t("trajectoryApply"))}</button>` : ""}
                ${Number(entry.sample_count) < 2 ? `<button type="button" class="secondary" data-trajectory-sample="${escapeHtml(entry.alias)}">${escapeHtml(t("trajectorySample"))}</button>` : ""}
              </div>
            </article>`).join("") : `<div class="row">${escapeHtml(t("trajectoryNoRows"))}</div>`}
          </div>
        `;
      }

      function buildWeightTrajectory(rows, limit = 7) {
        return buildBrandWeightFormula(rows, Array.isArray(rows) ? rows.length : 0).map((entry) => {
          const delta = Number(entry.delta) || 0;
          const sampleCount = Number(entry.sample_count) || 0;
          let direction = "aligned";
          let label = "trajectoryAligned";
          if (sampleCount < 2 && Number(entry.brand_weight) >= 70) {
            direction = "collect";
            label = "trajectoryCollect";
          } else if (delta > 0) {
            direction = "raise";
            label = "trajectoryRaise";
          } else if (delta < 0) {
            direction = "lower";
            label = "trajectoryLower";
          }
          return {
            ...entry,
            direction,
            label,
            path_score: Math.max(4, Math.min(100, Math.max(Number(entry.brand_weight) || 0, Number(entry.target_weight) || 0))),
          };
        }).sort((a, b) => (
          trajectoryRank(b.direction) - trajectoryRank(a.direction)
          || Math.abs(Number(b.delta) || 0) - Math.abs(Number(a.delta) || 0)
          || (Number(b.confidence) || 0) - (Number(a.confidence) || 0)
          || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0)
        )).slice(0, limit);
      }

      function weightTrajectoryStats(rows) {
        const total = rows.length || 0;
        const changed = rows.filter((entry) => Number(entry.delta) !== 0).length;
        const avgTarget = total ? Math.round(rows.reduce((sum, row) => sum + (Number(row.target_weight) || 0), 0) / total) : 0;
        const avgShift = total ? Math.round(rows.reduce((sum, row) => sum + Math.abs(Number(row.delta) || 0), 0) / total) : 0;
        return {
          total,
          changed,
          stable: Math.max(0, total - changed),
          avgTarget,
          avgShift,
          changeRate: total ? Math.round(changed / total * 100) : 0,
        };
      }

      function trajectoryRank(direction) {
        return { collect: 4, raise: 3, lower: 2, aligned: 1 }[direction] || 0;
      }

      function trajectoryPill(direction) {
        if (direction === "collect") return "gold";
        if (direction === "raise") return "rose";
        if (direction === "lower") return "warn";
        return "off";
      }

      function renderBrandWeightFormula(rows) {
        const formulas = buildBrandWeightFormula(rows);
        $("brandWeightFormula").innerHTML = formulas.length ? `
          ${formulaSummaryHtml(formulas)}
          ${formulas.map((entry) => `<article class="formula-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)}</strong>
              <p class="muted">${escapeHtml(entry.name)}</p>
            </div>
            <span class="pill ${formulaPill(entry.delta)}">${escapeHtml(t(formulaLabel(entry.delta)))}</span>
          </header>
          <div class="formula-score">
            <div>
              <strong>${escapeHtml(entry.target_weight)}</strong>
              <p class="muted">${escapeHtml(t("formulaTarget"))} · ${escapeHtml(formatDelta(entry.delta))}</p>
            </div>
            <div>
              <p class="muted">${escapeHtml(t("formulaConfidence"))} ${escapeHtml(entry.confidence)}%</p>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(entry.confidence)}%"></span></div>
            </div>
          </div>
          <div class="formula-parts">
            ${formulaPartBar(t("formulaBase"), entry.parts.base, 90)}
            ${formulaPartBar(t("formulaPremium"), entry.parts.premium, 16)}
            ${formulaPartBar(t("formulaEvidence"), entry.parts.evidence, 8)}
            ${formulaPartBar(t("formulaKeywords"), entry.parts.keywords, 4)}
            ${formulaPartBar(t("formulaWatchability"), entry.parts.watchability, 4)}
          </div>
          <p class="muted">${escapeHtml(t("weightLabel"))} ${escapeHtml(entry.brand_weight)} · ${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)}</p>
          ${entry.delta ? `<button type="button" class="secondary" data-formula-apply="${escapeHtml(entry.alias)}" data-formula-target="${escapeHtml(entry.target_weight)}">${escapeHtml(t("formulaApplyDraft"))}</button>` : ""}
        </article>`).join("")}
        ` : `<div class="row">${escapeHtml(t("formulaNoRows"))}</div>`;
      }

      function formulaSummaryHtml(formulas) {
        const stats = formulaContributionStats(formulas);
        return `<article class="formula-summary">
          <div>
            <h3>${escapeHtml(t("formulaSummary"))}</h3>
            <p>${escapeHtml(t("formulaSummaryHint"))}</p>
            <div class="formula-summary-stats">
              <span><strong>${escapeHtml(stats.avg_target)}</strong>${escapeHtml(t("formulaAvgTarget"))}</span>
              <span><strong>${escapeHtml(stats.avg_confidence)}%</strong>${escapeHtml(t("formulaAvgConfidence"))}</span>
              <span><strong>${escapeHtml(stats.collect_count)}</strong>${escapeHtml(t("formulaCollectCount"))}</span>
            </div>
          </div>
          <div class="formula-contrib-list">
            ${stats.parts.map((part) => `<div class="formula-contrib-row">
              <strong>${escapeHtml(t(part.label))}</strong>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(part.score)}%"></span></div>
              <span>${escapeHtml(part.value)}</span>
            </div>`).join("")}
            <p>${escapeHtml(t("formulaLeadMove"))} ${escapeHtml(stats.lead_move)}</p>
          </div>
        </article>`;
      }

      function formulaContributionStats(formulas) {
        const rows = formulas || [];
        const count = rows.length || 1;
        const avg = (key) => Math.round(rows.reduce((sum, row) => sum + (Number(row.parts?.[key]) || 0), 0) / count);
        const avgTarget = rows.length ? Math.round(rows.reduce((sum, row) => sum + (Number(row.target_weight) || 0), 0) / rows.length) : 0;
        const avgConfidence = rows.length ? Math.round(rows.reduce((sum, row) => sum + (Number(row.confidence) || 0), 0) / rows.length) : 0;
        const collectCount = rows.filter((row) => Number(row.sample_count) < 2).length;
        const lead = [...rows].sort((a, b) => Math.abs(Number(b.delta) || 0) - Math.abs(Number(a.delta) || 0))[0];
        const parts = [
          { key: "base", label: "formulaBase", max: 90 },
          { key: "premium", label: "formulaPremium", max: 16 },
          { key: "evidence", label: "formulaEvidence", max: 8 },
          { key: "keywords", label: "formulaKeywords", max: 4 },
          { key: "watchability", label: "formulaWatchability", max: 4 },
        ].map((part) => {
          const value = avg(part.key);
          return {
            ...part,
            value,
            score: clampScore(Math.max(0, value) / part.max * 100),
          };
        });
        return {
          avg_target: avgTarget,
          avg_confidence: avgConfidence,
          collect_count: collectCount,
          lead_move: lead ? `${lead.alias} ${formatDelta(lead.delta)}` : "-",
          parts,
        };
      }

      function buildBrandWeightFormula(rows, limit = 9) {
        return (rows || []).map((entry) => {
          const parts = brandWeightFormulaParts(entry);
          const rawTarget = parts.base + parts.premium + parts.evidence + parts.keywords + parts.watchability;
          const weight = Number(entry.brand_weight) || 0;
          const samples = Number(entry.sample_count) || 0;
          const isCore = entry.tier === "core" || weight >= 90;
          let target = clampScore(Math.round(rawTarget / 5) * 5);
          if (samples < 2 && target > weight) target = weight;
          if (samples < 2 && isCore && target < weight) target = weight;
          return {
            ...entry,
            parts,
            target_weight: target,
            delta: target - weight,
            confidence: formulaConfidence(entry),
          };
        }).sort((a, b) => (
          Math.abs(Number(b.delta) || 0) - Math.abs(Number(a.delta) || 0)
          || (Number(b.confidence) || 0) - (Number(a.confidence) || 0)
          || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
        )).slice(0, limit);
      }

      function brandWeightFormulaParts(entry) {
        const weight = Number(entry.brand_weight) || 0;
        const premium = Number(entry.avg_premium_rate) || 0;
        const samples = Number(entry.sample_count) || 0;
        const keywords = (entry.market_keywords || []).length;
        const watchLinks = (entry.watch_urls || []).length;
        const isCore = entry.tier === "core" || weight >= 90;
        const base = isCore ? 90 : entry.tier === "watch" || weight >= 70 ? 70 : 56;
        const premiumPart = premium >= 0.5 ? 12 : premium >= 0.25 ? 8 : premium >= 0.1 ? 4 : premium < -0.05 ? -8 : 0;
        const evidencePart = samples >= 5 ? 6 : samples >= 2 ? 3 : samples === 1 || isCore ? 0 : -4;
        return {
          base,
          premium: premiumPart,
          evidence: evidencePart,
          keywords: Math.min(4, keywords),
          watchability: Math.min(4, watchLinks),
        };
      }

      function formulaConfidence(entry) {
        const sampleScore = Math.min(60, (Number(entry.sample_count) || 0) * 12);
        const visualScore = entry.visual?.accent && entry.visual?.motif && entry.visual?.radar_cue ? 20 : 8;
        const watchScore = Math.min(20, (entry.watch_urls || []).length * 5);
        return clampScore(sampleScore + visualScore + watchScore);
      }

      function formulaPartBar(label, value, max) {
        const numeric = Number(value) || 0;
        const width = Math.min(100, Math.round(Math.abs(numeric) / max * 100));
        return `<div class="profile-row">
          <span>${escapeHtml(label)}</span>
          <div class="score-track" aria-hidden="true"><span style="--score: ${escapeHtml(width)}%"></span></div>
          <span>${escapeHtml(numeric > 0 ? `+${numeric}` : numeric)}</span>
        </div>`;
      }

      function formulaLabel(delta) {
        const value = Number(delta) || 0;
        if (value >= 5) return "formulaRaise";
        if (value <= -5) return "formulaLower";
        return "formulaAligned";
      }

      function formulaPill(delta) {
        const value = Number(delta) || 0;
        if (value >= 5) return "rose";
        if (value <= -5) return "warn";
        return "";
      }

      function strategyPill(action) {
        if (action === "collect") return "gold";
        if (action === "raise" || action === "hold") return "rose";
        if (action === "cooldown") return "warn";
        return "off";
      }

      function renderWeightTuning(rows) {
        const suggestions = buildWeightTuning(rows);
        syncTuningBatchControls(suggestions);
        $("weightTuning").innerHTML = suggestions.length ? suggestions.map((entry) => `<article class="tuning-card">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)}</strong>
              <p class="muted">${escapeHtml(entry.name)}</p>
            </div>
            <span class="pill ${tuningPill(entry.kind)}">${escapeHtml(t(entry.label))}</span>
          </header>
          <p class="muted">${escapeHtml(t("tuningTarget"))} ${escapeHtml(entry.target_weight)} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)} · ${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))}</p>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${Number(entry.priority_score) || 0}%"></span></div>
          <p class="muted">${escapeHtml(t("tuningReason"))} · ${escapeHtml(t(entry.reason))}</p>
          ${tuningActionHtml(entry)}
        </article>`).join("") : `<div class="row">${escapeHtml(t("noWeightTuning"))}</div>`;
      }

      function syncTuningBatchControls(suggestions) {
        const actionable = actionableTuningSuggestions(suggestions);
        const button = $("applyTuningBatchBtn");
        const summary = $("tuningBatchSummary");
        if (summary) summary.textContent = actionable.length ? `${actionable.length} ${t("tuningBatchReady")}` : t("tuningBatchEmpty");
        if (button) {
          button.dataset.disabled = actionable.length ? "false" : "true";
          button.disabled = !actionable.length;
        }
      }

      function actionableTuningSuggestions(suggestions) {
        return (suggestions || []).filter((entry) => Number(entry.target_weight) !== Number(entry.brand_weight));
      }

      function tuningActionHtml(entry) {
        if (entry.kind === "collect" || entry.kind === "baseline") {
          return `<button type="button" class="secondary" data-tuning-sample="${escapeHtml(entry.alias)}">${escapeHtml(t("tuningAddSample"))}</button>`;
        }
        if (Number(entry.target_weight) !== Number(entry.brand_weight)) {
          return `<button type="button" class="secondary" data-tuning-apply="${escapeHtml(entry.alias)}" data-tuning-target="${escapeHtml(entry.target_weight)}">${escapeHtml(t("tuningApplyDraft"))}</button>`;
        }
        return "";
      }

      function buildWeightTuning(rows) {
        return rows.map((entry) => {
          const suggestion = tuningSuggestion(entry);
          return { ...entry, ...suggestion };
        }).sort((a, b) => (
          tuningRank(b.kind) - tuningRank(a.kind)
          || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0)
          || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
        )).slice(0, 6);
      }

      function tuningSuggestion(entry) {
        const weight = Number(entry.brand_weight) || 0;
        const sampleCount = Number(entry.sample_count) || 0;
        const premium = Number(entry.avg_premium_rate) || 0;
        if (sampleCount < 2 && weight >= 70) {
          return { kind: "collect", label: "tuningCollect", reason: "tuningCollectReason", target_weight: weight };
        }
        if (sampleCount >= 2 && premium >= 0.25 && weight < 90) {
          return { kind: "raise", label: "tuningRaise", reason: "tuningRaiseReason", target_weight: premium >= 0.5 ? 90 : Math.min(90, Math.max(70, weight + 10)) };
        }
        if (sampleCount >= 2 && premium < 0 && weight > 70) {
          return { kind: "cool", label: "tuningCool", reason: "tuningCoolReason", target_weight: Math.max(60, weight - 10) };
        }
        if (weight < 70 && sampleCount < 2) {
          return { kind: "baseline", label: "tuningBaseline", reason: "tuningBaselineReason", target_weight: weight };
        }
        return { kind: "hold", label: "tuningHold", reason: "tuningHoldReason", target_weight: weight };
      }

      function tuningRank(kind) {
        return { raise: 5, collect: 4, cool: 3, hold: 2, baseline: 1 }[kind] || 0;
      }

      function tuningPill(kind) {
        if (kind === "raise") return "rose";
        if (kind === "collect") return "gold";
        if (kind === "cool") return "warn";
        if (kind === "baseline") return "off";
        return "";
      }

      function syncMatrixControls() {
        document.querySelectorAll("[data-matrix-filter]").forEach((button) => {
          const active = button.dataset.matrixFilter === activeMatrixFilter;
          button.classList.toggle("active", active);
          button.setAttribute("aria-pressed", active ? "true" : "false");
        });
        const sort = $("matrixSort");
        if (sort && sort.value !== activeMatrixSort) sort.value = activeMatrixSort;
      }

      function sortMatrixRows(rows) {
        const sorted = [...rows];
        const sorters = {
          score: (row) => Number(row.priority_score) || 0,
          premium: (row) => Number(row.avg_premium_rate) || 0,
          weight: (row) => Number(row.brand_weight) || 0,
          samples: (row) => Number(row.sample_count) || 0,
          delta: (row) => Math.abs(Number(row.score_delta) || 0),
        };
        const sorter = sorters[activeMatrixSort] || sorters.score;
        return sorted.sort((a, b) => (
          sorter(b) - sorter(a)
          || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0)
          || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
          || String(a.alias).localeCompare(String(b.alias))
        ));
      }

      function filterMatrixRows(rows) {
        if (activeMatrixFilter === "focus") return rows.filter((entry) => isFocusBrand(entry));
        if (activeMatrixFilter === "lead") return rows.filter((entry) => entry.band === "lead" || entry.band === "watch");
        if (activeMatrixFilter === "needs_samples") return rows.filter((entry) => (entry.reason_codes || []).includes("needs_samples"));
        if (activeMatrixFilter === "core") return rows.filter((entry) => entry.tier === "core" || Number(entry.brand_weight) >= 90);
        return rows;
      }

      function isFocusBrand(entry) {
        return entry.tier === "core"
          || Number(entry.brand_weight) >= 90
          || Number(entry.sample_count) > 0
          || Number(entry.avg_premium_rate) >= 0.25;
      }

      function renderScoreBreakdown(breakdown = {}) {
        const rows = [
          [t("premiumPoints"), breakdown.premium_points || 0, 55],
          [t("brandPoints"), breakdown.brand_points || 0, 40],
          [t("samplePoints"), breakdown.sample_points || 0, 10],
        ];
        return `<div class="score-breakdown">${rows.map(([label, value, max]) => `<div class="score-row">
          <span>${escapeHtml(label)}</span>
          <div class="score-track" aria-hidden="true"><span style="--score: ${Math.min(100, Math.round((Number(value) || 0) / max * 100))}%"></span></div>
          <span>${escapeHtml(value)}</span>
        </div>`).join("")}</div>`;
      }

      function renderOpportunitySummary(opportunities) {
        const counts = countBy(opportunities, "band");
        const bands = ["lead", "watch", "collect_samples", "cooldown"];
        $("opportunitySummary").innerHTML = bands.map((band) => `<span class="summary-chip">
          <strong>${escapeHtml(counts[band] || 0)}</strong>
          <span>${escapeHtml(valueLabel("opportunityBand", band))}</span>
        </span>`).join("");
      }

      function syncOpportunityFilterButtons() {
        document.querySelectorAll("[data-opportunity-filter]").forEach((button) => {
          const active = button.dataset.opportunityFilter === activeOpportunityFilter;
          button.classList.toggle("active", active);
          button.setAttribute("aria-pressed", active ? "true" : "false");
        });
      }

      function renderMarketSignal(events, items) {
        const statusCounts = countBy(items, "status");
        const preorder = statusCounts.preorder || 0;
        const restock = statusCounts.restock || 0;
        const newArrival = statusCounts.new_arrival || 0;
        const score = Math.min(100, Math.round((preorder * 30) + (restock * 24) + (newArrival * 16) + Math.min(events.length, 20)));
        $("signalBar").style.setProperty("--score", `${score}%`);
        $("signalSummary").textContent = `${t("signalSummary")} · ${score}/100`;
        const entries = Object.entries(statusCounts);
        $("statusMix").innerHTML = entries.length ? entries.map(([status, count]) => `<article class="status-card">
          <header>
            <strong>${escapeHtml(valueLabel("status", status))}</strong>
            <span class="status-count">${escapeHtml(count)}</span>
          </header>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${Math.min(100, count * 18)}%"></span></div>
        </article>`).join("") : `<div class="row">${escapeHtml(t("noStatus"))}</div>`;
      }

      function renderMarketPremium(market) {
        const summary = market.summary || {};
        const brands = summary.brands || [];
        const records = summary.records || [];
        syncPremiumBrandFilter(brands);
        const visibleRecords = filterPremiumRecords(records);
        syncPremiumRecordFilters(records);
        syncPremiumExportButton(visibleRecords);
        $("marketCount").textContent = `${summary.sample_count || 0} ${t("samples")}`;
        $("premiumBrands").innerHTML = brands.length ? brands.map((brand) => `<article class="market-card">
          <header>
            <strong>${escapeHtml(brand.brand_alias)}</strong>
            <div>
              <span class="premium-rate">${formatPercent(brand.avg_premium_rate)}</span>
              <span class="pill ${premiumBandPill(brand.premium_band)}">${escapeHtml(valueLabel("premiumBand", brand.premium_band))}</span>
            </div>
          </header>
          <p class="muted">${escapeHtml(t("priorityScore"))} ${escapeHtml(brand.priority_score)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(brand.brand_weight)}</p>
          ${renderPriceCorridor(brand)}
          <p class="muted">${escapeHtml(t("samples"))} ${escapeHtml(brand.sample_count)} · ${escapeHtml(t("maxPremium"))} ${escapeHtml(formatPercent(brand.max_premium_rate))} · ${escapeHtml(t("avgSpread"))} ${escapeHtml(formatMoney(brand.avg_spread, brand.currency))}</p>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${Number(brand.priority_score) || premiumWidth(brand.avg_premium_rate)}%"></span></div>
        </article>`).join("") : `<div class="row">${escapeHtml(t("noMarket"))}</div>`;
        $("premiumRecords").innerHTML = visibleRecords.length ? visibleRecords.map((record) => `<article class="market-card">
          <header>
            <strong>${escapeHtml(record.brand_alias)} · ${escapeHtml(record.item_name)}</strong>
            <div>
              <span class="premium-rate">${formatPercent(record.premium_rate)}</span>
              <span class="pill ${premiumBandPill(record.premium_band)}">${escapeHtml(valueLabel("premiumBand", record.premium_band))}</span>
            </div>
          </header>
          <p class="muted">${escapeHtml(t("priorityScore"))} ${escapeHtml(record.priority_score)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(record.brand_weight)}</p>
          <p class="muted">${escapeHtml(t("qualityScore"))} ${escapeHtml(record.quality_score || 0)}</p>
          <p class="muted">${escapeHtml(t("retailPrice"))} ${formatMoney(record.retail_price, record.currency)} · ${escapeHtml(t("resalePrice"))} ${formatMoney(record.resale_price, record.currency)}</p>
          <p class="muted">${escapeHtml([record.condition, record.source, record.observed_at].filter(Boolean).join(" · "))}</p>
        </article>`).join("") : `<div class="row">${escapeHtml(t("noMarket"))}</div>`;
      }

      function renderPriceCorridor(brand) {
        return `<div class="price-corridor" aria-label="${escapeHtml(t("priceCorridor"))}">
          ${priceCorridorRow(t("retailRange"), brand.avg_retail_price, brand.min_retail_price, brand.max_retail_price, brand.currency, brand.avg_retail_price)}
          ${priceCorridorRow(t("resaleRange"), brand.avg_resale_price, brand.min_resale_price, brand.max_resale_price, brand.currency, brand.avg_resale_price)}
        </div>`;
      }

      function priceCorridorRow(label, average, min, max, currency, widthValue) {
        const range = Number(min) === Number(max)
          ? formatMoney(average, currency)
          : `${formatMoney(min, currency)}-${formatMoney(max, currency)}`;
        return `<div class="price-corridor-row">
          <span>${escapeHtml(label)}</span>
          <div class="price-corridor-track" aria-hidden="true"><span style="--score: ${escapeHtml(priceCorridorWidth(widthValue, max))}%"></span></div>
          <span>${escapeHtml(range)}</span>
        </div>`;
      }

      function priceCorridorWidth(value, max) {
        const denominator = Math.max(Number(max) || 0, Number(value) || 0, 1);
        return Math.max(6, Math.min(100, Math.round((Number(value) || 0) / denominator * 100)));
      }

      function syncPremiumExportButton(records) {
        const button = $("exportPremiumCsvBtn");
        const hasRecords = (records || []).length > 0;
        button.dataset.disabled = hasRecords ? "false" : "true";
        button.disabled = !hasRecords;
      }

      function filterPremiumRecords(records) {
        return (records || []).filter((record) => {
          const bandMatch = activePremiumFilter === "all" || record.premium_band === activePremiumFilter;
          const brandMatch = activePremiumBrandFilter === "all" || normalizeAlias(record.brand_alias) === activePremiumBrandFilter;
          return bandMatch && brandMatch;
        });
      }

      function syncPremiumBrandFilter(brands = []) {
        const select = $("premiumBrandFilter");
        const aliases = [...new Set((brands || []).map((brand) => normalizeAlias(brand.brand_alias)).filter(Boolean))];
        if (activePremiumBrandFilter !== "all" && !aliases.includes(activePremiumBrandFilter)) activePremiumBrandFilter = "all";
        select.innerHTML = [
          `<option value="all">${escapeHtml(t("premiumBrandAll"))}</option>`,
          ...aliases.map((alias) => `<option value="${escapeHtml(alias)}">${escapeHtml(alias)}</option>`),
        ].join("");
        select.value = activePremiumBrandFilter;
      }

      function syncPremiumRecordFilters(records = []) {
        const scopedRecords = activePremiumBrandFilter === "all"
          ? (records || [])
          : (records || []).filter((record) => normalizeAlias(record.brand_alias) === activePremiumBrandFilter);
        const counts = countBy(scopedRecords, "premium_band");
        document.querySelectorAll("[data-premium-filter]").forEach((button) => {
          const filter = button.dataset.premiumFilter || "all";
          const active = filter === activePremiumFilter;
          button.classList.toggle("active", active);
          button.setAttribute("aria-pressed", active ? "true" : "false");
          const label = t(filter === "all" ? "premiumFilterAll" : premiumBandLabelKey(filter));
          const count = filter === "all" ? Object.values(counts).reduce((sum, value) => sum + (Number(value) || 0), 0) : Number(counts[filter]) || 0;
          button.textContent = `${label} ${count}`;
        });
      }

      function exportPremiumCsv() {
        const records = filterPremiumRecords(currentState?.market?.summary?.records || []);
        if (!records.length) {
          toast(t("noPremiumCsv"));
          return;
        }
        const csv = csvFromPremiumRecords(records);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = premiumCsvFilename();
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedPremiumCsv"));
      }

      function exportBrandWeightsCsv() {
        const rows = buildBrandRadarMatrix();
        if (!rows.length) {
          toast(t("noWeightsCsv"));
          return;
        }
        const csv = csvFromBrandWeights(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-brand-weights.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedWeightsCsv"));
      }

      function exportBrandWeightScorecardsCsv() {
        const rows = brandWeightScorecardRows(buildBrandRadarMatrix(), Number.POSITIVE_INFINITY);
        if (!rows.length) {
          toast(t("noScorecardsCsv"));
          return;
        }
        const csv = csvFromBrandWeightScorecards(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-weight-scorecards.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedScorecardsCsv"));
      }

      function exportBrandWeightGuardrailsCsv() {
        const rows = brandWeightGuardrailRows(buildBrandRadarMatrix(), Number.POSITIVE_INFINITY);
        if (!rows.length) {
          toast(t("noGuardrailsCsv"));
          return;
        }
        const csv = csvFromBrandWeightGuardrails(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-weight-guardrails.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedGuardrailsCsv"));
      }

      function exportWeightScenariosCsv() {
        const rows = weightScenarioCsvRows(buildBrandRadarMatrix());
        if (!rows.length) {
          toast(t("noScenariosCsv"));
          return;
        }
        const csv = csvFromWeightScenarios(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-weight-scenarios.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedScenariosCsv"));
      }

      function exportDailyRadarCsv() {
        const rows = dailyRadarActions(buildBrandRadarMatrix());
        if (!rows.length) {
          toast(t("noDailyCsv"));
          return;
        }
        const csv = csvFromDailyRadarActions(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-daily-radar.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedDailyCsv"));
      }

      function exportCrownCsv() {
        const rows = brandCrownRows(buildBrandRadarMatrix());
        if (!rows.length) {
          toast(t("noCrownCsv"));
          return;
        }
        const csv = csvFromCrownRows(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-brand-crown.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedCrownCsv"));
      }

      function exportSamplePlanCsv() {
        const rows = buildSamplePlanRows(buildBrandRadarMatrix());
        if (!rows.length) {
          toast(t("noSamplePlanCsv"));
          return;
        }
        const csv = csvFromSamplePlanRows(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-sample-plan.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedSamplePlanCsv"));
      }

      function exportPremiumSeedsCsv() {
        const rows = premiumSeedRows(buildBrandRadarMatrix());
        if (!rows.length) {
          toast(t("noPremiumSeedsCsv"));
          return;
        }
        const csv = csvFromPremiumSeedRows(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-premium-seeds.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedPremiumSeedsCsv"));
      }

      function exportCoreWatchCsv() {
        const rows = coreMarketWatchRows(buildBrandRadarMatrix());
        if (!rows.length) {
          toast(t("noCoreWatchCsv"));
          return;
        }
        const csv = csvFromCoreWatchRows(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-core-watch.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedCoreWatchCsv"));
      }

      function exportMarketActionsCsv() {
        const rows = marketActions(currentState?.market?.patterns || []);
        if (!rows.length) {
          toast(t("noMarketActionsCsv"));
          return;
        }
        const csv = csvFromMarketActionRows(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-market-actions.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedMarketActionsCsv"));
      }

      function exportRunSheetCsv() {
        const rows = resaleRunSheetRows(buildBrandRadarMatrix());
        if (!rows.length) {
          toast(t("noRunSheetCsv"));
          return;
        }
        const csv = csvFromRunSheetRows(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-run-sheet.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedRunSheetCsv"));
      }

      function exportReleaseWatchCsv() {
        const rows = releaseWatchRows(buildBrandRadarMatrix());
        if (!rows.length) {
          toast(t("noReleaseWatchCsv"));
          return;
        }
        const csv = csvFromReleaseWatchRows(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-release-watch.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedReleaseWatchCsv"));
      }

      function exportPortfolioCsv() {
        const rows = buildBrandRadarMatrix();
        if (!rows.length) {
          toast(t("noPortfolioCsv"));
          return;
        }
        const csv = csvFromPortfolioRows(rows);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "lolita-brand-portfolio.csv";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast(t("exportedPortfolioCsv"));
      }

      function premiumCsvFilename() {
        const brand = activePremiumBrandFilter === "all" ? "all-brands" : activePremiumBrandFilter.toLowerCase();
        const band = activePremiumFilter === "all" ? "all-bands" : activePremiumFilter;
        return `lolita-premium-${brand}-${band}.csv`;
      }

      function csvFromPremiumRecords(records) {
        const fields = [
          ["brand_alias", "brand_alias"],
          ["item_name", "item_name"],
          ["premium_rate", "premium_rate"],
          ["premium_band", "premium_band"],
          ["priority_score", "priority_score"],
          ["brand_weight", "brand_weight"],
          ["quality_score", "quality_score"],
          ["retail_price", "retail_price"],
          ["resale_price", "resale_price"],
          ["currency", "currency"],
          ["condition", "condition"],
          ["source", "source"],
          ["observed_at", "observed_at"],
          ["url", "url"],
          ["notes", "notes"],
        ];
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...(records || []).map((record) => fields.map(([, key]) => csvCell(record[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvFromSamplePlanRows(rows) {
        const fields = [
          ["alias", "alias"],
          ["name", "name"],
          ["urgency", "urgency"],
          ["next_action", "next_action"],
          ["priority_score", "priority_score"],
          ["brand_weight", "brand_weight"],
          ["sample_count", "sample_count"],
          ["target_samples", "target_samples"],
          ["missing_samples", "missing_samples"],
          ["avg_premium_rate", "avg_premium_rate"],
          ["market_keywords", "market_keywords"],
          ["watch_urls", "watch_urls"],
        ];
        const enriched = (rows || []).map((row) => ({
          ...row,
          market_keywords: (row.market_keywords || []).join(" | "),
          watch_urls: (row.watch_urls || []).map((link) => `${link.label}: ${link.url}`).join(" | "),
        }));
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...enriched.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvFromPremiumSeedRows(rows) {
        const fields = [
          ["brand_alias", "alias"],
          ["brand_name", "name"],
          ["seed_term", "seed_term"],
          ["seed_score", "seed_score"],
          ["sample_stage", "sample_stage"],
          ["brand_weight", "brand_weight"],
          ["tier", "tier"],
          ["style", "style"],
          ["avg_premium_rate", "avg_premium_rate"],
          ["sample_count", "sample_count"],
          ["intent", "intent"],
          ["radar_cue", "radar_cue"],
          ["goofish_url", "goofish_url"],
          ["taobao_url", "taobao_url"],
          ["mercari_url", "mercari_url"],
          ["yahoo_url", "yahoo_url"],
        ];
        const tasks = [];
        (rows || []).forEach((row) => {
          const links = row.watch_urls || [];
          (row.seed_terms || []).forEach((term) => {
            tasks.push({
              ...row,
              seed_term: term,
              sample_stage: t(premiumSeedStageLabel(row.seed_stage)),
              intent: t(premiumSeedIntentKey(row)),
              radar_cue: row.visual?.radar_cue || "",
              goofish_url: seedWatchUrl(links, "闲鱼", "Goofish"),
              taobao_url: seedWatchUrl(links, "淘宝", "Taobao"),
              mercari_url: seedWatchUrl(links, "Mercari"),
              yahoo_url: seedWatchUrl(links, "雅虎拍卖", "Yahoo"),
            });
          });
        });
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...tasks.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvFromCoreWatchRows(rows) {
        const fields = [
          ["brand_alias", "alias"],
          ["brand_name", "name"],
          ["primary_term", "primary_term"],
          ["watch_score", "watch_score"],
          ["watch_reasons", "watch_reasons"],
          ["brand_weight", "brand_weight"],
          ["sample_count", "sample_count"],
          ["target_samples", "target_samples"],
          ["next_action", "next_action"],
          ["price_anchor_status", "price_anchor_status"],
          ["avg_premium_rate", "avg_premium_rate"],
          ["avg_retail_price", "avg_retail_price"],
          ["avg_resale_price", "avg_resale_price"],
          ["avg_spread", "avg_spread"],
          ["currency", "currency"],
          ["watch_terms", "watch_terms"],
          ["radar_cue", "radar_cue"],
          ["goofish_url", "goofish_url"],
          ["taobao_url", "taobao_url"],
          ["mercari_url", "mercari_url"],
          ["yahoo_url", "yahoo_url"],
        ];
        const enriched = (rows || []).map((row) => {
          const primaryTerm = (row.watch_terms || [])[0] || row.alias;
          const links = marketSearchLinks({ ...row, keyword: primaryTerm });
          return {
            ...row,
            primary_term: primaryTerm,
            watch_reasons: coreWatchReasons(row).map((reason) => t(reason.label)).join(" | "),
            next_action: t(coreWatchNextAction(row).label),
            price_anchor_status: t(hasCoreWatchPriceAnchor(row) ? "coreWatchPriceStatusReady" : "coreWatchPriceStatusMissing"),
            watch_terms: (row.watch_terms || []).join(" | "),
            radar_cue: row.visual?.radar_cue || "",
            goofish_url: searchLinkByLabel(links, "闲鱼", "Goofish"),
            taobao_url: searchLinkByLabel(links, "淘宝", "Taobao"),
            mercari_url: searchLinkByLabel(links, "Mercari"),
            yahoo_url: searchLinkByLabel(links, "雅虎", "Yahoo"),
          };
        });
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...enriched.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvFromMarketActionRows(rows) {
        const fields = [
          ["brand_alias", "alias"],
          ["brand_name", "name"],
          ["keyword", "keyword"],
          ["action_query", "action_query"],
          ["band", "band_label"],
          ["priority_score", "priority_score"],
          ["brand_weight", "brand_weight"],
          ["avg_premium_rate", "avg_premium_rate"],
          ["sample_count", "sample_count"],
          ["next_action", "next_action"],
          ["goofish_url", "goofish_url"],
          ["taobao_url", "taobao_url"],
          ["mercari_url", "mercari_url"],
          ["yahoo_url", "yahoo_url"],
        ];
        const enriched = (rows || []).map((row) => {
          const links = marketSearchLinks(row);
          return {
            ...row,
            action_query: actionQuery(row),
            band_label: valueLabel("opportunityBand", row.band),
            next_action: Number(row.sample_count) < 2 ? t("patternSample") : t("actionSearch"),
            goofish_url: searchLinkByLabel(links, t("actionGoofish"), "Goofish", "闲鱼"),
            taobao_url: searchLinkByLabel(links, t("actionTaobao"), "Taobao", "淘宝"),
            mercari_url: searchLinkByLabel(links, t("actionMercari"), "Mercari"),
            yahoo_url: searchLinkByLabel(links, t("actionYahoo"), "Yahoo", "雅虎"),
          };
        });
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...enriched.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvFromRunSheetRows(rows) {
        const fields = [
          ["task_type", "kind_label"],
          ["brand_alias", "alias"],
          ["brand_name", "name"],
          ["keyword", "keyword"],
          ["label", "label"],
          ["detail", "detail"],
          ["priority_score", "priority_score"],
          ["brand_weight", "brand_weight"],
          ["sample_count", "sample_count"],
          ["next_target", "jump_target"],
          ["goofish_url", "goofish_url"],
          ["taobao_url", "taobao_url"],
          ["mercari_url", "mercari_url"],
          ["yahoo_url", "yahoo_url"],
        ];
        const enriched = (rows || []).map((row) => {
          const links = row.search_links || [];
          return {
            ...row,
            goofish_url: searchLinkByLabel(links, t("actionGoofish"), "Goofish", "闲鱼"),
            taobao_url: searchLinkByLabel(links, t("actionTaobao"), "Taobao", "淘宝"),
            mercari_url: searchLinkByLabel(links, t("actionMercari"), "Mercari"),
            yahoo_url: searchLinkByLabel(links, t("actionYahoo"), "Yahoo", "雅虎"),
          };
        });
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...enriched.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvFromReleaseWatchRows(rows) {
        const fields = [
          ["brand_alias", "alias"],
          ["brand_name", "name"],
          ["release_title", "title"],
          ["release_status", "status_label"],
          ["release_score", "release_score"],
          ["action", "action_label_text"],
          ["matched_terms", "matched_terms_text"],
          ["primary_keyword", "primary_keyword"],
          ["brand_weight", "brand_weight"],
          ["avg_premium_rate", "avg_premium_rate"],
          ["sample_count", "sample_count"],
          ["source", "source"],
          ["published_at", "published_at"],
          ["source_url", "url"],
          ["goofish_url", "goofish_url"],
          ["taobao_url", "taobao_url"],
          ["mercari_url", "mercari_url"],
          ["yahoo_url", "yahoo_url"],
        ];
        const enriched = (rows || []).map((row) => {
          const links = marketSearchLinks({ ...row, keyword: row.primary_keyword || row.alias });
          return {
            ...row,
            status_label: valueLabel("status", row.status),
            action_label_text: t(row.action_label),
            matched_terms_text: (row.matched_terms || []).join(" | "),
            goofish_url: searchLinkByLabel(links, t("actionGoofish"), "Goofish", "闲鱼"),
            taobao_url: searchLinkByLabel(links, t("actionTaobao"), "Taobao", "淘宝"),
            mercari_url: searchLinkByLabel(links, t("actionMercari"), "Mercari"),
            yahoo_url: searchLinkByLabel(links, t("actionYahoo"), "Yahoo", "雅虎"),
          };
        });
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...enriched.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvFromPortfolioRows(rows) {
        const stats = brandPortfolioStats(rows);
        const lanes = brandPortfolioLanes(rows, stats);
        const fields = [
          ["lane", "lane_label"],
          ["count", "count"],
          ["score", "score"],
          ["detail", "detail"],
          ["target", "target"],
          ["sample_alias", "sample_alias"],
          ["lead_alias", "lead_alias"],
          ["health", "health"],
          ["coverage", "coverage"],
          ["core_gaps", "core_gaps"],
          ["premium_heat", "hot_count"],
          ["weight_drift", "drift_count"],
        ];
        const enriched = lanes.map((lane) => ({
          ...lane,
          lane_label: t(lane.label),
          lead_alias: lane.lead_brand?.alias || "",
          health: stats.health,
          coverage: stats.coverage,
          core_gaps: stats.core_gaps,
          hot_count: stats.hot_count,
          drift_count: stats.drift_count,
        }));
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...enriched.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvFromCrownRows(rows) {
        const fields = [
          ["rank", "rank"],
          ["alias", "alias"],
          ["name", "name"],
          ["crown_score", "crown_score"],
          ["confidence_score", "confidence_score"],
          ["confidence", "confidence_label_text"],
          ["action", "action_label_text"],
          ["brand_weight", "brand_weight"],
          ["target_weight", "target_weight"],
          ["weight_delta", "weight_delta"],
          ["formula_confidence", "formula_confidence"],
          ["avg_premium_rate", "avg_premium_rate"],
          ["sample_count", "sample_count"],
          ["target_samples", "target_samples"],
          ["release_score", "release_score"],
          ["primary_keyword", "primary_keyword"],
          ["market_keywords", "market_keywords"],
          ["watch_urls", "watch_urls"],
          ["radar_cue", "radar_cue"],
        ];
        const enriched = (rows || []).map((row, index) => ({
          ...row,
          rank: index + 1,
          confidence_label_text: t(row.confidence_label),
          action_label_text: t(row.action_label),
          market_keywords: (row.keywords || row.market_keywords || []).join(" | "),
          watch_urls: (row.watch_urls || []).map((link) => `${link.label}: ${link.url}`).join(" | "),
          radar_cue: row.visual?.radar_cue || "",
        }));
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...enriched.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function searchLinkByLabel(links, ...labels) {
        const wanted = labels.map((label) => String(label || "").toLowerCase());
        const match = (links || []).find((link) => wanted.some((label) => String(link.label || "").toLowerCase().includes(label)));
        return match?.href || "";
      }

      function seedWatchUrl(links, ...labels) {
        const wanted = labels.map((label) => String(label || "").toLowerCase());
        const match = (links || []).find((link) => wanted.some((label) => String(link.label || "").toLowerCase().includes(label)));
        return match?.url || "";
      }

      function csvFromBrandWeights(rows) {
        const csvRows = rows || [];
        const fields = [
          ["alias", "alias"],
          ["name", "name"],
          ["saved_weight", "saved_weight"],
          ["draft_weight", "brand_weight"],
          ["score_delta", "score_delta"],
          ["formula_target", "formula_target"],
          ["formula_delta", "formula_delta"],
          ["formula_confidence", "formula_confidence"],
          ["tier", "tier"],
          ["style", "style"],
          ["palette", "palette"],
          ["motif", "motif"],
          ["radar_cue", "radar_cue"],
          ["watch_urls", "watch_urls"],
          ["avg_premium_rate", "avg_premium_rate"],
          ["max_premium_rate", "max_premium_rate"],
          ["premium_band", "premium_band"],
          ["avg_retail_price", "avg_retail_price"],
          ["avg_resale_price", "avg_resale_price"],
          ["avg_spread", "avg_spread"],
          ["sample_count", "sample_count"],
          ["priority_score", "priority_score"],
          ["evidence_level", "evidence_level"],
          ["market_keywords", "market_keywords"],
        ];
        const formulaByAlias = new Map(buildBrandWeightFormula(csvRows, csvRows.length).map((row) => [row.alias, row]));
        const enriched = csvRows.map((row) => {
          const formula = formulaByAlias.get(row.alias) || {};
          return {
            ...row,
            saved_weight: brandByAlias(row.alias)?.weight ?? row.brand_weight,
            formula_target: formula.target_weight ?? "",
            formula_delta: formula.delta ?? "",
            formula_confidence: formula.confidence ?? "",
            palette: row.visual?.palette || "",
            motif: row.visual?.motif || "",
            radar_cue: row.visual?.radar_cue || "",
            watch_urls: (row.watch_urls || []).map((link) => `${link.label}: ${link.url}`).join(" | "),
            market_keywords: (row.market_keywords || []).join(" | "),
          };
        });
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...enriched.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvFromBrandWeightScorecards(rows) {
        const fields = [
          ["alias", "alias"],
          ["name", "name"],
          ["verdict", "verdict"],
          ["current_weight", "brand_weight"],
          ["target_weight", "target_weight"],
          ["delta", "delta"],
          ["confidence", "confidence"],
          ["part_base", "part_base"],
          ["part_premium", "part_premium"],
          ["part_evidence", "part_evidence"],
          ["part_keywords", "part_keywords"],
          ["part_watchability", "part_watchability"],
          ["avg_premium_rate", "avg_premium_rate"],
          ["max_premium_rate", "max_premium_rate"],
          ["sample_count", "sample_count"],
          ["tier", "tier"],
          ["style", "style"],
          ["evidence_level", "evidence_level"],
          ["market_keywords", "market_keywords"],
          ["watch_urls", "watch_urls"],
          ["radar_cue", "radar_cue"],
        ];
        const enriched = (rows || []).map((row) => ({
          ...row,
          verdict: t(scorecardVerdictLabel(row.scorecard_verdict)),
          part_base: row.parts?.base ?? "",
          part_premium: row.parts?.premium ?? "",
          part_evidence: row.parts?.evidence ?? "",
          part_keywords: row.parts?.keywords ?? "",
          part_watchability: row.parts?.watchability ?? "",
          market_keywords: (row.market_keywords || []).join(" | "),
          watch_urls: (row.watch_urls || []).map((link) => `${link.label}: ${link.url}`).join(" | "),
          radar_cue: row.visual?.radar_cue || "",
        }));
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...enriched.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvFromBrandWeightGuardrails(rows) {
        const fields = [
          ["alias", "alias"],
          ["name", "name"],
          ["severity", "severity_label"],
          ["guardrail", "guardrail_label"],
          ["reason", "reason_label"],
          ["risk_score", "risk_score"],
          ["current_weight", "brand_weight"],
          ["target_weight", "target_weight"],
          ["confidence", "confidence"],
          ["avg_premium_rate", "avg_premium_rate"],
          ["max_premium_rate", "max_premium_rate"],
          ["sample_count", "sample_count"],
          ["tier", "tier"],
          ["style", "style"],
          ["evidence_level", "evidence_level"],
          ["market_keywords", "market_keywords"],
          ["watch_urls", "watch_urls"],
          ["next_action", "next_action"],
          ["radar_cue", "radar_cue"],
        ];
        const enriched = (rows || []).map((row) => ({
          ...row,
          severity_label: t(row.severity === "critical" ? "guardrailCritical" : "guardrailWatch"),
          guardrail_label: t(row.label),
          reason_label: t(row.reason),
          market_keywords: (row.market_keywords || []).join(" | "),
          watch_urls: (row.watch_urls || []).map((link) => `${link.label}: ${link.url}`).join(" | "),
          next_action: Number(row.sample_count) < 2 ? t("guardrailActionSample") : Number(row.target_weight) !== Number(row.brand_weight) ? t("guardrailActionApply") : t("guardrailWatch"),
          radar_cue: row.visual?.radar_cue || "",
        }));
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...enriched.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function weightScenarioCsvRows(rows) {
        const scenarios = ["release", "premium", "evidence"];
        const formulaByAlias = new Map(buildBrandWeightFormula(rows, Number.POSITIVE_INFINITY).map((row) => [row.alias, row]));
        return (rows || []).flatMap((entry) => {
          const savedWeight = Number(brandByAlias(entry.alias)?.weight ?? entry.brand_weight) || 0;
          const formula = formulaByAlias.get(entry.alias) || {};
          return scenarios.map((scenario) => {
            const targetWeight = scenarioTargetWeight(entry, scenario);
            return {
              ...entry,
              scenario,
              scenario_label: t(scenarioLabelKey(scenario)),
              saved_weight: savedWeight,
              target_weight: targetWeight,
              delta: targetWeight - savedWeight,
              formula_target: formula.target_weight ?? "",
              formula_delta: formula.delta ?? "",
              formula_confidence: formula.confidence ?? "",
              market_keywords: (entry.market_keywords || []).join(" | "),
              watch_urls: (entry.watch_urls || []).map((link) => `${link.label}: ${link.url}`).join(" | "),
              radar_cue: entry.visual?.radar_cue || "",
            };
          });
        });
      }

      function csvFromWeightScenarios(rows) {
        const fields = [
          ["scenario", "scenario"],
          ["scenario_label", "scenario_label"],
          ["alias", "alias"],
          ["name", "name"],
          ["saved_weight", "saved_weight"],
          ["target_weight", "target_weight"],
          ["delta", "delta"],
          ["formula_target", "formula_target"],
          ["formula_delta", "formula_delta"],
          ["formula_confidence", "formula_confidence"],
          ["current_draft_weight", "brand_weight"],
          ["tier", "tier"],
          ["style", "style"],
          ["avg_premium_rate", "avg_premium_rate"],
          ["max_premium_rate", "max_premium_rate"],
          ["sample_count", "sample_count"],
          ["evidence_level", "evidence_level"],
          ["priority_score", "priority_score"],
          ["market_keywords", "market_keywords"],
          ["watch_urls", "watch_urls"],
          ["radar_cue", "radar_cue"],
        ];
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...(rows || []).map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvFromDailyRadarActions(rows) {
        const fields = [
          ["rank", "rank"],
          ["brand_alias", "alias"],
          ["brand_name", "name"],
          ["action_kind", "action_kind"],
          ["action_label", "action_label"],
          ["daily_score", "daily_score"],
          ["brand_weight", "brand_weight"],
          ["sample_count", "sample_count"],
          ["avg_premium_rate", "avg_premium_rate"],
          ["detail", "daily_detail"],
          ["jump_target", "daily_target"],
          ["sample_alias", "daily_sample"],
          ["keyword", "daily_keyword"],
          ["radar_cue", "radar_cue"],
        ];
        const enriched = (rows || []).map((row, index) => ({
          ...row,
          rank: index + 1,
          action_kind: t(row.daily_kind),
          action_label: t(row.daily_label),
          radar_cue: row.visual?.radar_cue || "",
        }));
        const lines = [
          fields.map(([header]) => csvCell(header)).join(","),
          ...enriched.map((row) => fields.map(([, key]) => csvCell(row[key])).join(",")),
        ];
        return lines.join("\n");
      }

      function csvCell(value) {
        const text = String(value ?? "");
        return `"${text.replaceAll('"', '""')}"`;
      }

      function renderEvidenceHealth(quality) {
        const sampleCount = Number(quality.sample_count) || 0;
        const score = Number(quality.avg_quality_score) || 0;
        $("evidenceHealth").innerHTML = `
          <article class="quality-hero">
            <strong>${escapeHtml(score)}</strong>
            <p class="muted">${escapeHtml(t("qualityScore"))} · ${escapeHtml(sampleCount)} ${escapeHtml(t("samples"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(score)}%"></span></div>
          </article>
          <div class="quality-checks">
            ${[
              [t("qualityLinked"), quality.linked_count || 0],
              [t("qualitySourced"), quality.sourced_count || 0],
              [t("qualityDated"), quality.dated_count || 0],
              [t("qualityNoted"), quality.noted_count || 0],
              [t("qualityWeak"), quality.weak_count || 0],
            ].map(([label, value]) => `<article class="quality-check"><strong>${escapeHtml(value)}</strong><span class="muted">${escapeHtml(label)}</span></article>`).join("")}
          </div>
        `;
      }

      function renderMarketActionDesk(patterns) {
        const actions = marketActions(patterns);
        const needsSamples = actions.filter((pattern) => Number(pattern.sample_count) < 2).length;
        const sampled = actions.filter((pattern) => Number(pattern.sample_count) > 0).length;
        $("marketActionDesk").innerHTML = `
          <article class="action-brief">
            <strong>${escapeHtml(actions.length)}</strong>
            <p class="muted">${escapeHtml(t("actionTotal"))}</p>
            <div class="coverage-stats">
              <article class="coverage-stat"><strong>${escapeHtml(needsSamples)}</strong><span class="muted">${escapeHtml(t("actionNeedsSamples"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(sampled)}</strong><span class="muted">${escapeHtml(t("actionWithSamples"))}</span></article>
            </div>
          </article>
          <div class="action-list">
            ${actions.length ? actions.map((pattern) => `<article>
              <header>
                <div>
                  <strong>${escapeHtml(pattern.alias)} · ${escapeHtml(pattern.keyword)}</strong>
                  <p class="muted">${escapeHtml(t("priorityScore"))} ${escapeHtml(pattern.priority_score)} · ${escapeHtml(t("samples"))} ${escapeHtml(pattern.sample_count)} · ${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(pattern.avg_premium_rate))}</p>
                </div>
                <span class="pill ${opportunityPill(pattern.band)}">${escapeHtml(valueLabel("opportunityBand", pattern.band))}</span>
              </header>
              <p class="muted">${escapeHtml(t("actionQuery"))} · ${escapeHtml(actionQuery(pattern))}</p>
              <div class="search-links">
                ${marketSearchLinks(pattern).map((link) => `<a href="${escapeHtml(link.href)}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>`).join("")}
                <button type="button" data-action-sample="${escapeHtml(pattern.alias)}" data-action-keyword="${escapeHtml(pattern.keyword)}">${escapeHtml(t("patternSample"))}</button>
              </div>
            </article>`).join("") : `<div class="row">${escapeHtml(t("noPatternPremium"))}</div>`}
          </div>
        `;
      }

      function marketActions(patterns) {
        return [...patterns].sort((a, b) => (
          Number(b.sample_count < 2) - Number(a.sample_count < 2)
          || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0)
          || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
        )).slice(0, 6);
      }

      function actionQuery(pattern) {
        return `${pattern.alias} ${pattern.keyword} lolita`;
      }

      function marketSearchLinks(pattern) {
        const localQuery = encodeURIComponent(actionQuery(pattern));
        const jpQuery = encodeURIComponent(`${pattern.name || pattern.alias} ${pattern.keyword}`);
        return [
          { label: t("actionGoofish"), href: `https://www.goofish.com/search?q=${localQuery}` },
          { label: t("actionTaobao"), href: `https://s.taobao.com/search?q=${localQuery}` },
          { label: t("actionMercari"), href: `https://jp.mercari.com/search?keyword=${jpQuery}` },
          { label: t("actionYahoo"), href: `https://auctions.yahoo.co.jp/search/search?p=${jpQuery}` },
        ];
      }

      function renderResaleRunSheet(rows) {
        const tasks = resaleRunSheetRows(rows);
        const anchorGaps = tasks.filter((task) => task.kind === "price" && task.price_status === "sample").length;
        const searches = tasks.filter((task) => (task.search_links || []).length).length;
        const samples = tasks.filter((task) => task.sample_alias || task.sample_keyword).length;
        $("resaleRunSheet").innerHTML = tasks.length ? `
          <article class="run-sheet-brief">
            <strong>${escapeHtml(tasks.length)}</strong>
            <p>${escapeHtml(t("runSheetTasks"))} · ${escapeHtml(t("resaleRunSheetHint"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(runSheetHeat(tasks))}%"></span></div>
            <div class="run-sheet-stats">
              <span><strong>${escapeHtml(anchorGaps)}</strong>${escapeHtml(t("runSheetAnchorGaps"))}</span>
              <span><strong>${escapeHtml(searches)}</strong>${escapeHtml(t("runSheetSearches"))}</span>
              <span><strong>${escapeHtml(samples)}</strong>${escapeHtml(t("runSheetSamples"))}</span>
              <span><strong>${escapeHtml(tasks[0]?.alias || "-")}</strong>${escapeHtml(t("dailyLead"))}</span>
            </div>
          </article>
          <div class="run-sheet-list">
            ${tasks.map(runSheetCardHtml).join("")}
          </div>
        ` : `<div class="row">${escapeHtml(t("runSheetNoRows"))}</div>`;
      }

      function runSheetCardHtml(task) {
        const keyword = task.keyword ? ` · ${task.keyword}` : "";
        const links = (task.search_links || []).slice(0, 4);
        return `<article class="run-sheet-card" style="${escapeHtml(brandVisualStyle(task))}">
          <header>
            <div>
              <strong>${escapeHtml(task.alias)}${escapeHtml(keyword)}</strong>
              <p>${escapeHtml(task.name || "")}</p>
            </div>
            <span class="pill ${escapeHtml(task.tone || "")}">${escapeHtml(task.kind_label)}</span>
          </header>
          <p>${escapeHtml(task.label)} · ${escapeHtml(task.detail || "")}</p>
          <div class="run-sheet-meta">
            <span>${escapeHtml(t("priorityScore"))} ${escapeHtml(task.priority_score || 0)}</span>
            <span>${escapeHtml(t("weightLabel"))} ${escapeHtml(task.brand_weight || 0)}</span>
            <span>${escapeHtml(t("samples"))} ${escapeHtml(task.sample_count || 0)}</span>
          </div>
          <div class="run-sheet-actions">
            ${task.jump_target ? `<button type="button" data-run-sheet-jump="${escapeHtml(task.jump_target)}">${escapeHtml(t("runSheetGo"))}</button>` : ""}
            ${task.sample_alias ? `<button type="button" data-run-sheet-sample="${escapeHtml(task.sample_alias)}">${escapeHtml(t("runSheetSample"))}</button>` : ""}
            ${task.sample_keyword ? `<button type="button" data-run-sheet-keyword-brand="${escapeHtml(task.alias)}" data-run-sheet-keyword="${escapeHtml(task.sample_keyword)}">${escapeHtml(t("dailyKeyword"))}</button>` : ""}
            ${links.map((link) => `<a href="${escapeHtml(link.href)}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>`).join("")}
          </div>
        </article>`;
      }

      function resaleRunSheetRows(rows) {
        const tasks = [];
        dailyRadarActions(rows).slice(0, 4).forEach((entry) => {
          const keyword = entry.daily_keyword || (entry.market_keywords || [])[0] || "";
          tasks.push({
            ...entry,
            kind: "daily",
            kind_rank: 3,
            kind_label: t("runSheetDaily"),
            label: t(entry.daily_label),
            detail: entry.daily_detail,
            priority_score: Number(entry.daily_score) || Number(entry.priority_score) || 0,
            keyword,
            sample_alias: entry.daily_sample || "",
            sample_keyword: keyword,
            jump_target: entry.daily_target,
            search_links: keyword ? marketSearchLinks({ ...entry, keyword }) : [],
            tone: entry.daily_tone || "",
          });
        });
        releaseWatchRows(rows).slice(0, 4).forEach((entry) => {
          tasks.push({
            ...entry,
            kind: "release",
            kind_rank: 4.5,
            kind_label: t("runSheetRelease"),
            label: t(entry.action_label),
            detail: `${valueLabel("status", entry.status)} · ${entry.title}`,
            priority_score: Number(entry.release_score) || 0,
            keyword: entry.primary_keyword || entry.alias,
            sample_alias: entry.alias,
            sample_keyword: entry.primary_keyword || entry.alias,
            jump_target: "releaseWatchQueue",
            search_links: marketSearchLinks({ ...entry, keyword: entry.primary_keyword || entry.alias }),
            tone: releaseWatchPill(entry.action_label),
          });
        });
        marketActions(currentState?.market?.patterns || []).slice(0, 4).forEach((pattern) => {
          tasks.push({
            ...pattern,
            kind: "market",
            kind_rank: 4,
            kind_label: t("runSheetMarket"),
            label: t("actionQuery"),
            detail: actionQuery(pattern),
            priority_score: Number(pattern.priority_score) || 0,
            sample_alias: "",
            sample_keyword: pattern.keyword || "",
            jump_target: "marketActionDesk",
            search_links: marketSearchLinks(pattern),
            tone: opportunityPill(pattern.band),
          });
        });
        priceDisciplineRows(rows).slice(0, 4).forEach((entry) => {
          const keyword = (entry.market_keywords || [])[0] || "";
          tasks.push({
            ...entry,
            kind: "price",
            kind_rank: entry.price_status === "sample" ? 5 : 2,
            kind_label: t("runSheetPrice"),
            label: t(priceDisciplineLabel(entry.price_status)),
            detail: `${t("priceDisciplineCeiling")} ${priceDisciplineMoney(entry.price_ceiling, entry.currency)} · ${t("priceDisciplineObserved")} ${priceDisciplineMoney(entry.avg_resale_price, entry.currency)}`,
            priority_score: Number(entry.price_score) || 0,
            keyword,
            sample_alias: entry.alias,
            sample_keyword: keyword,
            jump_target: "priceDiscipline",
            search_links: keyword ? marketSearchLinks({ ...entry, keyword }) : [],
            tone: priceDisciplinePill(entry.price_status),
          });
        });
        return dedupeRunSheetTasks(tasks)
          .sort((a, b) => (
            (Number(b.kind_rank) || 0) - (Number(a.kind_rank) || 0)
            || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0)
            || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
          ))
          .slice(0, 8);
      }

      function dedupeRunSheetTasks(tasks) {
        const seen = new Set();
        return (tasks || []).filter((task) => {
          const key = [task.kind, task.alias, task.keyword || task.label].join("|").toLowerCase();
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
      }

      function runSheetHeat(tasks) {
        return tasks.length
          ? Math.round(tasks.reduce((sum, task) => sum + (Number(task.priority_score) || 0), 0) / tasks.length)
          : 0;
      }

      function renderPriceDiscipline(rows) {
        const lines = priceDisciplineRows(rows);
        const stats = priceDisciplineStats(lines);
        $("priceDiscipline").innerHTML = lines.length ? `
          <article class="price-brief">
            <strong>${escapeHtml(stats.hot)}</strong>
            <p>${escapeHtml(t("priceDisciplineHot"))} · ${escapeHtml(lines.length)} ${escapeHtml(t("priceDisciplineRows"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.heat)}%"></span></div>
            <div class="coverage-stats">
              <article class="coverage-stat"><strong>${escapeHtml(stats.room)}</strong><span class="muted">${escapeHtml(t("priceDisciplineRoom"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(stats.near)}</strong><span class="muted">${escapeHtml(t("priceDisciplineNear"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(stats.sample)}</strong><span class="muted">${escapeHtml(t("priceDisciplineSample"))}</span></article>
            </div>
            <p>${escapeHtml(t("priceDisciplineHint"))}</p>
          </article>
          <div class="price-list">
            ${lines.map(priceDisciplineCardHtml).join("")}
          </div>
        ` : `<div class="row">${escapeHtml(t("priceDisciplineNoRows"))}</div>`;
      }

      function priceDisciplineCardHtml(entry) {
        return `<article class="price-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)} · ${escapeHtml(entry.name)}</strong>
              <p>${escapeHtml(t("weightLabel"))} ${escapeHtml(entry.brand_weight)} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)} · ${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))}</p>
            </div>
            <span class="pill ${priceDisciplinePill(entry.price_status)}">${escapeHtml(t(priceDisciplineLabel(entry.price_status)))}</span>
          </header>
          <div class="price-ladder">
            <span>${escapeHtml(t("priceDisciplineCeiling"))}<strong>${escapeHtml(priceDisciplineMoney(entry.price_ceiling, entry.currency))}</strong></span>
            <span>${escapeHtml(t("priceDisciplineObserved"))}<strong>${escapeHtml(priceDisciplineMoney(entry.avg_resale_price, entry.currency))}</strong></span>
            <span>${escapeHtml(t("priceDisciplineGap"))}<strong>${escapeHtml(priceDisciplineMoney(entry.price_gap, entry.currency))}</strong></span>
          </div>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(entry.price_score)}%"></span></div>
          <div class="price-actions">
            <button type="button" class="secondary" data-price-discipline-sample="${escapeHtml(entry.alias)}">${escapeHtml(t("priceDisciplineSampleAction"))}</button>
          </div>
        </article>`;
      }

      function priceDisciplineRows(rows) {
        return (rows || []).map((entry) => {
          const retail = Number(entry.avg_retail_price) || 0;
          const resale = Number(entry.avg_resale_price) || 0;
          const sampleCount = Number(entry.sample_count) || 0;
          const weight = Number(entry.brand_weight) || 0;
          if ((retail <= 0 || resale <= 0) && weight < 70) return null;
          if (retail <= 0 || resale <= 0) {
            return {
              ...entry,
              price_ceiling: 0,
              price_gap: 0,
              price_status: "sample",
              price_score: clampScore(Math.round(weight * .36 + 16)),
            };
          }
          const ceilingRate = priceDisciplineCeilingRate(entry);
          const priceCeiling = Math.round(retail * (1 + ceilingRate));
          const priceGap = Math.round(priceCeiling - resale);
          const priceStatus = priceDisciplineStatus(entry, priceGap, retail);
          return {
            ...entry,
            price_ceiling: priceCeiling,
            price_gap: priceGap,
            price_status: priceStatus,
            price_score: priceDisciplineScore(entry, priceGap, retail, sampleCount),
          };
        }).filter(Boolean)
          .sort((a, b) => (
            priceDisciplineRank(b.price_status) - priceDisciplineRank(a.price_status)
            || (Number(b.price_score) || 0) - (Number(a.price_score) || 0)
            || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
          ))
          .slice(0, 6);
      }

      function priceDisciplineCeilingRate(entry) {
        const weight = Number(entry.brand_weight) || 0;
        const samples = Number(entry.sample_count) || 0;
        const premium = Number(entry.avg_premium_rate) || 0;
        let rate = weight >= 90 ? 0.35 : weight >= 75 ? 0.25 : 0.15;
        if (samples < 2) rate -= 0.08;
        if (samples >= 2 && premium >= 0.5) rate += 0.1;
        else if (samples >= 2 && premium >= 0.25) rate += 0.05;
        return Math.max(0.08, Math.min(0.55, rate));
      }

      function priceDisciplineStatus(entry, priceGap, retail) {
        const samples = Number(entry.sample_count) || 0;
        if (samples < 2) return "sample";
        if (Number(priceGap) < 0) return "hot";
        if (Number(priceGap) <= Number(retail) * 0.08) return "near";
        return "room";
      }

      function priceDisciplineScore(entry, priceGap, retail, sampleCount) {
        const weight = Number(entry.brand_weight) || 0;
        const premium = Math.max(0, Number(entry.avg_premium_rate) || 0);
        const gapPressure = Math.min(35, Math.abs(Number(priceGap) || 0) / Math.max(Number(retail) || 1, 1) * 70);
        const samplePressure = Number(sampleCount) < 2 ? 16 : 0;
        return clampScore(Math.round(weight * .36 + premium * 30 + gapPressure + samplePressure));
      }

      function priceDisciplineStats(rows) {
        const total = rows.length || 1;
        const hot = rows.filter((row) => row.price_status === "hot").length;
        return {
          hot,
          room: rows.filter((row) => row.price_status === "room").length,
          near: rows.filter((row) => row.price_status === "near").length,
          sample: rows.filter((row) => row.price_status === "sample").length,
          heat: Math.round(hot / total * 100),
        };
      }

      function priceDisciplineRank(status) {
        return { hot: 4, room: 3, near: 2, sample: 1 }[status] || 0;
      }

      function priceDisciplineLabel(status) {
        return {
          hot: "priceDisciplineHot",
          room: "priceDisciplineRoom",
          near: "priceDisciplineNear",
          sample: "priceDisciplineSample",
        }[status] || "priceDisciplineSample";
      }

      function priceDisciplinePill(status) {
        if (status === "hot") return "warn";
        if (status === "room") return "rose";
        if (status === "near") return "gold";
        return "off";
      }

      function priceDisciplineMoney(value, currency) {
        return Number(value) ? formatMoney(value, currency) : t("priceDisciplineMissing");
      }

      function renderPatternPremiumRadar(patterns) {
        $("patternPremiumRadar").innerHTML = patterns.length ? patterns.map((pattern) => `<article class="pattern-card">
          <header>
            <div>
              <strong>${escapeHtml(pattern.alias)} · ${escapeHtml(pattern.keyword)}</strong>
              <p class="muted">${escapeHtml(pattern.name)}</p>
            </div>
            <span class="pill ${opportunityPill(pattern.band)}">${escapeHtml(valueLabel("opportunityBand", pattern.band))}</span>
          </header>
          <span class="premium-rate">${escapeHtml(formatPercent(pattern.avg_premium_rate))}</span>
          <p class="muted">${escapeHtml(t("priorityScore"))} ${escapeHtml(pattern.priority_score)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(pattern.brand_weight)}</p>
          <p class="muted">${escapeHtml(t("samples"))} ${escapeHtml(pattern.sample_count)} · ${escapeHtml(t("maxPremium"))} ${escapeHtml(formatPercent(pattern.max_premium_rate))}</p>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${Number(pattern.priority_score) || 0}%"></span></div>
          ${renderPatternEvidence(pattern.evidence || [])}
          <button type="button" class="secondary" data-pattern-brand="${escapeHtml(pattern.alias)}" data-pattern-keyword="${escapeHtml(pattern.keyword)}">${escapeHtml(t("patternSample"))}</button>
        </article>`).join("") : `<div class="row">${escapeHtml(t("noPatternPremium"))}</div>`;
      }

      function renderPatternEvidence(evidence) {
        if (!evidence.length) return `<p class="muted">${escapeHtml(t("noEvidence"))}</p>`;
        return `<div class="evidence-list">
          <p class="muted">${escapeHtml(t("evidence"))}</p>
          ${evidence.map((row) => `<article>
            <p class="muted"><strong>${escapeHtml(row.item_name || "-")}</strong> · ${escapeHtml(formatPercent(row.premium_rate))} · ${escapeHtml(formatMoney(row.resale_price, row.currency))}</p>
            <p class="muted">${escapeHtml([row.source, row.observed_at, row.notes].filter(Boolean).join(" · "))}${row.url ? ` · <a href="${escapeHtml(row.url)}" target="_blank" rel="noreferrer">${escapeHtml(t("sampleUrl"))}</a>` : ""}</p>
          </article>`).join("")}
        </div>`;
      }

      function renderSource(source) {
        const keywords = source.keywords.length ? source.keywords.join(", ") : t("noKeywords");
        return `<article class="source-card">
          <header>
            <strong>${escapeHtml(source.name)}</strong>
            <span class="pill ${source.enabled ? "" : "off"}">${source.enabled ? t("enabled") : t("disabled")}</span>
          </header>
          <p>${escapeHtml(valueLabel("sourceType", source.type))} · ${escapeHtml(keywords)}</p>
          <p><a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(source.url)}</a></p>
          <button data-source="${escapeHtml(source.name)}" data-disabled="${source.enabled ? "false" : "true"}" ${source.enabled ? "" : "disabled"}>${source.enabled ? t("checkSource") : t("disabledButton")}</button>
        </article>`;
      }

      function renderEvent(event) {
        return `<article class="row event-card">
          <header>
            <strong><a href="${escapeHtml(event.url)}" target="_blank" rel="noreferrer">${escapeHtml(event.title)}</a></strong>
            <span class="pill ${event.event_type === "update" ? "warn" : ""}">${escapeHtml(valueLabel("eventType", event.event_type))}</span>
          </header>
          <p>${escapeHtml(event.source)} · ${escapeHtml(valueLabel("status", event.status))} · ${escapeHtml(event.created_at || "")}</p>
        </article>`;
      }

      function renderItem(item) {
        const lastSeen = item.last_seen_at ? `${t("seen")} ${item.last_seen_at}` : "";
        return `<article class="row">
          <header>
            <strong><a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a></strong>
            <span class="pill">${escapeHtml(valueLabel("status", item.status))}</span>
          </header>
          <p>${escapeHtml(item.source)} · ${escapeHtml(item.published_at || t("undated"))}${lastSeen ? ` · ${escapeHtml(lastSeen)}` : ""}</p>
        </article>`;
      }

      async function runCheck(source = null) {
        setBusy(true);
        try {
          const payload = await api("/api/check", { method: "POST", body: JSON.stringify({ source, notify: false }) });
          currentState = payload;
          render(payload);
          toast(`${source || t("allSources")} ${t("checked")}: ${newEventText(payload.new_event_count || 0)}`);
        } catch (error) {
          toast(error.message);
        } finally {
          setBusy(false);
        }
      }

      async function addMarketObservation(event) {
        event.preventDefault();
        setBusy(true);
        try {
          const form = event.currentTarget;
          const payload = Object.fromEntries(new FormData(form).entries());
          const nextState = await api("/api/market/observations", { method: "POST", body: JSON.stringify(payload) });
          currentState = nextState;
          render(nextState);
          $("marketItem").value = "";
          $("marketRetail").value = "";
          $("marketResale").value = "";
          $("marketUrl").value = "";
          $("marketNotes").value = "";
          clearSampleTaskHint();
          renderSamplePreview();
          toast(t("sampleAdded"));
        } catch (error) {
          toast(error.message);
        } finally {
          setBusy(false);
        }
      }

      async function saveBrandWeights() {
        setBusy(true);
        try {
          const weights = Array.from(document.querySelectorAll("[data-brand-weight]")).map((input) => ({
            alias: input.dataset.brandWeight,
            weight: Number(input.value) || 0,
          }));
          const nextState = await api("/api/brand-weights", { method: "PUT", body: JSON.stringify({ weights }) });
          currentState = nextState;
          render(nextState);
          toast(t("weightsSaved"));
        } catch (error) {
          toast(error.message);
        } finally {
          setBusy(false);
        }
      }

      function resetBrandWeightDraft() {
        renderBrandWeights(currentState?.brand_weights || []);
        renderBrandRadarViews();
        renderOpportunityRadar(currentState?.opportunity_radar || []);
        toast(t("weightsReset"));
      }

      function handleWeightInput(event) {
        const input = event.target.closest("[data-brand-weight]");
        if (!input) return;
        updateWeightDraftInput(input);
        updateWeightDirtyState();
        renderBrandRadarViews();
        renderOpportunityRadar(buildDraftOpportunityRadar());
      }

      function updateWeightDraftInput(input) {
        const card = input.closest(".brand-chip");
        const label = card?.querySelector("[data-weight-label]");
        const bar = card?.querySelector(".signal-bar span");
        const insight = card?.querySelector("[data-weight-insight]");
        const brand = brandByAlias(input.dataset.brandWeight);
        if (label) label.textContent = `${t("weightLabel")} ${input.value}`;
        if (bar) bar.style.setProperty("--score", `${input.value}%`);
        if (insight && brand) insight.innerHTML = brandWeightInsightHtml(brand, input.value);
        if (card) card.classList.toggle("dirty", input.value !== input.dataset.originalWeight);
      }

      function applyTuningDraft(alias, targetWeight) {
        const input = document.querySelector(`[data-brand-weight="${cssEscape(alias)}"]`);
        if (!input) return;
        input.value = clampScore(targetWeight);
        updateWeightDraftInput(input);
        updateWeightDirtyState();
        renderBrandRadarViews();
        renderOpportunityRadar(buildDraftOpportunityRadar());
        input.scrollIntoView({ behavior: "smooth", block: "center" });
        toast(`${alias} ${t("tuningDraftApplied")}`);
      }

      function applyFormulaDraft(alias, targetWeight) {
        const input = document.querySelector(`[data-brand-weight="${cssEscape(alias)}"]`);
        if (!input) return;
        input.value = clampScore(targetWeight);
        updateWeightDraftInput(input);
        updateWeightDirtyState();
        renderBrandRadarViews();
        renderOpportunityRadar(buildDraftOpportunityRadar());
        input.scrollIntoView({ behavior: "smooth", block: "center" });
        toast(`${alias} ${t("formulaDraftApplied")}`);
      }

      function applyAllTuningDrafts() {
        const suggestions = actionableTuningSuggestions(buildWeightTuning(buildBrandRadarMatrix()));
        if (!suggestions.length) return;
        suggestions.forEach((entry) => {
          const input = document.querySelector(`[data-brand-weight="${cssEscape(entry.alias)}"]`);
          if (!input) return;
          input.value = clampScore(entry.target_weight);
          updateWeightDraftInput(input);
        });
        updateWeightDirtyState();
        renderBrandRadarViews();
        renderOpportunityRadar(buildDraftOpportunityRadar());
        toast(`${suggestions.length} ${t("tuningBatchApplied")}`);
      }

      function applyWeightScenario(scenario) {
        const rows = buildBrandRadarMatrix();
        let changed = 0;
        rows.forEach((entry) => {
          const input = document.querySelector(`[data-brand-weight="${cssEscape(entry.alias)}"]`);
          if (!input) return;
          const targetWeight = scenarioTargetWeight(entry, scenario);
          if (Number(input.value) !== targetWeight) changed += 1;
          input.value = targetWeight;
          updateWeightDraftInput(input);
        });
        updateWeightDirtyState();
        renderBrandRadarViews();
        renderOpportunityRadar(buildDraftOpportunityRadar());
        toast(`${t(scenarioLabelKey(scenario))} · ${changed} ${t("scenarioApplied")}`);
      }

      function scenarioTargetWeight(entry, scenario) {
        const savedWeight = Number(brandByAlias(entry.alias)?.weight ?? entry.brand_weight) || 0;
        const premium = Number(entry.avg_premium_rate) || 0;
        const sampleCount = Number(entry.sample_count) || 0;
        const isCore = entry.tier === "core" || savedWeight >= 90;
        const isWatch = entry.tier === "watch" || (savedWeight >= 70 && savedWeight < 90);
        let target = savedWeight;
        if (scenario === "release") {
          target = isCore ? 95 : isWatch ? 80 : 65;
          if (premium >= 0.25 && sampleCount >= 2) target += 5;
          if (sampleCount < 2 && isCore) target = Math.max(target, savedWeight);
        } else if (scenario === "premium") {
          if (sampleCount >= 2 && premium >= 0.5) target = 95;
          else if (sampleCount >= 2 && premium >= 0.25) target = isCore ? 95 : 88;
          else if (sampleCount >= 2 && premium >= 0.1) target = isCore ? 90 : 78;
          else if (sampleCount >= 2 && premium < -0.05) target = Math.max(55, savedWeight - 12);
          else target = isCore ? 88 : isWatch ? 72 : 60;
        } else if (scenario === "evidence") {
          if (sampleCount < 2 && isCore) target = 95;
          else if (sampleCount < 2 && isWatch) target = 82;
          else if (sampleCount < 2) target = 68;
          else if (sampleCount >= 5 && premium < 0.1) target = Math.max(60, savedWeight - 5);
          else target = savedWeight;
        }
        return roundWeightStep(target);
      }

      function roundWeightStep(value) {
        return clampScore(Math.round((Number(value) || 0) / 5) * 5);
      }

      function scenarioLabelKey(scenario) {
        return {
          release: "scenarioRelease",
          premium: "scenarioPremium",
          evidence: "scenarioEvidence",
        }[scenario] || "brandWeights";
      }

      function prepareMarketSample(alias) {
        clearSampleTaskHint();
        const select = $("marketBrand");
        if (Array.from(select.options).some((option) => option.value === alias)) {
          select.value = alias;
        }
        $("marketItem").focus();
        $("marketForm").scrollIntoView({ behavior: "smooth", block: "center" });
        toast(`${alias} ${t("tuningSampleReady")}`);
      }

      function prepareKeywordSample(alias, keyword) {
        clearSampleTaskHint();
        prepareKeywordSampleFields(alias, keyword);
        toast(`${alias} · ${keyword} ${t("keywordSampleReady")}`);
      }

      function prepareCoreWatchSample(alias, keyword, actionLabel) {
        prepareKeywordSampleFields(alias, keyword);
        setCoreWatchSampleContext(alias, keyword, actionLabel || "coreWatchActionAnchor");
        setSampleTaskHint(actionLabel || "coreWatchActionAnchor", keyword);
        toast(`${alias} · ${keyword} ${t(actionLabel || "coreWatchActionAnchor")}`);
      }

      function prepareKeywordSampleFields(alias, keyword) {
        const select = $("marketBrand");
        if (Array.from(select.options).some((option) => option.value === alias)) {
          select.value = alias;
        }
        $("marketItem").value = keyword || "";
        $("marketRetail").focus();
        $("marketForm").scrollIntoView({ behavior: "smooth", block: "center" });
        renderSamplePreview();
      }

      function setCoreWatchSampleContext(alias, keyword, actionLabel) {
        const source = $("marketSource");
        const notes = $("marketNotes");
        if (!source.value.trim()) source.value = t("coreWatchTaskSource");
        if (!notes.value.trim()) {
          notes.value = `${t("coreWatchTaskNotePrefix")}: ${alias} ${keyword || ""} · ${t(actionLabel)}`.trim();
        }
      }

      function setSampleTaskHint(actionLabel, keyword) {
        const hint = $("sampleTaskHint");
        hint.classList.add("show");
        hint.innerHTML = `<strong>${escapeHtml(t(actionLabel))}${keyword ? ` · ${escapeHtml(keyword)}` : ""}</strong><span>${escapeHtml(t("sampleTaskAnchorHint"))}</span>`;
      }

      function clearSampleTaskHint() {
        const hint = $("sampleTaskHint");
        hint.classList.remove("show");
        hint.innerHTML = "";
      }

      function updateWeightDirtyState() {
        const draftRows = weightDraftRows();
        const dirtyCount = draftRows.length;
        const dirty = dirtyCount > 0;
        const riskCount = dirty ? weightDraftRisks(draftRows, Infinity).length : 0;
        previewingDraftWeights = dirty;
        const saveButton = $("saveWeightsBtn");
        const resetButton = $("resetWeightsBtn");
        [saveButton, resetButton].forEach((button) => {
          button.dataset.disabled = dirty ? "false" : "true";
          button.disabled = !dirty;
        });
        const status = $("weightDirtyStatus");
        if (status) {
          status.textContent = dirty ? weightDirtyStatusText(dirtyCount, riskCount) : t("weightsClean");
        }
        renderBrandStyleLedger();
        renderWeightDraftAudit(draftRows);
      }

      function weightDirtyStatusText(dirtyCount, riskCount) {
        const base = `${dirtyCount} ${t("weightsDirty")}`;
        return riskCount ? `${base} · ${riskCount} ${t("weightsRisk")}` : base;
      }

      function weightDraftRows() {
        const marketRows = new Map((currentState?.market?.summary?.brands || []).map((row) => [normalizeAlias(row.brand_alias), row]));
        return Array.from(document.querySelectorAll("[data-brand-weight]")).map((input) => {
          const alias = input.dataset.brandWeight || "";
          const brand = brandByAlias(alias) || {};
          const market = marketRows.get(normalizeAlias(alias)) || {};
          const savedWeight = Number(input.dataset.originalWeight) || 0;
          const draftWeight = Number(input.value) || 0;
          return {
            alias,
            name: brand.name || alias,
            tier: brand.tier || "",
            sample_count: Number(market.sample_count) || 0,
            saved_weight: savedWeight,
            draft_weight: draftWeight,
            delta: draftWeight - savedWeight,
          };
        }).filter((row) => row.delta !== 0);
      }

      function renderWeightDraftAudit(rows = weightDraftRows()) {
        const audit = $("weightDraftAudit");
        if (!audit) return;
        if (!rows.length) {
          audit.classList.add("empty");
          audit.innerHTML = `<span class="muted">${escapeHtml(t("weightDraftClean"))}</span>`;
          return;
        }
        audit.classList.remove("empty");
        const stats = weightDraftStats(rows);
        const risks = weightDraftRisks(rows);
        audit.innerHTML = `
          <div class="weight-draft-summary">
            <article class="weight-draft-stat"><strong>${escapeHtml(formatDelta(stats.avgDelta))}</strong><span>${escapeHtml(t("weightDraftAvgDelta"))}</span></article>
            <article class="weight-draft-stat"><strong>${escapeHtml(stats.raised)}</strong><span>${escapeHtml(t("weightDraftRaised"))}</span></article>
            <article class="weight-draft-stat"><strong>${escapeHtml(stats.lowered)}</strong><span>${escapeHtml(t("weightDraftLowered"))}</span></article>
            <article class="weight-draft-stat"><strong>${escapeHtml(stats.maxAlias)} ${escapeHtml(formatDelta(stats.maxDelta))}</strong><span>${escapeHtml(t("weightDraftMaxMove"))}</span></article>
          </div>
          ${risks.length ? `<div class="weight-draft-warnings">${risks.map((risk) => `<article class="weight-draft-warning"><strong>${escapeHtml(risk.alias)} · ${escapeHtml(t(risk.label))}</strong><span>${escapeHtml(t(risk.hint))}</span></article>`).join("")}</div>` : ""}
          <div class="weight-draft-head">
            <strong>${escapeHtml(t("weightDraftAudit"))}</strong>
            <span>${escapeHtml(t("weightDraftSaved"))}</span>
            <span>${escapeHtml(t("weightDraftCurrent"))}</span>
            <span>${escapeHtml(t("weightDraftDelta"))}</span>
          </div>
          <div class="weight-draft-list">
            ${rows.map((row) => `<div class="weight-draft-row">
              <strong>${escapeHtml(row.alias)}</strong>
              <span>${escapeHtml(row.saved_weight)}</span>
              <span>${escapeHtml(row.draft_weight)}</span>
              <span class="weight-draft-delta">${escapeHtml(formatDelta(row.delta))}</span>
            </div>`).join("")}
          </div>
          <span class="muted">${escapeHtml(rows.length)} ${escapeHtml(t("weightDraftChanged"))}</span>
        `;
      }

      function weightDraftStats(rows) {
        const total = rows.length || 0;
        const strongest = [...rows].sort((a, b) => Math.abs(Number(b.delta) || 0) - Math.abs(Number(a.delta) || 0))[0] || {};
        return {
          avgDelta: total ? Math.round(rows.reduce((sum, row) => sum + (Number(row.delta) || 0), 0) / total) : 0,
          raised: rows.filter((row) => Number(row.delta) > 0).length,
          lowered: rows.filter((row) => Number(row.delta) < 0).length,
          maxAlias: strongest.alias || "-",
          maxDelta: Number(strongest.delta) || 0,
        };
      }

      function weightDraftRisks(rows, limit = 4) {
        const risks = [];
        rows.forEach((row) => {
          const saved = Number(row.saved_weight) || 0;
          const draft = Number(row.draft_weight) || 0;
          const delta = Number(row.delta) || 0;
          const isCore = row.tier === "core" || saved >= 90;
          if (isCore && delta <= -10) {
            risks.push({ alias: row.alias, label: "weightDraftRiskCoreDown", hint: "weightDraftRiskCoreDownHint", rank: 4 });
          }
          if ((Number(row.sample_count) || 0) < 2 && delta > 0) {
            risks.push({ alias: row.alias, label: "weightDraftRiskThinRaise", hint: "weightDraftRiskThinRaiseHint", rank: 3 });
          }
          if (Math.abs(delta) >= 15) {
            risks.push({ alias: row.alias, label: "weightDraftRiskLargeMove", hint: "weightDraftRiskLargeMoveHint", rank: 2 });
          }
          if (saved < 70 && draft >= 80) {
            risks.push({ alias: row.alias, label: "weightDraftRiskArchiveJump", hint: "weightDraftRiskArchiveJumpHint", rank: 1 });
          }
        });
        const sorted = risks.sort((a, b) => (Number(b.rank) || 0) - (Number(a.rank) || 0) || String(a.alias).localeCompare(String(b.alias)));
        return Number.isFinite(limit) ? sorted.slice(0, limit) : sorted;
      }

      function renderDraftRiskRadar(rows = weightDraftRows()) {
        const target = $("draftRiskRadar");
        if (!target) return;
        const stats = weightDraftStats(rows);
        const risks = weightDraftRisks(rows);
        const riskScore = draftRiskScore(rows, risks);
        if (!rows.length) {
          target.innerHTML = `
            <article class="draft-risk-brief">
              <strong>0</strong>
              <p>${escapeHtml(t("draftRiskScore"))} · ${escapeHtml(t("draftRiskCleanHint"))}</p>
              <div class="signal-bar" aria-hidden="true"><span style="--score: 0%"></span></div>
              <div class="draft-risk-stats">
                <span><strong>0</strong>${escapeHtml(t("draftRiskChanged"))}</span>
                <span><strong>0</strong>${escapeHtml(t("draftRiskOpen"))}</span>
                <span><strong>-</strong>${escapeHtml(t("draftRiskMaxMove"))}</span>
              </div>
            </article>
            <div class="draft-risk-list">
              <article class="draft-risk-card">
                <strong>${escapeHtml(t("draftRiskClean"))}</strong>
                <p>${escapeHtml(t("draftRiskCleanHint"))}</p>
                <button type="button" class="secondary" data-draft-risk-review>${escapeHtml(t("draftRiskReview"))}</button>
              </article>
            </div>
          `;
          return;
        }
        target.innerHTML = `
          <article class="draft-risk-brief">
            <strong>${escapeHtml(riskScore)}</strong>
            <p>${escapeHtml(t("draftRiskScore"))} · ${escapeHtml(weightDirtyStatusText(rows.length, risks.length))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(riskScore)}%"></span></div>
            <div class="draft-risk-stats">
              <span><strong>${escapeHtml(rows.length)}</strong>${escapeHtml(t("draftRiskChanged"))}</span>
              <span><strong>${escapeHtml(risks.length)}</strong>${escapeHtml(t("draftRiskOpen"))}</span>
              <span><strong>${escapeHtml(stats.maxAlias)} ${escapeHtml(formatDelta(stats.maxDelta))}</strong>${escapeHtml(t("draftRiskMaxMove"))}</span>
            </div>
          </article>
          <div class="draft-risk-list">
            ${risks.length ? risks.map((risk) => `<article class="draft-risk-card">
              <strong>${escapeHtml(risk.alias)}</strong>
              <div>
                <strong>${escapeHtml(t(risk.label))}</strong>
                <p>${escapeHtml(t(risk.hint))}</p>
              </div>
              <span class="pill ${Number(risk.rank) >= 3 ? "warn" : "gold"}">${escapeHtml(risk.rank)}</span>
            </article>`).join("") : `<article class="draft-risk-card"><strong>${escapeHtml(t("draftRiskNoOpen"))}</strong><p>${escapeHtml(t("draftRiskNoOpenHint"))}</p><span class="pill off">0</span></article>`}
            <div class="draft-risk-actions">
              <button type="button" class="secondary" data-draft-risk-review>${escapeHtml(t("draftRiskReview"))}</button>
            </div>
          </div>
        `;
      }

      function draftRiskScore(rows, risks) {
        const maxMove = Math.max(0, ...((rows || []).map((row) => Math.abs(Number(row.delta) || 0))));
        const riskRank = (risks || []).reduce((sum, risk) => sum + (Number(risk.rank) || 0), 0);
        return clampScore(Math.round((rows || []).length * 8 + maxMove * 1.8 + riskRank * 10));
      }

      function renderStylePremiumTape(rows = buildBrandRadarMatrix()) {
        const target = $("stylePremiumTape");
        if (!target) return;
        const lanes = stylePremiumRows(rows);
        const lead = [...lanes].sort((a, b) => (
          (Number(b.heat) || 0) - (Number(a.heat) || 0)
          || (Number(b.avgPremium) || 0) - (Number(a.avgPremium) || 0)
        ))[0] || {};
        target.innerHTML = `
          <article class="style-premium-brief" style="${escapeHtml(styleFamilyVisualStyle(lead.family || "sweet"))}">
            <strong>${escapeHtml(lead.heat || 0)}</strong>
            <p>${escapeHtml(t("stylePremiumHeat"))} · ${escapeHtml(t("stylePremiumTapeHint"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(lead.heat || 0)}%"></span></div>
            <div class="style-premium-stats">
              <span><strong>${escapeHtml(t(styleFamilyLabelKey(lead.family || "sweet")))}</strong>${escapeHtml(t("stylePremiumLead"))}</span>
              <span><strong>${escapeHtml(formatPercent(lead.avgPremium || 0))}</strong>${escapeHtml(t("stylePremiumAvg"))}</span>
              <span><strong>${escapeHtml(lead.samples || 0)}/${escapeHtml(lead.sampleTarget || 0)}</strong>${escapeHtml(t("stylePremiumSamples"))}</span>
              <span><strong>${escapeHtml(lead.premiumSignals || 0)}</strong>${escapeHtml(t("stylePremiumPremiumSignals"))}</span>
            </div>
          </article>
          <div class="style-premium-list">
            ${lanes.map((lane) => `<article class="style-premium-card" data-style-premium="${escapeHtml(lane.family)}" style="${escapeHtml(styleFamilyVisualStyle(lane.family))}">
              <header>
                <div>
                  <strong>${escapeHtml(t(styleFamilyLabelKey(lane.family)))}</strong>
                  <p>${escapeHtml(t("stylePremiumAction"))} · ${escapeHtml(t(stylePremiumActionLabel(lane.action)))}</p>
                </div>
                <span class="pill ${escapeHtml(stylePremiumActionPill(lane.action))}">${escapeHtml(lane.heat)}</span>
              </header>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(lane.heat)}%"></span></div>
              <div class="style-premium-meta">
                <span>${escapeHtml(t("stylePremiumAvg"))} ${escapeHtml(formatPercent(lane.avgPremium))}</span>
                <span>${escapeHtml(t("stylePremiumWeighted"))} ${escapeHtml(lane.weightedHeat)}</span>
                <span>${escapeHtml(t("stylePremiumSamples"))} ${escapeHtml(lane.samples)}/${escapeHtml(lane.sampleTarget)}</span>
                <span>${escapeHtml(t("stylePremiumEvidence"))} ${escapeHtml(lane.evidenceCoverage)}%</span>
                <span>${escapeHtml(t("stylePremiumSpread"))} ${escapeHtml(formatMoney(lane.avgSpread, lane.currency))}</span>
              </div>
            </article>`).join("")}
          </div>
        `;
      }

      function stylePremiumRows(rows) {
        const families = ["sweet", "classic", "gothic", "release", "art"];
        return families.map((family) => {
          const members = (rows || []).filter((entry) => brandStyleFamily(entry) === family);
          const sampled = members.filter((entry) => Number(entry.sample_count) > 0);
          const sampleTargetTotal = members.reduce((sum, entry) => sum + sampleTarget(entry.brand_weight, entry.tier), 0);
          const samples = members.reduce((sum, entry) => sum + (Number(entry.sample_count) || 0), 0);
          const avgWeight = members.length ? Math.round(members.reduce((sum, entry) => sum + (Number(entry.brand_weight) || 0), 0) / members.length) : 0;
          const avgPremium = sampled.length ? sampled.reduce((sum, entry) => sum + (Number(entry.avg_premium_rate) || 0), 0) / sampled.length : 0;
          const spreadRows = members.filter((entry) => Number(entry.avg_spread));
          const avgSpread = spreadRows.length ? Math.round(spreadRows.reduce((sum, entry) => sum + (Number(entry.avg_spread) || 0), 0) / spreadRows.length) : 0;
          const evidenceCoverage = sampleTargetTotal ? clampScore(samples / sampleTargetTotal * 100) : 0;
          const premiumSignals = members.filter((entry) => Number(entry.avg_premium_rate) >= 0.25).length;
          const weightedHeat = clampScore(Math.max(0, avgPremium) * 100 + avgWeight * .34 + evidenceCoverage * .22);
          const lane = {
            family,
            count: members.length,
            avgPremium,
            avgWeight,
            weightedHeat,
            heat: clampScore(weightedHeat + premiumSignals * 6),
            samples,
            sampleTarget: sampleTargetTotal,
            evidenceCoverage,
            premiumSignals,
            avgSpread,
            currency: members.find((entry) => entry.currency)?.currency || "CNY",
          };
          lane.action = stylePremiumAction(lane);
          return lane;
        });
      }

      function stylePremiumAction(lane) {
        if (Number(lane.samples) < Number(lane.sampleTarget) && Number(lane.avgWeight) >= 70) return "collect";
        if (Number(lane.avgPremium) >= .25) return "track";
        if (Number(lane.avgPremium) >= .1) return "watch";
        if (Number(lane.avgPremium) < -.05) return "review";
        return "hold";
      }

      function stylePremiumActionLabel(action) {
        return {
          collect: "stylePremiumCollect",
          track: "stylePremiumTrack",
          watch: "stylePremiumWatch",
          review: "stylePremiumReview",
          hold: "stylePremiumHold",
        }[action] || "stylePremiumHold";
      }

      function stylePremiumActionPill(action) {
        if (action === "collect") return "gold";
        if (action === "track") return "rose";
        if (action === "review") return "warn";
        return "off";
      }

      function buildDraftOpportunityRadar() {
        return buildOpportunityRows().slice(0, 8);
      }

      function buildBrandRadarMatrix() {
        return buildOpportunityRows();
      }

      function renderBrandRadarViews() {
        const rows = buildBrandRadarMatrix();
        renderStyleCompass(rows);
        renderBrandWeightSalon(rows);
        renderNorthStarRadar(rows);
        renderBrandCrownQueue(rows);
        renderDraftRiskRadar();
        renderStylePremiumTape(rows);
        renderDailyRadarBrief(rows);
        renderResaleRunSheet(rows);
        renderBrandPortfolio(rows);
        renderReleaseWatchQueue(rows);
        renderBrandWeightRubric(rows);
        renderBrandPlaybook(rows);
        renderWeightScenarioCompare(rows);
        renderBrandLookbook(rows);
        renderBrandWeightScorecard(rows);
        renderBrandWeightGuardrails(rows);
        renderWeightSnapshot(rows);
        renderBrandWeightStrategy(rows);
        renderWeightTrajectory(rows);
        renderBrandWeightFormula(rows);
        renderBrandWeightProfile(rows);
        renderBrandIdentityMatrix(rows);
        renderCoreMarketWatch(rows);
        renderPremiumSeedRadar(rows);
        renderBrandRadarMatrix(rows);
        renderSampleCoverage(rows);
        renderSamplePlan(rows);
        renderWeightTuning(rows);
        renderPriceDiscipline(rows);
      }

      function renderNorthStarRadar(rows) {
        const target = $("northStarRadar");
        if (!target) return;
        const stats = northStarStats(rows);
        target.innerHTML = `
          <article class="north-star-brief">
            <strong>${escapeHtml(stats.score)}</strong>
            <p>${escapeHtml(t("northStarScore"))} · ${escapeHtml(t("northStarHint"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.score)}%"></span></div>
            <div class="north-star-stats">
              <span><strong>${escapeHtml(stats.lead)}</strong>${escapeHtml(t("northStarLead"))}</span>
              <span><strong>${escapeHtml(stats.weighted_coverage)}%</strong>${escapeHtml(t("northStarWeightedCoverage"))}</span>
              <span><strong>${escapeHtml(stats.release_heat)}</strong>${escapeHtml(t("northStarReleaseHeat"))}</span>
              <span><strong>${escapeHtml(stats.run_sheet_heat)}</strong>${escapeHtml(t("northStarRunSheetHeat"))}</span>
            </div>
          </article>
          <div class="north-star-list">
            ${northStarLanes(stats).map((lane) => `<article class="north-star-card" style="${escapeHtml(lane.style)}">
              <header>
                <div>
                  <strong>${escapeHtml(t(lane.label))}</strong>
                  <p>${escapeHtml(t(lane.detail))}</p>
                </div>
                <span class="north-star-score">${escapeHtml(lane.score)}</span>
              </header>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(lane.score)}%"></span></div>
              <p>${escapeHtml(lane.summary)}</p>
            </article>`).join("")}
          </div>
        `;
      }

      function northStarStats(rows) {
        const portfolio = brandPortfolioStats(rows);
        const releases = releaseWatchRows(rows);
        const runSheet = resaleRunSheetRows(rows);
        const totalWeight = (rows || []).reduce((sum, row) => sum + (Number(row.brand_weight) || 0), 0) || 1;
        const readyWeight = (rows || []).filter((row) => Number(row.sample_count) >= 2).reduce((sum, row) => sum + (Number(row.brand_weight) || 0), 0);
        const weightedCoverage = Math.round(readyWeight / totalWeight * 100);
        const releaseHeat = releases.length ? Math.max(...releases.map((row) => Number(row.release_score) || 0)) : 0;
        const runSheetHeatScore = runSheetHeat(runSheet);
        const hotPremium = (rows || []).filter((row) => Number(row.sample_count) >= 2 && Number(row.avg_premium_rate) >= 0.25);
        const premiumHeat = rows?.length ? Math.round(hotPremium.length / rows.length * 100) : 0;
        const score = clampScore(Math.round(
          (Number(portfolio.health) || 0) * .3
          + weightedCoverage * .25
          + releaseHeat * .2
          + runSheetHeatScore * .15
          + premiumHeat * .1
        ));
        const lead = runSheet[0]?.alias || releases[0]?.alias || rows?.[0]?.alias || "-";
        return {
          score,
          lead,
          portfolio_health: portfolio.health,
          weighted_coverage: weightedCoverage,
          release_heat: releaseHeat,
          run_sheet_heat: runSheetHeatScore,
          premium_heat: premiumHeat,
          release_count: releases.length,
          run_sheet_count: runSheet.length,
          hot_premium_count: hotPremium.length,
          core_gaps: portfolio.core_gaps,
        };
      }

      function northStarLanes(stats) {
        return [
          {
            label: "northStarEvidenceLane",
            detail: "northStarEvidenceDetail",
            score: stats.weighted_coverage,
            summary: `${t("portfolioCoreGaps")} ${stats.core_gaps} · ${t("northStarWeightedCoverage")} ${stats.weighted_coverage}%`,
            style: styleFamilyVisualStyle("classic"),
          },
          {
            label: "northStarReleaseLane",
            detail: "northStarReleaseDetail",
            score: stats.release_heat,
            summary: `${t("releaseWatchSignals")} ${stats.release_count} · ${t("northStarReleaseHeat")} ${stats.release_heat}`,
            style: styleFamilyVisualStyle("release"),
          },
          {
            label: "northStarPremiumLane",
            detail: "northStarPremiumDetail",
            score: stats.premium_heat,
            summary: `${t("portfolioHeat")} ${stats.hot_premium_count} · ${t("northStarPremiumHeat")} ${stats.premium_heat}%`,
            style: styleFamilyVisualStyle("sweet"),
          },
          {
            label: "northStarExecutionLane",
            detail: "northStarExecutionDetail",
            score: stats.run_sheet_heat,
            summary: `${t("runSheetTasks")} ${stats.run_sheet_count} · ${t("northStarRunSheetHeat")} ${stats.run_sheet_heat}`,
            style: styleFamilyVisualStyle("gothic"),
          },
        ];
      }

      function renderBrandCrownQueue(rows) {
        const target = $("brandCrownQueue");
        if (!target) return;
        const entries = brandCrownRows(rows);
        if (!entries.length) {
          target.innerHTML = `<div class="row">${escapeHtml(t("crownNoRows"))}</div>`;
          return;
        }
        const stats = brandCrownStats(entries, rows);
        target.innerHTML = `
          <article class="crown-brief">
            <strong>${escapeHtml(stats.top_score)}</strong>
            <p>${escapeHtml(t("crownScore"))} · ${escapeHtml(t("brandCrownHint"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.top_score)}%"></span></div>
            <div class="crown-stats">
              <span><strong>${escapeHtml(stats.lead)}</strong>${escapeHtml(t("crownLead"))}</span>
              <span><strong>${escapeHtml(stats.core_ready)}</strong>${escapeHtml(t("crownCoreReady"))}</span>
              <span><strong>${escapeHtml(stats.release_signals)}</strong>${escapeHtml(t("crownReleaseSignals"))}</span>
              <span><strong>${escapeHtml(stats.keyword_total)}</strong>${escapeHtml(t("crownKeywordTotal"))}</span>
            </div>
          </article>
          <div class="crown-list">
            ${entries.map(crownCardHtml).join("")}
          </div>
        `;
      }

      function crownCardHtml(entry) {
        const links = marketSearchLinks({ ...entry, keyword: entry.primary_keyword || entry.keywords?.[0] || entry.alias }).slice(0, 2);
        return `<article class="crown-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)}</strong>
              <p>${escapeHtml(entry.name || entry.alias)} · ${escapeHtml(t(entry.action_label))}</p>
            </div>
            <div class="crown-score-stack">
              <span class="crown-score">${escapeHtml(entry.crown_score)}</span>
              <span class="pill ${escapeHtml(entry.confidence_band)}">${escapeHtml(t(entry.confidence_label))} ${escapeHtml(entry.confidence_score)}</span>
            </div>
          </header>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(entry.crown_score)}%"></span></div>
          <div class="crown-meta">
            <span>${escapeHtml(t("weightLabel"))} ${escapeHtml(entry.brand_weight)}</span>
            <span>${escapeHtml(t("formulaTarget"))} ${escapeHtml(entry.target_weight)} ${escapeHtml(formatDelta(entry.weight_delta))}</span>
            <span>${escapeHtml(t("formulaConfidence"))} ${escapeHtml(entry.formula_confidence)}%</span>
            <span>${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))}</span>
            <span>${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)}/${escapeHtml(entry.target_samples)}</span>
            <span>${escapeHtml(t("crownReleaseSignals"))} ${escapeHtml(entry.release_score)}</span>
          </div>
          <div class="crown-keywords">
            ${entry.keywords.length ? entry.keywords.map((keyword) => `<span>${escapeHtml(keyword)}</span>`).join("") : `<span>${escapeHtml(entry.alias)}</span>`}
          </div>
          <p>${escapeHtml(entry.visual?.radar_cue || entry.style || t("crownAction"))}</p>
          <div class="crown-actions">
            <button type="button" data-crown-sample="${escapeHtml(entry.alias)}">${escapeHtml(t("crownSample"))}</button>
            <button type="button" data-crown-keyword-sample="${escapeHtml(entry.alias)}" data-crown-keyword="${escapeHtml(entry.primary_keyword || entry.keywords?.[0] || entry.alias)}">${escapeHtml(t("crownKeywordSample"))}</button>
            ${Number(entry.weight_delta) ? `<button type="button" data-crown-apply="${escapeHtml(entry.alias)}" data-crown-target="${escapeHtml(entry.target_weight)}">${escapeHtml(t("formulaApplyDraft"))}</button>` : ""}
            ${entry.release_score ? `<button type="button" data-crown-jump="releaseWatchQueue">${escapeHtml(t("crownOpenRelease"))}</button>` : ""}
            ${links.map((link) => `<a href="${escapeHtml(link.href)}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>`).join("")}
          </div>
        </article>`;
      }

      function brandCrownRows(rows) {
        const releases = releaseWatchRows(rows);
        const formulaByAlias = new Map(buildBrandWeightFormula(rows, Number.POSITIVE_INFINITY).map((entry) => [entry.alias, entry]));
        const releaseByAlias = new Map();
        releases.forEach((entry) => {
          const current = releaseByAlias.get(entry.alias);
          if (!current || Number(entry.release_score) > Number(current.release_score)) {
            releaseByAlias.set(entry.alias, entry);
          }
        });
        return (rows || []).map((entry) => {
          const release = releaseByAlias.get(entry.alias) || {};
          const formula = formulaByAlias.get(entry.alias) || {};
          const weight = Number(entry.brand_weight) || 0;
          const premium = Number(entry.avg_premium_rate) || 0;
          const sampleCount = Number(entry.sample_count) || 0;
          const targetSamples = sampleTarget(weight, entry.tier);
          const targetWeight = Number(formula.target_weight ?? weight) || 0;
          const releaseScore = Number(release.release_score) || 0;
          const keywords = (entry.market_keywords || []).slice(0, 4);
          const sampleGapBonus = sampleCount < 2 && weight >= 75 ? 9 : 0;
          const keywordBonus = Math.min(8, keywords.length * 2);
          const crownScore = clampScore(
            weight * .42
            + Math.max(0, premium) * 36
            + releaseScore * .2
            + evidenceScore(sampleCount) * .12
            + sampleGapBonus
            + keywordBonus
          );
          const confidenceScore = crownConfidenceScore({ ...entry, sample_count: sampleCount, release_score: releaseScore });
          return {
            ...entry,
            target_samples: targetSamples,
            release_score: releaseScore,
            primary_keyword: release.primary_keyword || keywords[0] || entry.alias,
            keywords,
            crown_score: crownScore,
            target_weight: targetWeight,
            weight_delta: targetWeight - weight,
            formula_confidence: Number(formula.confidence ?? formulaConfidence(entry)) || 0,
            confidence_score: confidenceScore,
            confidence_band: crownConfidenceBand(confidenceScore),
            confidence_label: crownConfidenceLabel(confidenceScore),
            action_label: crownActionLabel({ ...entry, release_score: releaseScore, target_samples: targetSamples, crown_score: crownScore }),
          };
        }).filter((entry) => Number(entry.brand_weight) >= 75 || Number(entry.avg_premium_rate) >= 0.25 || Number(entry.release_score) >= 55)
          .sort((a, b) => (
            (Number(b.crown_score) || 0) - (Number(a.crown_score) || 0)
            || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
            || String(a.alias).localeCompare(String(b.alias))
          )).slice(0, 6);
      }

      function brandCrownStats(entries, rows) {
        const coreRows = (rows || []).filter((entry) => entry.tier === "core" || Number(entry.brand_weight) >= 90);
        const readyCore = coreRows.filter((entry) => Number(entry.sample_count) >= 2).length;
        return {
          top_score: entries.length ? Math.max(...entries.map((entry) => Number(entry.crown_score) || 0)) : 0,
          lead: entries[0]?.alias || "-",
          core_ready: `${readyCore}/${coreRows.length || 0}`,
          release_signals: entries.filter((entry) => Number(entry.release_score) > 0).length,
          keyword_total: entries.reduce((sum, entry) => sum + (entry.keywords?.length || 0), 0),
          premium_backed: entries.filter((entry) => Number(entry.avg_premium_rate) >= 0.25).length,
        };
      }

      function crownActionLabel(entry) {
        const weight = Number(entry.brand_weight) || 0;
        const samples = Number(entry.sample_count) || 0;
        const releaseScore = Number(entry.release_score) || 0;
        const premium = Number(entry.avg_premium_rate) || 0;
        if (samples < 2 && weight >= 75) return "crownActionAnchor";
        if (releaseScore >= 60) return "crownActionRelease";
        if (premium >= 0.25) return "crownActionPremium";
        return "crownActionHold";
      }

      function crownConfidenceScore(entry) {
        const samplePoints = Math.min(50, (Number(entry.sample_count) || 0) * 14);
        const watchPoints = Math.min(20, (entry.watch_urls || []).length * 5);
        const keywordPoints = Math.min(15, (entry.market_keywords || []).length * 3);
        const releasePoints = Number(entry.release_score) > 0 ? 10 : 0;
        const premiumPoints = Number(entry.avg_premium_rate) >= 0.25 ? 5 : 0;
        return clampScore(samplePoints + watchPoints + keywordPoints + releasePoints + premiumPoints);
      }

      function crownConfidenceBand(score) {
        const value = Number(score) || 0;
        if (value >= 70) return "rose";
        if (value >= 45) return "gold";
        return "warn";
      }

      function crownConfidenceLabel(score) {
        const value = Number(score) || 0;
        if (value >= 70) return "crownConfidenceHigh";
        if (value >= 45) return "crownConfidenceMedium";
        return "crownConfidenceLow";
      }

      function renderBrandPortfolio(rows) {
        const stats = brandPortfolioStats(rows);
        const lanes = brandPortfolioLanes(rows, stats);
        $("brandPortfolio").innerHTML = `
          <article class="portfolio-brief">
            <strong>${escapeHtml(stats.health)}</strong>
            <p>${escapeHtml(t("portfolioHealth"))} · ${escapeHtml(t("brandPortfolioHint"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.health)}%"></span></div>
            <div class="portfolio-stats">
              <span><strong>${escapeHtml(stats.coverage)}%</strong>${escapeHtml(t("portfolioCoverage"))}</span>
              <span><strong>${escapeHtml(stats.core_gaps)}</strong>${escapeHtml(t("portfolioCoreGaps"))}</span>
              <span><strong>${escapeHtml(stats.hot_count)}</strong>${escapeHtml(t("portfolioHeat"))}</span>
              <span><strong>${escapeHtml(stats.drift_count)}</strong>${escapeHtml(t("portfolioDrift"))}</span>
            </div>
          </article>
          <div class="portfolio-list">
            ${lanes.map((lane) => `<article class="portfolio-card" style="${escapeHtml(brandVisualStyle(lane.lead_brand || {}))}">
              <header>
                <div>
                  <strong>${escapeHtml(t(lane.label))}</strong>
                  <p>${escapeHtml(t(lane.hint))}</p>
                </div>
                <span class="pill ${escapeHtml(lane.tone)}">${escapeHtml(lane.count)}</span>
              </header>
              <div class="portfolio-meter">
                <strong>${escapeHtml(lane.score)}</strong>
                <div>
                  <p>${escapeHtml(lane.detail)}</p>
                  <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(lane.score)}%"></span></div>
                </div>
              </div>
              <div class="portfolio-actions">
                <button type="button" class="secondary" data-portfolio-jump="${escapeHtml(lane.target)}">${escapeHtml(t("portfolioReview"))}</button>
                ${lane.sample_alias ? `<button type="button" class="secondary" data-portfolio-sample="${escapeHtml(lane.sample_alias)}">${escapeHtml(t("portfolioSample"))}</button>` : ""}
              </div>
            </article>`).join("")}
          </div>
        `;
      }

      function brandPortfolioStats(rows) {
        const total = (rows || []).length;
        const ready = (rows || []).filter((entry) => Number(entry.sample_count) >= 2).length;
        const coverage = total ? Math.round(ready / total * 100) : 0;
        const coreGaps = (rows || []).filter((entry) => Number(entry.brand_weight) >= 90 && Number(entry.sample_count) < 2).length;
        const hotCount = (rows || []).filter((entry) => Number(entry.sample_count) >= 2 && Number(entry.avg_premium_rate) >= 0.25).length;
        const formulas = buildBrandWeightFormula(rows, Array.isArray(rows) ? rows.length : 0);
        const driftCount = formulas.filter((entry) => Math.abs(Number(entry.delta) || 0) >= 5).length;
        const health = clampScore(Math.round(coverage * .45 + Math.max(0, 100 - coreGaps * 18) * .3 + Math.max(0, 100 - driftCount * 10) * .25));
        return { total, coverage, core_gaps: coreGaps, hot_count: hotCount, drift_count: driftCount, health, formulas };
      }

      function brandPortfolioLanes(rows, stats) {
        const sortedGaps = [...(rows || [])].filter((entry) => Number(entry.sample_count) < 2).sort((a, b) => (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0));
        const coreGaps = sortedGaps.filter((entry) => Number(entry.brand_weight) >= 90);
        const hotRows = [...(rows || [])].filter((entry) => Number(entry.sample_count) >= 2 && Number(entry.avg_premium_rate) >= 0.25).sort((a, b) => (Number(b.avg_premium_rate) || 0) - (Number(a.avg_premium_rate) || 0));
        const driftRows = [...(stats.formulas || [])].filter((entry) => Math.abs(Number(entry.delta) || 0) >= 5).sort((a, b) => Math.abs(Number(b.delta) || 0) - Math.abs(Number(a.delta) || 0));
        return [
          {
            label: "portfolioEvidenceLane",
            hint: "portfolioEvidenceHint",
            count: sortedGaps.length,
            score: stats.coverage,
            detail: `${t("coverageProgress")} ${stats.coverage}% · ${t("portfolioActions")} ${sortedGaps.length}`,
            tone: stats.coverage < 50 ? "gold" : "",
            target: "samplePlan",
            sample_alias: sortedGaps[0]?.alias || "",
            lead_brand: sortedGaps[0] || null,
          },
          {
            label: "portfolioCoreLane",
            hint: "portfolioCoreHint",
            count: coreGaps.length,
            score: clampScore(100 - coreGaps.length * 22),
            detail: `${t("portfolioCoreGaps")} ${coreGaps.length} · ${t("dailyLead")} ${coreGaps[0]?.alias || "-"}`,
            tone: coreGaps.length ? "gold" : "off",
            target: "coreMarketWatch",
            sample_alias: coreGaps[0]?.alias || "",
            lead_brand: coreGaps[0] || null,
          },
          {
            label: "portfolioPremiumLane",
            hint: "portfolioPremiumHint",
            count: hotRows.length,
            score: hotRows.length ? clampScore(Math.round((Number(hotRows[0].avg_premium_rate) || 0) * 100)) : 0,
            detail: `${t("portfolioHeat")} ${hotRows.length} · ${t("dailyLead")} ${hotRows[0]?.alias || "-"}`,
            tone: hotRows.length ? "rose" : "off",
            target: "brandRadarMatrix",
            sample_alias: "",
            lead_brand: hotRows[0] || null,
          },
          {
            label: "portfolioDriftLane",
            hint: "portfolioDriftHint",
            count: driftRows.length,
            score: driftRows.length ? clampScore(100 - Math.min(80, Math.abs(Number(driftRows[0].delta) || 0) * 5)) : 100,
            detail: `${t("portfolioDrift")} ${driftRows.length} · ${t("dailyLead")} ${driftRows[0]?.alias || "-"}`,
            tone: driftRows.length ? "warn" : "off",
            target: "brandWeightFormula",
            sample_alias: "",
            lead_brand: driftRows[0] || null,
          },
        ];
      }

      function renderReleaseWatchQueue(rows) {
        const releases = releaseWatchRows(rows);
        const stats = releaseWatchStats(releases);
        $("releaseWatchQueue").innerHTML = releases.length ? `
          <article class="release-brief">
            <strong>${escapeHtml(stats.top_score)}</strong>
            <p>${escapeHtml(t("releaseWatchTopScore"))} · ${escapeHtml(t("releaseWatchHint"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.top_score)}%"></span></div>
            <div class="release-stats">
              <span><strong>${escapeHtml(releases.length)}</strong>${escapeHtml(t("releaseWatchSignals"))}</span>
              <span><strong>${escapeHtml(stats.brand_count)}</strong>${escapeHtml(t("releaseWatchBrands"))}</span>
              <span><strong>${escapeHtml(stats.premium_backed)}</strong>${escapeHtml(t("releaseWatchPremium"))}</span>
              <span><strong>${escapeHtml(stats.sample_gaps)}</strong>${escapeHtml(t("dailySampleGaps"))}</span>
            </div>
          </article>
          <div class="release-list">
            ${releases.map(releaseWatchCardHtml).join("")}
          </div>
        ` : `<div class="row">${escapeHtml(t("releaseWatchNoRows"))}</div>`;
      }

      function releaseWatchCardHtml(entry) {
        const links = marketSearchLinks({ ...entry, keyword: entry.primary_keyword || entry.alias }).slice(0, 4);
        return `<article class="release-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)} · ${escapeHtml(entry.title)}</strong>
              <p>${escapeHtml(t("releaseWatchSource"))} ${escapeHtml(entry.source)} · ${escapeHtml(valueLabel("status", entry.status))}</p>
            </div>
            <span class="pill ${escapeHtml(releaseWatchPill(entry.action_label))}">${escapeHtml(t(entry.action_label))}</span>
          </header>
          <div class="release-score">
            <strong>${escapeHtml(entry.release_score)}</strong>
            <div>
              <p>${escapeHtml(t("releaseWatchScore"))} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(entry.brand_weight)} · ${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))}</p>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(entry.release_score)}%"></span></div>
            </div>
          </div>
          <div class="release-meta">
            <span>${escapeHtml(t("releaseWatchMatched"))} ${escapeHtml(entry.matched_terms.join(" / ") || "-")}</span>
            <span>${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)}</span>
            <span>${escapeHtml(entry.published_at || t("undated"))}</span>
          </div>
          <div class="release-actions">
            ${entry.url ? `<a href="${escapeHtml(entry.url)}" target="_blank" rel="noreferrer">${escapeHtml(t("releaseWatchOpen"))}</a>` : ""}
            <button type="button" data-release-sample="${escapeHtml(entry.alias)}" data-release-keyword="${escapeHtml(entry.primary_keyword || entry.alias)}">${escapeHtml(t("releaseWatchSample"))}</button>
            ${links.map((link) => `<a href="${escapeHtml(link.href)}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>`).join("")}
          </div>
        </article>`;
      }

      function releaseWatchRows(rows) {
        return (currentState?.items || []).flatMap((item) => {
          const matches = releaseBrandMatches(item, rows).slice(0, 1);
          return matches.map((entry) => {
            const sampleCount = Number(entry.sample_count) || 0;
            const premium = Number(entry.avg_premium_rate) || 0;
            const weight = Number(entry.brand_weight) || 0;
            const releaseScore = clampScore(Math.round(
              weight * .42
              + releaseStatusScore(item.status)
              + Math.max(0, premium) * 30
              + (sampleCount < 2 && weight >= 70 ? 10 : 0)
              + Math.min(10, entry.matched_terms.length * 3)
            ));
            return {
              ...entry,
              title: item.title || "",
              url: safeUrl(item.url),
              source: item.source || "",
              status: item.status || "",
              published_at: item.published_at || item.last_seen_at || "",
              primary_keyword: releasePrimaryKeyword(entry, entry.matched_terms),
              release_score: releaseScore,
              action_label: releaseWatchAction(entry, item),
            };
          });
        }).sort((a, b) => (
          (Number(b.release_score) || 0) - (Number(a.release_score) || 0)
          || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
        )).slice(0, 6);
      }

      function releaseBrandMatches(item, rows) {
        const haystack = [item.source, item.title, item.url, item.status].map((value) => String(value || "").toLowerCase()).join(" ");
        return (rows || []).map((entry) => {
          const terms = releaseWatchTerms(entry);
          const matchedTerms = terms.filter((term) => releaseKeywordMatches(term, haystack)).slice(0, 4);
          return matchedTerms.length ? { ...entry, matched_terms: matchedTerms } : null;
        }).filter(Boolean).sort((a, b) => (
          (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
          || (b.matched_terms.length || 0) - (a.matched_terms.length || 0)
        ));
      }

      function releaseWatchTerms(entry) {
        return uniqueValues([
          entry.alias,
          entry.name,
          ...(entry.keywords || []),
          ...(entry.market_keywords || []),
        ]);
      }

      function releaseKeywordMatches(term, haystack) {
        const keyword = String(term || "").trim().toLowerCase();
        if (!keyword) return false;
        if (/^[a-z0-9 -]+$/.test(keyword) && keyword.replaceAll("-", "").replaceAll(" ", "").length <= 3) {
          return new RegExp(`(^|[^a-z0-9])${escapeRegExp(keyword)}([^a-z0-9]|$)`).test(haystack);
        }
        return haystack.includes(keyword);
      }

      function releasePrimaryKeyword(entry, matchedTerms) {
        const matched = new Set((matchedTerms || []).map((term) => String(term).toLowerCase()));
        return (entry.market_keywords || []).find((term) => matched.has(String(term).toLowerCase()))
          || matchedTerms?.[0]
          || entry.alias;
      }

      function releaseWatchStats(rows) {
        return {
          top_score: rows.length ? Math.max(...rows.map((row) => Number(row.release_score) || 0)) : 0,
          brand_count: new Set(rows.map((row) => row.alias)).size,
          premium_backed: rows.filter((row) => Number(row.avg_premium_rate) >= 0.25).length,
          sample_gaps: rows.filter((row) => Number(row.sample_count) < 2).length,
        };
      }

      function releaseStatusScore(status) {
        return {
          preorder: 18,
          restock: 16,
          new_arrival: 14,
          shop_news: 8,
        }[status] || 8;
      }

      function releaseWatchAction(entry, item) {
        if (Number(entry.sample_count) < 2) return "releaseActionSample";
        if (Number(entry.avg_premium_rate) >= 0.25) return "releaseActionTrackPremium";
        if (["preorder", "restock", "new_arrival"].includes(item.status)) return "releaseActionWatchDrop";
        return "releaseActionReview";
      }

      function releaseWatchPill(actionLabel) {
        if (actionLabel === "releaseActionSample") return "gold";
        if (actionLabel === "releaseActionTrackPremium") return "rose";
        if (actionLabel === "releaseActionReview") return "off";
        return "";
      }

      function renderBrandPlaybook(rows) {
        const cards = brandPlaybookRows(rows);
        $("brandPlaybook").innerHTML = cards.length ? cards.map((entry) => {
          const links = marketSearchLinks({ ...entry, keyword: entry.primary_term || entry.alias }).slice(0, 4);
          return `<article class="playbook-card" style="${escapeHtml(brandVisualStyle(entry))}">
            <header>
              <div>
                <strong>${escapeHtml(entry.alias)} · ${escapeHtml(entry.name)}</strong>
                <p>${escapeHtml(t("playbookPrimaryTerm"))} ${escapeHtml(entry.primary_term || "-")}</p>
              </div>
              <span class="pill ${escapeHtml(entry.action_tone)}">${escapeHtml(t(entry.action_label))}</span>
            </header>
            <div class="playbook-score">
              <strong>${escapeHtml(entry.brand_weight)}</strong>
              <div>
                <p>${escapeHtml(t("weightLabel"))} · ${escapeHtml(t("playbookTarget"))} ${escapeHtml(entry.target_weight)}</p>
                <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(entry.brand_weight)}%"></span></div>
              </div>
            </div>
            <div class="playbook-stats">
              <span><strong>${escapeHtml(formatPercent(entry.avg_premium_rate))}</strong>${escapeHtml(t("avgPremium"))}</span>
              <span><strong>${escapeHtml(entry.sample_count)}</strong>${escapeHtml(t("samples"))}</span>
              <span><strong>${escapeHtml(entry.confidence)}%</strong>${escapeHtml(t("formulaConfidence"))}</span>
            </div>
            <p>${escapeHtml(t("playbookAction"))} · ${escapeHtml(t(entry.action_label))}</p>
            <div class="playbook-reasons">
              ${entry.reason_labels.map((label) => `<span>${escapeHtml(t(label))}</span>`).join("")}
            </div>
            <div class="playbook-actions">
              ${Number(entry.sample_count) < 2 ? `<button type="button" class="secondary" data-playbook-sample="${escapeHtml(entry.alias)}">${escapeHtml(t("playbookSample"))}</button>` : ""}
              ${entry.primary_term ? `<button type="button" class="secondary" data-playbook-keyword-brand="${escapeHtml(entry.alias)}" data-playbook-keyword="${escapeHtml(entry.primary_term)}">${escapeHtml(t("playbookKeyword"))}</button>` : ""}
              ${Number(entry.target_weight) !== Number(entry.brand_weight) ? `<button type="button" class="secondary" data-playbook-apply="${escapeHtml(entry.alias)}" data-playbook-target="${escapeHtml(entry.target_weight)}">${escapeHtml(t("playbookApply"))}</button>` : ""}
              ${links.map((link) => `<a href="${escapeHtml(link.href)}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>`).join("")}
            </div>
          </article>`;
        }).join("") : `<div class="row">${escapeHtml(t("playbookNoRows"))}</div>`;
      }

      function brandPlaybookRows(rows) {
        const formulaByAlias = new Map(buildBrandWeightFormula(rows, Array.isArray(rows) ? rows.length : 0).map((entry) => [entry.alias, entry]));
        return (rows || []).map((entry) => {
          const formula = formulaByAlias.get(entry.alias) || {};
          const primaryTerm = (entry.market_keywords || [])[0] || entry.alias;
          const action = playbookAction(entry, formula);
          return {
            ...entry,
            primary_term: primaryTerm,
            target_weight: formula.target_weight ?? entry.brand_weight,
            confidence: formula.confidence ?? formulaConfidence(entry),
            action_label: action.label,
            action_rank: action.rank,
            action_tone: action.tone,
            reason_labels: playbookReasons(entry, formula),
          };
        }).sort((a, b) => (
          (Number(b.action_rank) || 0) - (Number(a.action_rank) || 0)
          || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
          || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0)
        )).slice(0, 6);
      }

      function playbookAction(entry, formula) {
        const samples = Number(entry.sample_count) || 0;
        const premium = Number(entry.avg_premium_rate) || 0;
        const target = Number(formula.target_weight ?? entry.brand_weight) || 0;
        const weight = Number(entry.brand_weight) || 0;
        if (samples <= 0 && weight >= 70) return { label: "playbookActionAnchor", rank: 6, tone: "gold" };
        if (samples < 2 && weight >= 70) return { label: "playbookActionPair", rank: 5, tone: "gold" };
        if (premium >= 0.35) return { label: "playbookActionTrack", rank: 4, tone: "rose" };
        if (target > weight) return { label: "playbookActionRaise", rank: 3, tone: "rose" };
        if (target < weight || premium < -0.05) return { label: "playbookActionCool", rank: 2, tone: "warn" };
        return { label: "playbookActionHold", rank: 1, tone: "off" };
      }

      function playbookReasons(entry, formula) {
        const reasons = [];
        const weight = Number(entry.brand_weight) || 0;
        const samples = Number(entry.sample_count) || 0;
        const premium = Number(entry.avg_premium_rate) || 0;
        const target = Number(formula.target_weight ?? entry.brand_weight) || 0;
        if (weight >= 90 || entry.tier === "core") reasons.push("playbookReasonCore");
        if (samples < 2) reasons.push("playbookReasonThin");
        if (premium >= 0.25) reasons.push("playbookReasonPremium");
        if (premium < -0.05) reasons.push("playbookReasonDiscount");
        if (target !== weight) reasons.push("playbookReasonTarget");
        if ((entry.market_keywords || []).length) reasons.push("playbookReasonKeyword");
        return reasons.slice(0, 4);
      }

      function renderBrandWeightRubric(rows) {
        const lanes = brandWeightRubricRows(rows);
        $("brandWeightRubric").innerHTML = lanes.length ? lanes.map((lane) => `<article class="rubric-card" style="${escapeHtml(brandVisualStyle(lane.lead_brand || lane))}">
          <header>
            <div>
              <strong>${escapeHtml(t(lane.label))}</strong>
              <p>${escapeHtml(t("rubricRange"))} ${escapeHtml(lane.range)}</p>
            </div>
            <span class="pill ${escapeHtml(lane.tone)}">${escapeHtml(lane.count)}</span>
          </header>
          <div class="rubric-score">
            <strong>${escapeHtml(lane.avg_weight)}</strong>
            <div>
              <p>${escapeHtml(t("rubricAvgWeight"))} · ${escapeHtml(t("rubricBrands"))} ${escapeHtml(lane.count)}</p>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(lane.avg_weight)}%"></span></div>
            </div>
          </div>
          <div class="rubric-stats">
            <span><strong>${escapeHtml(formatPercent(lane.avg_premium))}</strong>${escapeHtml(t("rubricAvgPremium"))}</span>
            <span><strong>${escapeHtml(lane.sample_gaps)}</strong>${escapeHtml(t("rubricSampleGaps"))}</span>
            <span><strong>${escapeHtml(lane.lead_brand?.alias || "-")}</strong>${escapeHtml(t("dailyLead"))}</span>
          </div>
          <p>${escapeHtml(t(lane.hint))}</p>
          <div class="rubric-brands">
            ${lane.members.length ? lane.members.slice(0, 5).map((brand) => `<span>${escapeHtml(brand.alias)} · ${escapeHtml(brand.brand_weight)}</span>`).join("") : `<span>${escapeHtml(t("rubricNoBrands"))}</span>`}
          </div>
          <div class="rubric-actions">
            <button type="button" class="secondary" data-rubric-jump="brandWeights">${escapeHtml(t("rubricReviewWeights"))}</button>
            ${lane.sample_alias ? `<button type="button" class="secondary" data-rubric-sample="${escapeHtml(lane.sample_alias)}">${escapeHtml(t("rubricSampleGap"))}</button>` : ""}
          </div>
        </article>`).join("") : `<div class="row">${escapeHtml(t("rubricNoBrands"))}</div>`;
      }

      function brandWeightRubricRows(rows) {
        const definitions = [
          { key: "core", label: "rubricCore", hint: "rubricCoreHint", range: "90-100", min: 90, max: 101, tone: "rose" },
          { key: "lead", label: "rubricLead", hint: "rubricLeadHint", range: "75-89", min: 75, max: 90, tone: "gold" },
          { key: "seed", label: "rubricSeed", hint: "rubricSeedHint", range: "60-74", min: 60, max: 75, tone: "" },
          { key: "archive", label: "rubricArchive", hint: "rubricArchiveHint", range: "0-59", min: 0, max: 60, tone: "off" },
        ];
        return definitions.map((lane) => {
          const members = (rows || []).filter((entry) => {
            const weight = Number(entry.brand_weight) || 0;
            return weight >= lane.min && weight < lane.max;
          }).sort((a, b) => (
            (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
            || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0)
          ));
          const count = members.length;
          const avgWeight = count ? Math.round(members.reduce((sum, entry) => sum + (Number(entry.brand_weight) || 0), 0) / count) : 0;
          const avgPremium = count ? members.reduce((sum, entry) => sum + (Number(entry.avg_premium_rate) || 0), 0) / count : 0;
          const gaps = members.filter((entry) => Number(entry.sample_count) < sampleTarget(entry.brand_weight, entry.tier));
          return {
            ...lane,
            members,
            count,
            avg_weight: avgWeight,
            avg_premium: avgPremium,
            sample_gaps: gaps.length,
            lead_brand: members[0] || null,
            sample_alias: gaps[0]?.alias || "",
          };
        });
      }

      function renderWeightScenarioCompare(rows) {
        const scenarios = ["release", "premium", "evidence"].map((scenario) => weightScenarioSummary(rows, scenario));
        $("weightScenarioCompare").innerHTML = scenarios.map((scenario) => `<article class="scenario-card">
          <header>
            <div>
              <strong>${escapeHtml(t(scenario.label))}</strong>
              <p>${escapeHtml(t("scenarioTopMoves"))}</p>
            </div>
            <span class="pill ${scenarioPill(scenario.key)}">${escapeHtml(scenario.changed)}</span>
          </header>
          <div class="scenario-score">
            <strong>${escapeHtml(scenario.avgTarget)}</strong>
            <div>
              <p>${escapeHtml(t("scenarioAvgTarget"))} · ${escapeHtml(t("scenarioChanged"))} ${escapeHtml(scenario.changed)}</p>
              <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(scenario.avgTarget)}%"></span></div>
            </div>
          </div>
          <div class="scenario-stats">
            <article class="scenario-stat"><strong>${escapeHtml(scenario.raised)}</strong><span>${escapeHtml(t("scenarioRaised"))}</span></article>
            <article class="scenario-stat"><strong>${escapeHtml(scenario.lowered)}</strong><span>${escapeHtml(t("scenarioLowered"))}</span></article>
            <article class="scenario-stat"><strong>${escapeHtml(scenario.maxDeltaText)}</strong><span>${escapeHtml(t("weightDraftMaxMove"))}</span></article>
          </div>
          <div class="scenario-moves">
            ${scenario.moves.length ? scenario.moves.map((move) => `<div class="scenario-move" style="${escapeHtml(brandVisualStyle(move))}">
              <strong>${escapeHtml(move.alias)}</strong>
              <span>${escapeHtml(move.saved_weight)}</span>
              <span>${escapeHtml(move.target_weight)}</span>
              <span class="scenario-delta">${escapeHtml(formatDelta(move.delta))}</span>
            </div>`).join("") : `<div class="row">${escapeHtml(t("scenarioNoMoves"))}</div>`}
          </div>
          <button type="button" class="secondary" data-scenario-preview-apply="${escapeHtml(scenario.key)}">${escapeHtml(t("scenarioApplyDraft"))}</button>
        </article>`).join("");
      }

      function weightScenarioSummary(rows, scenario) {
        const moves = (rows || []).map((entry) => {
          const savedWeight = Number(brandByAlias(entry.alias)?.weight ?? entry.brand_weight) || 0;
          const targetWeight = scenarioTargetWeight(entry, scenario);
          return {
            ...entry,
            saved_weight: savedWeight,
            target_weight: targetWeight,
            delta: targetWeight - savedWeight,
          };
        });
        const changedMoves = moves.filter((entry) => Number(entry.delta) !== 0);
        const avgTarget = moves.length ? Math.round(moves.reduce((sum, entry) => sum + (Number(entry.target_weight) || 0), 0) / moves.length) : 0;
        const maxMove = changedMoves
          .sort((a, b) => Math.abs(Number(b.delta) || 0) - Math.abs(Number(a.delta) || 0))[0];
        return {
          key: scenario,
          label: scenarioLabelKey(scenario),
          avgTarget,
          changed: changedMoves.length,
          raised: changedMoves.filter((entry) => Number(entry.delta) > 0).length,
          lowered: changedMoves.filter((entry) => Number(entry.delta) < 0).length,
          maxDeltaText: maxMove ? `${maxMove.alias} ${formatDelta(maxMove.delta)}` : "-",
          moves: changedMoves
            .sort((a, b) => (
              Math.abs(Number(b.delta) || 0) - Math.abs(Number(a.delta) || 0)
              || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
            ))
            .slice(0, 4),
        };
      }

      function scenarioPill(scenario) {
        if (scenario === "premium") return "rose";
        if (scenario === "evidence") return "gold";
        return "off";
      }

      function renderCoreMarketWatch(rows) {
        const watchRows = coreMarketWatchRows(rows);
        const thinCount = watchRows.filter((entry) => Number(entry.sample_count) < 2).length;
        const anchorGaps = watchRows.filter((entry) => !hasCoreWatchPriceAnchor(entry)).length;
        const avgScore = watchRows.length ? Math.round(watchRows.reduce((sum, entry) => sum + (Number(entry.watch_score) || 0), 0) / watchRows.length) : 0;
        $("coreMarketWatch").innerHTML = `
          <article class="core-watch-brief">
            <strong>${escapeHtml(watchRows.length)}</strong>
            <p>${escapeHtml(t("coreWatchBrands"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(avgScore)}%"></span></div>
            <div class="coverage-stats">
              <article class="coverage-stat"><strong>${escapeHtml(thinCount)}</strong><span class="muted">${escapeHtml(t("coreWatchThin"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(anchorGaps)}</strong><span class="muted">${escapeHtml(t("coreWatchAnchorGaps"))}</span></article>
              <article class="coverage-stat"><strong>${escapeHtml(avgScore)}</strong><span class="muted">${escapeHtml(t("coreWatchAvgScore"))}</span></article>
            </div>
          </article>
          <div class="core-watch-list">
            ${watchRows.length ? watchRows.map((entry) => coreMarketWatchCardHtml(entry)).join("") : `<div class="row">${escapeHtml(t("noCoreWatch"))}</div>`}
          </div>
        `;
      }

      function coreMarketWatchCardHtml(entry) {
        const terms = entry.watch_terms || [];
        const primaryTerm = terms[0] || entry.alias;
        const reasons = coreWatchReasons(entry);
        const nextAction = coreWatchNextAction(entry);
        return `<article class="core-watch-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <header>
            <div>
              <strong>${escapeHtml(entry.alias)} · ${escapeHtml(entry.name)}</strong>
              <p>${escapeHtml(t("coreWatchCue"))} · ${escapeHtml(entry.visual?.radar_cue || styleLabel(entry.style))}</p>
            </div>
            <div class="core-watch-side">
              <div class="core-watch-score">${escapeHtml(entry.watch_score)}</div>
              <span class="pill ${escapeHtml(nextAction.tone)}">${escapeHtml(t(nextAction.label))}</span>
            </div>
          </header>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(entry.watch_score)}%"></span></div>
          <p>${escapeHtml(t("weightLabel"))} ${escapeHtml(entry.brand_weight)} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)}/${escapeHtml(entry.target_samples)} · ${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))}</p>
          ${coreWatchPriceAnchorHtml(entry)}
          <div class="core-watch-reasons">
            ${reasons.map((reason) => `<span class="${escapeHtml(reason.tone)}">${escapeHtml(t(reason.label))}</span>`).join("")}
          </div>
          <div class="core-watch-terms" aria-label="${escapeHtml(t("coreWatchTerms"))}">
            ${terms.length ? terms.map((term) => `<button type="button" data-core-watch-brand="${escapeHtml(entry.alias)}" data-core-watch-term="${escapeHtml(term)}" data-core-watch-action="${escapeHtml(nextAction.label)}">${escapeHtml(term)}</button>`).join("") : `<span class="muted">${escapeHtml(t("noMarketKeywords"))}</span>`}
          </div>
          <div class="core-watch-links" aria-label="${escapeHtml(t("coreWatchSearch"))}">
            ${marketSearchLinks({ ...entry, keyword: primaryTerm }).map((link) => `<a href="${escapeHtml(link.href)}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>`).join("")}
            <button type="button" class="secondary" data-core-watch-brand="${escapeHtml(entry.alias)}" data-core-watch-term="${escapeHtml(primaryTerm)}" data-core-watch-action="${escapeHtml(nextAction.label)}">${escapeHtml(t("coreWatchSample"))}</button>
          </div>
        </article>`;
      }

      function coreWatchPriceAnchorHtml(entry) {
        if (!hasCoreWatchPriceAnchor(entry)) {
          return `<div class="core-watch-price missing" aria-label="${escapeHtml(t("coreWatchPriceAnchor"))}">
            <span><strong>${escapeHtml(t("coreWatchPriceMissing"))}</strong>${escapeHtml(t("coreWatchSample"))}</span>
          </div>`;
        }
        return `<div class="core-watch-price" aria-label="${escapeHtml(t("coreWatchPriceAnchor"))}">
          <span>${escapeHtml(t("coreWatchRetailAnchor"))}<strong>${escapeHtml(formatMoney(entry.avg_retail_price, entry.currency))}</strong></span>
          <span>${escapeHtml(t("coreWatchResaleAnchor"))}<strong>${escapeHtml(formatMoney(entry.avg_resale_price, entry.currency))}</strong></span>
          <span>${escapeHtml(t("coreWatchSpreadAnchor"))}<strong>${escapeHtml(formatMoney(entry.avg_spread, entry.currency))}</strong></span>
        </div>`;
      }

      function hasCoreWatchPriceAnchor(entry) {
        return Number(entry.sample_count) > 0 && (Number(entry.avg_retail_price) > 0 || Number(entry.avg_resale_price) > 0);
      }

      function coreWatchNextAction(entry) {
        const sampleCount = Number(entry.sample_count) || 0;
        const premium = Number(entry.avg_premium_rate) || 0;
        if (!hasCoreWatchPriceAnchor(entry)) return { label: "coreWatchActionAnchor", tone: "gold" };
        if (sampleCount < 2) return { label: "coreWatchActionPair", tone: "gold" };
        if (premium < 0) return { label: "coreWatchActionReview", tone: "warn" };
        if (premium >= 0.25) return { label: "coreWatchActionTrack", tone: "rose" };
        return { label: "coreWatchActionHold", tone: "off" };
      }

      function coreWatchReasons(entry) {
        const weight = Number(entry.brand_weight) || 0;
        const samples = Number(entry.sample_count) || 0;
        const premium = Number(entry.avg_premium_rate) || 0;
        const keywordCount = (entry.watch_terms || entry.market_keywords || []).length;
        const reasons = [];
        if (weight >= 90) reasons.push({ label: "coreWatchReasonCore", tone: "rose" });
        else if (weight >= 75) reasons.push({ label: "coreWatchReasonWatch", tone: "gold" });
        if (samples < 2) reasons.push({ label: "coreWatchReasonThin", tone: "gold" });
        if (premium >= 0.5) reasons.push({ label: "coreWatchReasonStrongPremium", tone: "rose" });
        else if (premium >= 0.25) reasons.push({ label: "coreWatchReasonPositivePremium", tone: "rose" });
        else if (premium < 0) reasons.push({ label: "coreWatchReasonDiscount", tone: "warn" });
        if (keywordCount >= 4) reasons.push({ label: "coreWatchReasonKeywordRich", tone: "" });
        return reasons.slice(0, 4);
      }

      function coreMarketWatchRows(rows) {
        return (rows || []).map((entry) => {
          const target = sampleTarget(entry.brand_weight, entry.tier);
          const sampleCount = Number(entry.sample_count) || 0;
          const premium = Number(entry.avg_premium_rate) || 0;
          const keywordBonus = Math.min(12, (entry.market_keywords || []).length * 2);
          const thinBonus = sampleCount < 2 ? 16 : sampleCount < target ? 8 : 0;
          return {
            ...entry,
            target_samples: target,
            watch_terms: (entry.market_keywords || []).slice(0, 5),
            watch_score: clampScore((Number(entry.brand_weight) || 0) * .46 + Math.max(0, premium) * 36 + thinBonus + keywordBonus),
          };
        }).filter((entry) => Number(entry.brand_weight) >= 75 || Number(entry.avg_premium_rate) >= 0.25)
          .sort((a, b) => (
            (Number(b.watch_score) || 0) - (Number(a.watch_score) || 0)
            || (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0)
            || String(a.alias).localeCompare(String(b.alias))
          )).slice(0, 5);
      }

      function renderBrandIdentityMatrix(rows) {
        const stats = brandIdentityStats(rows);
        const visible = [...rows]
          .sort((a, b) => (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0) || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0))
          .slice(0, 9);
        $("brandIdentityMatrix").innerHTML = `
          <article class="identity-brief">
            <strong>${escapeHtml(stats.coverage)}%</strong>
            <p>${escapeHtml(t("identityCoverage"))} · ${escapeHtml(stats.total)} ${escapeHtml(t("brandWeights"))}</p>
            <div class="signal-bar" aria-hidden="true"><span style="--score: ${escapeHtml(stats.coverage)}%"></span></div>
            <div class="identity-counts">
              <span><strong>${escapeHtml(stats.core)}</strong>${escapeHtml(t("identityCoreCount"))}</span>
              <span><strong>${escapeHtml(stats.watch)}</strong>${escapeHtml(t("identityWatchCount"))}</span>
              <span><strong>${escapeHtml(stats.archive)}</strong>${escapeHtml(t("identityArchiveCount"))}</span>
            </div>
          </article>
          <div class="identity-stack">
            ${visible.length ? visible.map((entry) => brandIdentityCardHtml(entry)).join("") : `<div class="row">${escapeHtml(t("noBrandProfile"))}</div>`}
          </div>
        `;
      }

      function brandIdentityStats(rows) {
        const total = rows.length || 0;
        const complete = rows.filter((entry) => entry.visual?.accent && entry.visual?.motif && entry.visual?.radar_cue).length;
        return {
          total,
          coverage: total ? Math.round(complete / total * 100) : 0,
          core: rows.filter((entry) => entry.tier === "core" || Number(entry.brand_weight) >= 90).length,
          watch: rows.filter((entry) => entry.tier === "watch" || (Number(entry.brand_weight) >= 70 && Number(entry.brand_weight) < 90)).length,
          archive: rows.filter((entry) => entry.tier === "archive" || Number(entry.brand_weight) < 70).length,
        };
      }

      function brandIdentityCardHtml(entry) {
        const keywords = (entry.market_keywords || []).slice(0, 3);
        const motif = entry.visual?.motif || styleLabel(entry.style);
        const cue = entry.visual?.radar_cue || "";
        const palette = entry.visual?.palette || styleLabel(entry.style);
        return `<article class="identity-card" style="${escapeHtml(brandVisualStyle(entry))}">
          <div class="identity-swatch" aria-hidden="true">${escapeHtml(entry.alias)}</div>
          <div class="identity-main">
            <strong>${escapeHtml(entry.name)}</strong>
            <p>${escapeHtml(t("identityPalette"))} · ${escapeHtml(palette)} · ${escapeHtml(t("visualMotif"))} · ${escapeHtml(motif)}</p>
            ${cue ? `<p>${escapeHtml(cue)}</p>` : ""}
            <div class="identity-tags">
              ${keywords.length ? keywords.map((keyword) => `<span>${escapeHtml(keyword)}</span>`).join("") : `<span>${escapeHtml(t("profileNoKeywords"))}</span>`}
            </div>
            ${brandWatchLinksHtml(entry)}
          </div>
          <div class="identity-score">
            <strong>${escapeHtml(entry.brand_weight)}</strong>
            <span class="muted">${escapeHtml(t("identityPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))}</span>
            <span class="muted">${escapeHtml(t("identityEvidence"))} ${escapeHtml(entry.sample_count)}</span>
          </div>
        </article>`;
      }

      function brandWatchLinksHtml(entry) {
        const links = (entry.watch_urls || [])
          .map((link) => ({ label: String(link.label || "").trim(), url: safeUrl(link.url) }))
          .filter((link) => link.label && link.url)
          .slice(0, 4);
        if (!links.length) return "";
        return `<div class="identity-links">
          ${links.map((link) => `<a href="${escapeHtml(link.url)}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>`).join("")}
        </div>`;
      }

      function buildOpportunityRows() {
        const draftWeights = new Map(Array.from(document.querySelectorAll("[data-brand-weight]")).map((input) => [input.dataset.brandWeight, Number(input.value) || 0]));
        const marketRows = new Map((currentState?.market?.summary?.brands || []).map((row) => [normalizeAlias(row.brand_alias), row]));
        return (currentState?.brand_weights || []).map((brand) => {
          const market = marketRows.get(normalizeAlias(brand.alias)) || {};
          const sampleCount = Number(market.sample_count) || 0;
          const avgPremiumRate = Number(market.avg_premium_rate) || 0;
          const maxPremiumRate = Number(market.max_premium_rate) || 0;
          const weight = clampScore(draftWeights.get(brand.alias) ?? brand.weight);
          const breakdown = premiumScoreBreakdown(avgPremiumRate, weight, sampleCount);
          const priorityScore = opportunityPriorityScore(breakdown);
          const savedBreakdown = premiumScoreBreakdown(avgPremiumRate, brand.weight, sampleCount);
          const savedPriorityScore = opportunityPriorityScore(savedBreakdown);
          return {
            name: brand.name,
            alias: brand.alias,
            tier: brand.tier,
            style: brand.style,
            visual: brand.visual || {},
            watch_urls: brand.watch_urls || [],
            brand_weight: weight,
            weight_band: weightBandKey(weight).replace("weightBand", "").toLowerCase(),
            weight_role: weightRoleKey(weight),
            market_keywords: brand.market_keywords || [],
            sample_count: sampleCount,
            avg_premium_rate: avgPremiumRate,
            max_premium_rate: maxPremiumRate,
            premium_band: market.premium_band || premiumBand(avgPremiumRate),
            avg_retail_price: Number(market.avg_retail_price) || 0,
            avg_resale_price: Number(market.avg_resale_price) || 0,
            avg_spread: Number(market.avg_spread) || 0,
            currency: market.currency || "CNY",
            evidence_level: evidenceLevel(sampleCount),
            evidence_score: evidenceScore(sampleCount),
            priority_score: priorityScore,
            score_delta: priorityScore - savedPriorityScore,
            score_breakdown: breakdown,
            band: opportunityBand(priorityScore, avgPremiumRate, sampleCount, weight),
            reason_codes: opportunityReasons(avgPremiumRate, sampleCount, weight),
          };
        }).sort((a, b) => (
          (b.priority_score - a.priority_score)
          || (b.brand_weight - a.brand_weight)
          || (b.sample_count - a.sample_count)
          || (b.avg_premium_rate - a.avg_premium_rate)
        ));
      }

      function premiumScoreBreakdown(premiumRate, brandWeight, sampleCount) {
        return {
          premium_points: Math.round(Math.max(0, Number(premiumRate) || 0) * 55),
          brand_points: Math.round(clampScore(brandWeight) * 0.4),
          sample_points: Math.round(Math.min(10, Math.max(0, Number(sampleCount) || 0) * 2)),
        };
      }

      function opportunityPriorityScore(breakdown) {
        return Math.max(0, Math.min(100, Math.round(
          (Number(breakdown.premium_points) || 0)
          + (Number(breakdown.brand_points) || 0)
          + (Number(breakdown.sample_points) || 0)
        )));
      }

      function opportunityBand(score, avgPremiumRate, sampleCount, brandWeight) {
        if (sampleCount < 2 && brandWeight >= 85) return "collect_samples";
        if (score >= 78 && avgPremiumRate >= 0.25 && sampleCount >= 2) return "lead";
        if (score >= 62 || brandWeight >= 85) return "watch";
        return "cooldown";
      }

      function opportunityReasons(avgPremiumRate, sampleCount, brandWeight) {
        const reasons = [];
        if (brandWeight >= 90) reasons.push("core_brand");
        else if (brandWeight >= 70) reasons.push("watch_brand");
        if (sampleCount < 2) reasons.push("needs_samples");
        else if (sampleCount >= 5) reasons.push("sample_supported");
        if (avgPremiumRate >= 0.5) reasons.push("strong_premium");
        else if (avgPremiumRate >= 0.25) reasons.push("positive_premium");
        else if (avgPremiumRate < 0) reasons.push("discounted_resale");
        return reasons.length ? reasons : ["baseline"];
      }

      function weightRoleKey(weight) {
        const value = clampScore(weight);
        if (value >= 90) return "release_priority";
        if (value >= 70) return "premium_watch";
        return "evidence_sampling";
      }

      function evidenceLevel(sampleCount) {
        const count = Number(sampleCount) || 0;
        if (count >= 5) return "ready";
        if (count >= 2) return "thin";
        return "missing";
      }

      function evidenceScore(sampleCount) {
        return Math.max(0, Math.min(100, Math.round((Number(sampleCount) || 0) * 20)));
      }

      function clampScore(value) {
        return Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
      }

      function brandByAlias(alias) {
        return (currentState?.brand_weights || []).find((brand) => brand.alias === alias);
      }

      function normalizeAlias(alias) {
        return String(alias || "").toUpperCase();
      }

      function formatDelta(value) {
        const number = Number(value) || 0;
        return number > 0 ? `+${number}` : String(number);
      }

      function formatDeltaPercent(value) {
        const points = Math.round((Number(value) || 0) * 100);
        return points > 0 ? `+${points}%` : `${points}%`;
      }

      function percentScore(value) {
        return clampScore((Number(value) || 0) * 100);
      }

      function hasScoreDelta(value) {
        return (Number(value) || 0) !== 0;
      }

      function setBusy(busy) {
        document.querySelectorAll("button").forEach((button) => {
          button.disabled = busy || button.dataset.disabled === "true";
        });
      }

      function toast(message) {
        const el = $("toast");
        el.textContent = message;
        el.classList.add("show");
        clearTimeout(toast.timer);
        toast.timer = setTimeout(() => el.classList.remove("show"), 2600);
      }

      function escapeHtml(value) {
        return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
      }

      function escapeRegExp(value) {
        return String(value ?? "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      }

      function safeUrl(value) {
        const text = String(value || "").trim();
        return /^https?:\/\//.test(text) ? text : "";
      }

      function cssEscape(value) {
        return String(value ?? "").replaceAll("\\", "\\\\").replaceAll('"', '\\"');
      }

      function t(key) {
        return translations[currentLanguage]?.[key] ?? translations.en[key] ?? key;
      }

      function valueLabel(group, value) {
        if (!value) return "";
        return translations[currentLanguage]?.[group]?.[value] ?? translations.en[group]?.[value] ?? value;
      }

      function shownText(count) {
        return currentLanguage === "zh" ? `${count} 条展示` : `${count} shown`;
      }

      function newEventText(count) {
        return currentLanguage === "zh" ? `${count} 条新事件` : `${count} new events`;
      }

      function formatPercent(value) {
        return `${Math.round((Number(value) || 0) * 100)}%`;
      }

      function momentumDirection(direction) {
        if (direction === "rising") return t("momentumRising");
        if (direction === "cooling") return t("momentumCooling");
        return t("momentumSteady");
      }

      function momentumPill(direction) {
        if (direction === "rising") return "rose";
        if (direction === "cooling") return "gold";
        return "off";
      }

      function premiumWidth(value) {
        return Math.max(4, Math.min(100, Math.round((Number(value) || 0) * 100)));
      }

      function formatMoney(value, currency) {
        const number = Number(value) || 0;
        return `${number.toLocaleString(undefined, { maximumFractionDigits: 0 })} ${currency || ""}`.trim();
      }

      function sampleSignalLabel(premiumRate) {
        const value = Number(premiumRate) || 0;
        if (value >= 0.5) return t("sampleSignalStrong");
        if (value >= 0.25) return t("sampleSignalPositive");
        if (value < 0) return t("sampleSignalDiscount");
        return t("sampleSignalNeutral");
      }

      function premiumBand(premiumRate) {
        const value = Number(premiumRate) || 0;
        if (value >= 0.8) return "collector";
        if (value >= 0.5) return "hot";
        if (value >= 0.25) return "premium";
        if (value >= -0.1) return "near_retail";
        return "discount";
      }

      function premiumBandLabelKey(band) {
        return {
          collector: "premiumBandCollector",
          hot: "premiumBandHot",
          premium: "premiumBandPremium",
          near_retail: "premiumBandNearRetail",
          discount: "premiumBandDiscount",
        }[band] || "premiumFilterAll";
      }

      function tierLabel(tier) {
        if (tier === "core") return t("tierCore");
        if (tier === "watch") return t("tierWatch");
        if (tier === "archive") return t("tierArchive");
        return tier;
      }

      function styleLabel(style) {
        return valueLabel("brandStyle", style);
      }

      function opportunityPill(band) {
        if (band === "lead") return "rose";
        if (band === "collect_samples") return "gold";
        if (band === "cooldown") return "off";
        return "";
      }

      function premiumBandPill(band) {
        if (band === "collector" || band === "hot") return "rose";
        if (band === "premium") return "gold";
        if (band === "discount") return "off";
        return "";
      }

      function reasonLabels(codes) {
        return (codes || []).map((code) => valueLabel("reasonCode", code));
      }

      function countBy(rows, key) {
        return rows.reduce((counts, row) => {
          const value = row[key] || "unknown";
          counts[value] = (counts[value] || 0) + 1;
          return counts;
        }, {});
      }

      function applyLanguage() {
        document.documentElement.lang = currentLanguage === "zh" ? "zh-CN" : "en";
        document.querySelectorAll("[data-i18n]").forEach((node) => {
          node.textContent = t(node.dataset.i18n);
        });
        document.querySelectorAll("option[data-i18n]").forEach((option) => {
          option.textContent = t(option.dataset.i18n);
        });
        document.querySelectorAll("[data-language]").forEach((button) => {
          const active = button.dataset.language === currentLanguage;
          button.classList.toggle("active", active);
          button.setAttribute("aria-pressed", active ? "true" : "false");
        });
        if (currentState) render(currentState);
      }

      function applyTheme() {
        document.documentElement.dataset.lolitaTheme = currentTheme;
        document.querySelectorAll("[data-theme-control]").forEach((button) => {
          const active = button.dataset.themeControl === currentTheme;
          button.classList.toggle("active", active);
          button.setAttribute("aria-pressed", active ? "true" : "false");
        });
      }

      function jumpToRadarSection(targetId) {
        const target = $(targetId);
        const section = target?.dataset.radarAnchor === "exact" ? target : target?.closest("section, main") || target;
        if (!section) return;
        section.scrollIntoView({ behavior: "smooth", block: "start" });
      }

      $("checkAllBtn").addEventListener("click", () => runCheck(null));
      document.querySelector(".radar-nav").addEventListener("click", (event) => {
        const button = event.target.closest("[data-radar-jump]");
        if (!button) return;
        jumpToRadarSection(button.dataset.radarJump);
      });
      $("brandCrownQueue").addEventListener("click", (event) => {
        const sampleButton = event.target.closest("[data-crown-sample]");
        if (sampleButton) {
          prepareMarketSample(sampleButton.dataset.crownSample);
          return;
        }
        const keywordButton = event.target.closest("[data-crown-keyword-sample]");
        if (keywordButton) {
          prepareKeywordSample(keywordButton.dataset.crownKeywordSample, keywordButton.dataset.crownKeyword);
          return;
        }
        const applyButton = event.target.closest("[data-crown-apply]");
        if (applyButton) {
          applyFormulaDraft(applyButton.dataset.crownApply, applyButton.dataset.crownTarget);
          return;
        }
        const jumpButton = event.target.closest("[data-crown-jump]");
        if (jumpButton) jumpToRadarSection(jumpButton.dataset.crownJump);
      });
      $("draftRiskRadar").addEventListener("click", (event) => {
        const reviewButton = event.target.closest("[data-draft-risk-review]");
        if (reviewButton) jumpToRadarSection("brandWeightsPanel");
      });
      $("marketForm").addEventListener("submit", addMarketObservation);
      ["marketBrand", "marketRetail", "marketResale", "marketCurrency"].forEach((id) => {
        $(id).addEventListener("input", renderSamplePreview);
        $(id).addEventListener("change", renderSamplePreview);
      });
      $("brandWeights").addEventListener("input", handleWeightInput);
      $("weightScenarios").addEventListener("click", (event) => {
        const button = event.target.closest("[data-weight-scenario]");
        if (button) applyWeightScenario(button.dataset.weightScenario);
      });
      $("brandWeightRubric").addEventListener("click", (event) => {
        const jumpButton = event.target.closest("[data-rubric-jump]");
        if (jumpButton) {
          jumpToRadarSection(jumpButton.dataset.rubricJump);
          return;
        }
        const sampleButton = event.target.closest("[data-rubric-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.rubricSample);
      });
      $("brandPortfolio").addEventListener("click", (event) => {
        const jumpButton = event.target.closest("[data-portfolio-jump]");
        if (jumpButton) {
          jumpToRadarSection(jumpButton.dataset.portfolioJump);
          return;
        }
        const sampleButton = event.target.closest("[data-portfolio-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.portfolioSample);
      });
      $("releaseWatchQueue").addEventListener("click", (event) => {
        const sampleButton = event.target.closest("[data-release-sample]");
        if (sampleButton) prepareKeywordSample(sampleButton.dataset.releaseSample, sampleButton.dataset.releaseKeyword);
      });
      $("brandPlaybook").addEventListener("click", (event) => {
        const applyButton = event.target.closest("[data-playbook-apply]");
        if (applyButton) {
          applyFormulaDraft(applyButton.dataset.playbookApply, applyButton.dataset.playbookTarget);
          return;
        }
        const keywordButton = event.target.closest("[data-playbook-keyword-brand]");
        if (keywordButton) {
          prepareKeywordSample(keywordButton.dataset.playbookKeywordBrand, keywordButton.dataset.playbookKeyword);
          return;
        }
        const sampleButton = event.target.closest("[data-playbook-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.playbookSample);
      });
      $("weightScenarioCompare").addEventListener("click", (event) => {
        const button = event.target.closest("[data-scenario-preview-apply]");
        if (button) applyWeightScenario(button.dataset.scenarioPreviewApply);
      });
      $("exportDailyCsvBtn").addEventListener("click", exportDailyRadarCsv);
      $("exportWeightsCsvBtn").addEventListener("click", exportBrandWeightsCsv);
      $("exportScorecardsCsvBtn").addEventListener("click", exportBrandWeightScorecardsCsv);
      $("exportGuardrailsCsvBtn").addEventListener("click", exportBrandWeightGuardrailsCsv);
      $("exportScenariosCsvBtn").addEventListener("click", exportWeightScenariosCsv);
      $("exportCrownCsvBtn").addEventListener("click", exportCrownCsv);
      $("exportRunSheetCsvBtn").addEventListener("click", exportRunSheetCsv);
      $("exportReleaseWatchCsvBtn").addEventListener("click", exportReleaseWatchCsv);
      $("exportPortfolioCsvBtn").addEventListener("click", exportPortfolioCsv);
      $("exportPremiumSeedsCsvBtn").addEventListener("click", exportPremiumSeedsCsv);
      $("exportCoreWatchCsvBtn").addEventListener("click", exportCoreWatchCsv);
      $("exportMarketActionsCsvBtn").addEventListener("click", exportMarketActionsCsv);
      $("exportSamplePlanCsvBtn").addEventListener("click", exportSamplePlanCsv);
      $("saveWeightsBtn").addEventListener("click", saveBrandWeights);
      $("resetWeightsBtn").addEventListener("click", resetBrandWeightDraft);
      $("applyTuningBatchBtn").addEventListener("click", applyAllTuningDrafts);
      $("weightTuning").addEventListener("click", (event) => {
        const applyButton = event.target.closest("[data-tuning-apply]");
        if (applyButton) {
          applyTuningDraft(applyButton.dataset.tuningApply, applyButton.dataset.tuningTarget);
          return;
        }
        const sampleButton = event.target.closest("[data-tuning-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.tuningSample);
      });
      $("brandWeightFormula").addEventListener("click", (event) => {
        const applyButton = event.target.closest("[data-formula-apply]");
        if (applyButton) applyFormulaDraft(applyButton.dataset.formulaApply, applyButton.dataset.formulaTarget);
      });
      $("weightTrajectory").addEventListener("click", (event) => {
        const applyButton = event.target.closest("[data-trajectory-apply]");
        if (applyButton) {
          applyFormulaDraft(applyButton.dataset.trajectoryApply, applyButton.dataset.trajectoryTarget);
          return;
        }
        const sampleButton = event.target.closest("[data-trajectory-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.trajectorySample);
      });
      $("sampleCoverage").addEventListener("click", (event) => {
        const sampleButton = event.target.closest("[data-coverage-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.coverageSample);
      });
      $("samplePlan").addEventListener("click", (event) => {
        const keywordButton = event.target.closest("[data-sample-plan-keyword]");
        if (keywordButton) {
          prepareKeywordSample(keywordButton.dataset.samplePlanKeywordBrand, keywordButton.dataset.samplePlanKeyword);
          return;
        }
        const sampleButton = event.target.closest("[data-sample-plan]");
        if (sampleButton) {
          prepareMarketSample(sampleButton.dataset.samplePlan);
          toast(`${sampleButton.dataset.samplePlan} ${t("samplePlanSampleReady")}`);
        }
      });
      $("weightSnapshot").addEventListener("click", (event) => {
        const sampleButton = event.target.closest("[data-weight-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.weightSample);
      });
      $("dailyRadarBrief").addEventListener("click", (event) => {
        const laneButton = event.target.closest("[data-daily-lane]");
        if (laneButton) {
          activeDailyLane = laneButton.dataset.dailyLane || "all";
          renderBrandRadarViews();
          return;
        }
        const jumpButton = event.target.closest("[data-daily-jump]");
        if (jumpButton) {
          jumpToRadarSection(jumpButton.dataset.dailyJump);
          return;
        }
        const keywordButton = event.target.closest("[data-daily-keyword-brand]");
        if (keywordButton) {
          prepareKeywordSample(keywordButton.dataset.dailyKeywordBrand, keywordButton.dataset.dailyKeyword);
          return;
        }
        const sampleButton = event.target.closest("[data-daily-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.dailySample);
      });
      $("resaleRunSheet").addEventListener("click", (event) => {
        const jumpButton = event.target.closest("[data-run-sheet-jump]");
        if (jumpButton) {
          jumpToRadarSection(jumpButton.dataset.runSheetJump);
          return;
        }
        const keywordButton = event.target.closest("[data-run-sheet-keyword-brand]");
        if (keywordButton) {
          prepareKeywordSample(keywordButton.dataset.runSheetKeywordBrand, keywordButton.dataset.runSheetKeyword);
          return;
        }
        const sampleButton = event.target.closest("[data-run-sheet-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.runSheetSample);
      });
      $("brandLookbook").addEventListener("click", (event) => {
        const keywordButton = event.target.closest("[data-lookbook-keyword-brand]");
        if (keywordButton) {
          prepareKeywordSample(keywordButton.dataset.lookbookKeywordBrand, keywordButton.dataset.lookbookKeyword);
          return;
        }
        const sampleButton = event.target.closest("[data-lookbook-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.lookbookSample);
      });
      $("brandWeightScorecard").addEventListener("click", (event) => {
        const applyButton = event.target.closest("[data-scorecard-apply]");
        if (applyButton) {
          applyFormulaDraft(applyButton.dataset.scorecardApply, applyButton.dataset.scorecardTarget);
          return;
        }
        const sampleButton = event.target.closest("[data-scorecard-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.scorecardSample);
      });
      $("brandWeightGuardrails").addEventListener("click", (event) => {
        const applyButton = event.target.closest("[data-guardrail-apply]");
        if (applyButton) {
          applyFormulaDraft(applyButton.dataset.guardrailApply, applyButton.dataset.guardrailTarget);
          return;
        }
        const sampleButton = event.target.closest("[data-guardrail-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.guardrailSample);
      });
      $("brandKeywordRadar").addEventListener("click", (event) => {
        const keywordButton = event.target.closest("[data-keyword-brand]");
        if (keywordButton) prepareKeywordSample(keywordButton.dataset.keywordBrand, keywordButton.dataset.keywordTerm);
      });
      $("coreMarketWatch").addEventListener("click", (event) => {
        const watchButton = event.target.closest("[data-core-watch-brand]");
        if (watchButton) prepareCoreWatchSample(watchButton.dataset.coreWatchBrand, watchButton.dataset.coreWatchTerm, watchButton.dataset.coreWatchAction);
      });
      $("premiumSeedRadar").addEventListener("click", (event) => {
        const keywordButton = event.target.closest("[data-premium-seed-brand]");
        if (keywordButton) prepareKeywordSample(keywordButton.dataset.premiumSeedBrand, keywordButton.dataset.premiumSeedKeyword);
      });
      $("patternPremiumRadar").addEventListener("click", (event) => {
        const patternButton = event.target.closest("[data-pattern-brand]");
        if (patternButton) prepareKeywordSample(patternButton.dataset.patternBrand, patternButton.dataset.patternKeyword);
      });
      $("marketActionDesk").addEventListener("click", (event) => {
        const actionButton = event.target.closest("[data-action-sample]");
        if (actionButton) prepareKeywordSample(actionButton.dataset.actionSample, actionButton.dataset.actionKeyword);
      });
      $("priceDiscipline").addEventListener("click", (event) => {
        const sampleButton = event.target.closest("[data-price-discipline-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.priceDisciplineSample);
      });
      $("matrixFilters").addEventListener("click", (event) => {
        const button = event.target.closest("[data-matrix-filter]");
        if (!button) return;
        activeMatrixFilter = button.dataset.matrixFilter || "all";
        renderBrandRadarMatrix(buildBrandRadarMatrix());
      });
      $("matrixSort").addEventListener("change", (event) => {
        activeMatrixSort = event.target.value || "score";
        renderBrandRadarMatrix(buildBrandRadarMatrix());
      });
      $("opportunityFilters").addEventListener("click", (event) => {
        const button = event.target.closest("[data-opportunity-filter]");
        if (!button) return;
        activeOpportunityFilter = button.dataset.opportunityFilter || "all";
        renderOpportunityRadar(currentState?.opportunity_radar || []);
      });
      $("premiumRecordFilters").addEventListener("click", (event) => {
        const button = event.target.closest("[data-premium-filter]");
        if (!button) return;
        activePremiumFilter = button.dataset.premiumFilter || "all";
        renderMarketPremium(currentState?.market || {});
      });
      $("premiumBrandFilter").addEventListener("change", (event) => {
        activePremiumBrandFilter = event.target.value === "all" ? "all" : normalizeAlias(event.target.value);
        renderMarketPremium(currentState?.market || {});
      });
      $("exportPremiumCsvBtn").addEventListener("click", exportPremiumCsv);
      $("refreshBtn").addEventListener("click", () => loadState().then(() => toast(t("refreshed"))).catch((error) => toast(error.message)));
      $("sources").addEventListener("click", (event) => {
        const button = event.target.closest("button[data-source]");
        if (button) runCheck(button.dataset.source);
      });
      document.querySelectorAll("[data-language]").forEach((button) => {
        button.addEventListener("click", () => {
          currentLanguage = button.dataset.language;
          localStorage.setItem("radarLanguage", currentLanguage);
          applyLanguage();
        });
      });
      document.querySelectorAll("[data-theme-control]").forEach((button) => {
        button.addEventListener("click", () => {
          currentTheme = button.dataset.themeControl;
          localStorage.setItem("radarTheme", currentTheme);
          applyTheme();
          toast(t("themeChanged"));
        });
      });
      applyTheme();
      applyLanguage();
      loadState().catch((error) => toast(error.message));
    </script>
  </body>
</html>"""
