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
    build_opportunity_radar,
    build_pattern_radar,
    default_market_observations_path,
    load_market_observations,
    summarize_market_observations,
)
from .models import RadarEvent
from .runner import check_sources
from .storage import connect, list_events, list_items, storage_counts


DEFAULT_WEB_PORT = 8766


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
            "patterns": build_pattern_radar(brand_weights, market_observations),
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
        --shadow: 0 18px 44px rgba(63, 39, 47, .13);
        --pearl-shadow: 0 1px 0 rgba(255,255,255,.88), 0 7px 18px rgba(97,27,49,.1);
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background:
          radial-gradient(circle at 50% 0, rgba(255,255,255,.72) 0 7px, transparent 7px) 0 0 / 22px 14px repeat-x,
          radial-gradient(circle at 50% 14px, rgba(180,87,111,.11) 0 1px, transparent 2px) 0 0 / 22px 14px repeat-x,
          radial-gradient(circle at 16px 16px, rgba(180,87,111,.09) 0 2px, transparent 2px),
          linear-gradient(90deg, rgba(97,27,49,.045) 1px, transparent 1px),
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
        background: linear-gradient(180deg, #93415b, var(--rose-dark));
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
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 18px;
        align-items: center;
        padding: 28px 24px 20px;
        color: #fff;
        background:
          radial-gradient(circle at 92% 28%, rgba(246,216,223,.22) 0 2px, transparent 2px),
          radial-gradient(circle at 18% 16%, rgba(255,255,255,.14) 0 1px, transparent 2px),
          repeating-linear-gradient(90deg, rgba(255,255,255,.045) 0 12px, rgba(255,255,255,0) 12px 24px),
          linear-gradient(135deg, rgba(136,59,80,.92), rgba(32,21,29,.96) 50%, rgba(15,111,106,.86)),
          #241c21;
        border-bottom: 5px double rgba(255,255,255,.24);
        overflow: hidden;
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
      .eyebrow { margin: 0 0 5px; color: #f1dad7; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }
      .topbar h1 {
        position: relative;
        display: inline-block;
        margin: 0;
        padding-right: 42px;
        font: 600 31px/1.05 Georgia, "Times New Roman", serif;
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
      .actions { display: flex; gap: 9px; flex-wrap: wrap; justify-content: flex-end; }
      .language-switch { display: inline-flex; align-items: center; gap: 2px; padding: 2px; border: 1px solid rgba(255,255,255,.18); border-radius: 7px; background: rgba(255,255,255,.08); }
      .language-switch button { min-height: 32px; padding: 0 10px; border-radius: 5px; background: transparent; color: #c9d6dc; }
      .language-switch button.active { background: #fff; color: #14242d; }
      .metrics { display: grid; grid-template-columns: repeat(5, minmax(132px, 1fr)); gap: 12px; padding: 22px 20px 12px; }
      .metric, .panel, .atelier {
        background:
          radial-gradient(circle at 16px 16px, rgba(255,255,255,.88) 0 2px, transparent 2px) 0 0 / 22px 22px,
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
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 13px 10px 10px;
        background:
          linear-gradient(90deg, rgba(180,87,111,.1), transparent 42%),
          var(--bg-soft);
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
          linear-gradient(90deg, var(--rose), var(--gold), var(--teal));
      }
      .brand-chip.dirty { border-color: rgba(169,120,44,.68); box-shadow: inset 0 0 0 1px rgba(169,120,44,.2); }
      .brand-chip strong { display: block; color: var(--wine); }
      .brand-chip span { color: var(--muted); font-size: 12px; }
      .brand-chip input[type="range"] { width: 100%; accent-color: var(--rose-dark); }
      .weight-control { display: grid; gap: 4px; margin-top: 7px; color: var(--muted); font-size: 12px; }
      .weight-insight { display: grid; gap: 5px; margin-top: 8px; padding-top: 8px; border-top: 1px dashed rgba(97,27,49,.16); }
      .weight-insight p { margin: 0; color: var(--muted); font-size: 12px; }
      .weight-insight strong { display: inline; color: var(--wine); }
      .brand-tools { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 9px; }
      .brand-actions { display: flex; align-items: center; justify-content: flex-end; gap: 7px; flex-wrap: wrap; }
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
      .weight-snapshot-board { margin: 0 20px 14px; }
      .weight-snapshot { display: grid; grid-template-columns: minmax(190px, .7fr) minmax(260px, 1.3fr) minmax(260px, 1fr); gap: 12px; padding: 12px; }
      .weight-hero, .weight-metric, .weight-lane, .weight-gap-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf8;
      }
      .weight-hero { display: grid; gap: 9px; align-content: start; padding: 12px; background: linear-gradient(135deg, rgba(255,247,232,.78), rgba(248,251,250,.9)); box-shadow: inset 0 0 0 4px rgba(255,255,255,.48); }
      .weight-hero strong { color: var(--wine); font: 650 34px/1 Georgia, "Times New Roman", serif; }
      .weight-hero p, .weight-metric span, .weight-lane span, .weight-gap-card p { margin: 0; color: var(--muted); }
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
      .market-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 0 0 8px; }
      .market-heading h2 { margin: 0; padding: 0; border: 0; background: transparent; }
      .market-heading .segmented { justify-content: flex-end; }
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
        .actions { justify-content: flex-start; }
        .opportunity-toolbar, .matrix-toolbar, .coverage-grid, .weight-snapshot, .action-grid, .quality-grid, .alert-grid { grid-template-columns: 1fr; }
        .matrix-tools { justify-content: flex-start; }
        .market-heading { align-items: flex-start; flex-direction: column; }
        .coverage-card, .sample-preview { grid-template-columns: 1fr; }
        .matrix-row { grid-template-columns: 1fr 1fr; }
        .matrix-row.header { display: none; }
        .brand-tools { align-items: flex-start; flex-direction: column; }
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
      </div>
      <div class="actions">
        <div class="language-switch" role="group" aria-label="Language">
          <button type="button" data-language="zh">中文</button>
          <button type="button" data-language="en">EN</button>
        </div>
        <button id="checkAllBtn" data-i18n="checkAll">检查全部</button>
        <button id="refreshBtn" class="secondary" data-i18n="refresh">刷新</button>
      </div>
    </header>
    <section class="metrics" id="metrics"></section>
    <section class="atelier">
      <div class="signal-strip">
        <h2 data-i18n="marketSignal">溢价信号</h2>
        <p class="muted" id="signalSummary"></p>
        <div class="signal-bar" aria-hidden="true"><span id="signalBar" style="--score: 0%"></span></div>
        <div id="statusMix" class="status-list"></div>
        <h2 data-i18n="focusQueue">重点关注队列</h2>
        <div id="focusQueue" class="focus-list"></div>
      </div>
      <div>
        <div class="brand-tools">
          <h2 data-i18n="brandWeights">品牌权重</h2>
          <div class="brand-actions">
            <span id="weightDirtyStatus" class="muted" data-i18n="weightsClean">已保存</span>
            <button id="resetWeightsBtn" type="button" class="secondary" data-i18n="resetWeights" data-disabled="true" disabled>重置</button>
            <button id="saveWeightsBtn" type="button" class="secondary" data-i18n="saveWeights" data-disabled="true" disabled>保存权重</button>
          </div>
        </div>
        <div id="brandWeights" class="watch-grid"></div>
      </div>
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
    <section class="panel weight-snapshot-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="weightSnapshot">权重画像</h2>
          <span class="muted" data-i18n="weightSnapshotHint">把品牌档位、价格证据和样本缺口放在一起校准</span>
        </div>
      </div>
      <div id="weightSnapshot" class="weight-snapshot"></div>
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
    <section class="panel keyword-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="brandKeywordRadar">热门款式词</h2>
          <span class="muted" data-i18n="brandKeywordHint">把 AP 贝壳这类款式线索接到价格样本录入</span>
        </div>
      </div>
      <div id="brandKeywordRadar" class="keyword-radar"></div>
    </section>
    <section class="panel action-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="marketActionDesk">二级市场行动台</h2>
          <span class="muted" data-i18n="marketActionHint">把高权重款式词转成搜索和补样本任务</span>
        </div>
      </div>
      <div id="marketActionDesk" class="action-grid"></div>
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
    <section class="panel tuning-board">
      <div class="toolbar">
        <div>
          <h2 data-i18n="weightTuning">权重校准建议</h2>
          <span class="muted" data-i18n="weightTuningHint">把溢价、样本和当前权重翻译成下一步动作</span>
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
      <div id="samplePreview" class="sample-preview"></div>
      <div class="market-grid">
        <div>
          <h2 data-i18n="premiumByBrand">品牌溢价排行</h2>
          <div id="premiumBrands" class="market-list"></div>
        </div>
        <div>
          <div class="market-heading">
            <h2 data-i18n="premiumRecords">高溢价样本</h2>
            <div id="premiumRecordFilters" class="segmented" role="group" aria-label="Premium sample filter">
              <button type="button" data-premium-filter="all" data-i18n="premiumFilterAll">全部</button>
              <button type="button" data-premium-filter="collector" data-i18n="premiumBandCollector">藏品级</button>
              <button type="button" data-premium-filter="hot" data-i18n="premiumBandHot">强溢价</button>
              <button type="button" data-premium-filter="premium" data-i18n="premiumBandPremium">溢价</button>
              <button type="button" data-premium-filter="near_retail" data-i18n="premiumBandNearRetail">近原价</button>
              <button type="button" data-premium-filter="discount" data-i18n="premiumBandDiscount">折价</button>
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
          checkAll: "检查全部",
          refresh: "刷新",
          sourcesHeading: "监控源",
          recentEvents: "上新动态",
          trackedItemsHeading: "雷达条目",
          marketSignal: "溢价信号",
          brandWeights: "品牌权重",
          saveWeights: "保存权重",
          resetWeights: "重置",
          weightsClean: "已保存",
          weightsDirty: "项未保存",
          draftPreview: "草稿预览",
          scoreDelta: "变化",
          weightsReset: "品牌权重已重置",
          weightsSaved: "品牌权重已保存",
          brandRadarMatrix: "品牌雷达矩阵",
          matrixHint: "把权重、溢价、样本和动作放在一起看",
          matrixBrand: "品牌",
          matrixScore: "雷达分",
          matrixWeight: "权重",
          matrixPremium: "均值溢价",
          matrixSamples: "样本",
          matrixAction: "动作",
          matrixFilterAll: "全部",
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
          marketPremium: "二手溢价观察",
          premiumByBrand: "品牌溢价排行",
          premiumRecords: "高溢价样本",
          premiumFilterAll: "全部",
          premiumBandCollector: "藏品级",
          premiumBandHot: "强溢价",
          premiumBandPremium: "溢价",
          premiumBandNearRetail: "近原价",
          premiumBandDiscount: "折价",
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
          weightAverage: "平均权重",
          weightCoreAverage: "核心均值",
          weightEvidenceCoverage: "证据覆盖",
          weightNeedsEvidence: "待补证据",
          weightDistribution: "权重分布",
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
          brandKeywordRadar: "热门款式词",
          brandKeywordHint: "把 AP 贝壳这类款式线索接到价格样本录入",
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
          tuningDraftApplied: "已套用建议权重",
          tuningSampleReady: "已选中品牌，可补价格样本",
          sampleCoverage: "样本覆盖",
          sampleCoverageHint: "判断雷达分背后的价格证据厚度",
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
          checkAll: "Check All",
          refresh: "Refresh",
          sourcesHeading: "Watch Sources",
          recentEvents: "Release Feed",
          trackedItemsHeading: "Radar Items",
          marketSignal: "Premium Signal",
          brandWeights: "Brand Weights",
          saveWeights: "Save Weights",
          resetWeights: "Reset",
          weightsClean: "saved",
          weightsDirty: "unsaved",
          draftPreview: "draft preview",
          scoreDelta: "delta",
          weightsReset: "brand weights reset",
          weightsSaved: "brand weights saved",
          brandRadarMatrix: "Brand Radar Matrix",
          matrixHint: "Weight, premium, samples, and action in one view",
          matrixBrand: "brand",
          matrixScore: "score",
          matrixWeight: "weight",
          matrixPremium: "avg premium",
          matrixSamples: "samples",
          matrixAction: "action",
          matrixFilterAll: "All",
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
          marketPremium: "Resale Premium Watch",
          premiumByBrand: "Premium by Brand",
          premiumRecords: "High-Premium Samples",
          premiumFilterAll: "All",
          premiumBandCollector: "Collector",
          premiumBandHot: "Hot",
          premiumBandPremium: "Premium",
          premiumBandNearRetail: "Near retail",
          premiumBandDiscount: "Discount",
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
          weightAverage: "average weight",
          weightCoreAverage: "core average",
          weightEvidenceCoverage: "evidence coverage",
          weightNeedsEvidence: "needs evidence",
          weightDistribution: "weight distribution",
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
          brandKeywordRadar: "Hot Pattern Keywords",
          brandKeywordHint: "Connect item-level signals such as AP shell to price-sample entry",
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
          tuningDraftApplied: "draft weight applied",
          tuningSampleReady: "brand selected for price sample",
          sampleCoverage: "Sample Coverage",
          sampleCoverageHint: "Show how much price evidence sits behind radar scores",
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
      let activeOpportunityFilter = "all";
      let activeMatrixFilter = "all";
      let activeMatrixSort = "score";
      let activePremiumFilter = "all";
      let previewingDraftWeights = false;
      if (!translations[currentLanguage]) currentLanguage = "zh";

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
        renderBrandKeywordRadar(state.brand_weights || []);
        renderFocusQueue(state.focus_queue || []);
        renderMarketAlertLine(state.market_alerts || {});
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
        $("brandWeights").innerHTML = weights.map((brand) => `<article class="brand-chip">
          <strong>${escapeHtml(brand.alias)}</strong>
          <span>${escapeHtml(brand.name)}</span>
          <div class="signal-bar" aria-hidden="true"><span style="--score: ${Number(brand.weight) || 0}%"></span></div>
          <label class="weight-control">
            <span data-weight-label>${escapeHtml(t("weightLabel"))} ${escapeHtml(brand.weight)}</span>
            <input type="range" min="0" max="100" step="1" value="${escapeHtml(brand.weight)}" data-original-weight="${escapeHtml(brand.weight)}" data-brand-weight="${escapeHtml(brand.alias)}">
          </label>
          <p class="muted">${escapeHtml(tierLabel(brand.tier))} · ${escapeHtml(styleLabel(brand.style))}</p>
          <div class="weight-insight" data-weight-insight="${escapeHtml(brand.alias)}">
            ${brandWeightInsightHtml(brand, brand.weight)}
          </div>
        </article>`).join("");
        updateWeightDirtyState();
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
          return `<article class="keyword-card">
            <header>
              <div>
                <strong>${escapeHtml(brand.alias)}</strong>
                <p class="muted">${escapeHtml(brand.name)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(brand.weight)}</p>
              </div>
              <span class="pill ${brand.weight >= 90 ? "rose" : ""}">${escapeHtml(t("marketKeywords"))} ${escapeHtml(terms.length)}</span>
            </header>
            <div class="keyword-chips">
              ${terms.length ? terms.map((term) => `<button type="button" class="secondary" data-keyword-brand="${escapeHtml(brand.alias)}" data-keyword-term="${escapeHtml(term)}">${escapeHtml(term)}</button>`).join("") : `<span class="muted">${escapeHtml(t("noMarketKeywords"))}</span>`}
            </div>
          </article>`;
        }).join("");
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
            <span class="pill ${opportunityPill(entry.band)}">${escapeHtml(valueLabel("opportunityBand", entry.band))}</span>
          </article>`),
        ].join("") : `<div class="row">${escapeHtml(t("noOpportunity"))}</div>`;
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

      function renderWeightSnapshot(rows) {
        const stats = weightSnapshotStats(rows);
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
          </article>
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
        `;
      }

      function renderBrandWeightProfile(rows) {
        const visible = [...rows]
          .sort((a, b) => (Number(b.brand_weight) || 0) - (Number(a.brand_weight) || 0) || (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0))
          .slice(0, 9);
        $("brandWeightProfile").innerHTML = visible.length ? visible.map((entry) => {
          const keywords = (entry.market_keywords || []).slice(0, 4);
          return `<article class="profile-card">
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
            <p class="muted">${escapeHtml(t("avgPremium"))} ${escapeHtml(formatPercent(entry.avg_premium_rate))} · ${escapeHtml(t("samples"))} ${escapeHtml(entry.sample_count)} · ${escapeHtml(tierLabel(entry.tier))}</p>
            <div class="profile-keywords" aria-label="${escapeHtml(t("profileKeywords"))}">
              ${keywords.length ? keywords.map((keyword) => `<span>${escapeHtml(keyword)}</span>`).join("") : `<span>${escapeHtml(t("profileNoKeywords"))}</span>`}
            </div>
          </article>`;
        }).join("") : `<div class="row">${escapeHtml(t("noBrandProfile"))}</div>`;
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

      function renderWeightTuning(rows) {
        const suggestions = buildWeightTuning(rows);
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
        if (activeMatrixFilter === "lead") return rows.filter((entry) => entry.band === "lead" || entry.band === "watch");
        if (activeMatrixFilter === "needs_samples") return rows.filter((entry) => (entry.reason_codes || []).includes("needs_samples"));
        if (activeMatrixFilter === "core") return rows.filter((entry) => entry.tier === "core" || Number(entry.brand_weight) >= 90);
        return rows;
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
        const visibleRecords = activePremiumFilter === "all" ? records : records.filter((record) => record.premium_band === activePremiumFilter);
        syncPremiumRecordFilters(summary.premium_bands || []);
        $("marketCount").textContent = `${summary.sample_count || 0} ${t("samples")}`;
        $("premiumBrands").innerHTML = brands.length ? brands.map((brand) => `<article class="market-card">
          <header>
            <strong>${escapeHtml(brand.brand_alias)}</strong>
            <span class="premium-rate">${formatPercent(brand.avg_premium_rate)}</span>
          </header>
          <p class="muted">${escapeHtml(t("priorityScore"))} ${escapeHtml(brand.priority_score)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(brand.brand_weight)}</p>
          <p class="muted">${escapeHtml(t("samples"))} ${escapeHtml(brand.sample_count)} · ${escapeHtml(t("maxPremium"))} ${escapeHtml(formatPercent(brand.max_premium_rate))}</p>
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

      function syncPremiumRecordFilters(bands = []) {
        const counts = Object.fromEntries((bands || []).map((row) => [row.band, row.count]));
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
        const card = input.closest(".brand-chip");
        const label = card?.querySelector("[data-weight-label]");
        const bar = card?.querySelector(".signal-bar span");
        const insight = card?.querySelector("[data-weight-insight]");
        const brand = brandByAlias(input.dataset.brandWeight);
        if (label) label.textContent = `${t("weightLabel")} ${input.value}`;
        if (bar) bar.style.setProperty("--score", `${input.value}%`);
        if (insight && brand) insight.innerHTML = brandWeightInsightHtml(brand, input.value);
        if (card) card.classList.toggle("dirty", input.value !== input.dataset.originalWeight);
        updateWeightDirtyState();
        renderBrandRadarViews();
        renderOpportunityRadar(buildDraftOpportunityRadar());
      }

      function applyTuningDraft(alias, targetWeight) {
        const input = document.querySelector(`[data-brand-weight="${cssEscape(alias)}"]`);
        if (!input) return;
        input.value = clampScore(targetWeight);
        handleWeightInput({ target: input });
        input.scrollIntoView({ behavior: "smooth", block: "center" });
        toast(`${alias} ${t("tuningDraftApplied")}`);
      }

      function prepareMarketSample(alias) {
        const select = $("marketBrand");
        if (Array.from(select.options).some((option) => option.value === alias)) {
          select.value = alias;
        }
        $("marketItem").focus();
        $("marketForm").scrollIntoView({ behavior: "smooth", block: "center" });
        toast(`${alias} ${t("tuningSampleReady")}`);
      }

      function prepareKeywordSample(alias, keyword) {
        const select = $("marketBrand");
        if (Array.from(select.options).some((option) => option.value === alias)) {
          select.value = alias;
        }
        $("marketItem").value = keyword || "";
        $("marketRetail").focus();
        $("marketForm").scrollIntoView({ behavior: "smooth", block: "center" });
        renderSamplePreview();
        toast(`${alias} · ${keyword} ${t("keywordSampleReady")}`);
      }

      function updateWeightDirtyState() {
        const dirtyCount = Array.from(document.querySelectorAll("[data-brand-weight]")).filter((input) => input.value !== input.dataset.originalWeight).length;
        const dirty = dirtyCount > 0;
        previewingDraftWeights = dirty;
        const saveButton = $("saveWeightsBtn");
        const resetButton = $("resetWeightsBtn");
        [saveButton, resetButton].forEach((button) => {
          button.dataset.disabled = dirty ? "false" : "true";
          button.disabled = !dirty;
        });
        const status = $("weightDirtyStatus");
        if (status) {
          status.textContent = dirty ? `${dirtyCount} ${t("weightsDirty")}` : t("weightsClean");
        }
      }

      function buildDraftOpportunityRadar() {
        return buildOpportunityRows().slice(0, 8);
      }

      function buildBrandRadarMatrix() {
        return buildOpportunityRows();
      }

      function renderBrandRadarViews() {
        const rows = buildBrandRadarMatrix();
        renderWeightSnapshot(rows);
        renderBrandWeightProfile(rows);
        renderBrandRadarMatrix(rows);
        renderSampleCoverage(rows);
        renderWeightTuning(rows);
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
            brand_weight: weight,
            weight_band: weightBandKey(weight).replace("weightBand", "").toLowerCase(),
            weight_role: weightRoleKey(weight),
            market_keywords: brand.market_keywords || [],
            sample_count: sampleCount,
            avg_premium_rate: avgPremiumRate,
            max_premium_rate: maxPremiumRate,
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

      $("checkAllBtn").addEventListener("click", () => runCheck(null));
      $("marketForm").addEventListener("submit", addMarketObservation);
      ["marketBrand", "marketRetail", "marketResale", "marketCurrency"].forEach((id) => {
        $(id).addEventListener("input", renderSamplePreview);
        $(id).addEventListener("change", renderSamplePreview);
      });
      $("brandWeights").addEventListener("input", handleWeightInput);
      $("saveWeightsBtn").addEventListener("click", saveBrandWeights);
      $("resetWeightsBtn").addEventListener("click", resetBrandWeightDraft);
      $("weightTuning").addEventListener("click", (event) => {
        const applyButton = event.target.closest("[data-tuning-apply]");
        if (applyButton) {
          applyTuningDraft(applyButton.dataset.tuningApply, applyButton.dataset.tuningTarget);
          return;
        }
        const sampleButton = event.target.closest("[data-tuning-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.tuningSample);
      });
      $("sampleCoverage").addEventListener("click", (event) => {
        const sampleButton = event.target.closest("[data-coverage-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.coverageSample);
      });
      $("weightSnapshot").addEventListener("click", (event) => {
        const sampleButton = event.target.closest("[data-weight-sample]");
        if (sampleButton) prepareMarketSample(sampleButton.dataset.weightSample);
      });
      $("brandKeywordRadar").addEventListener("click", (event) => {
        const keywordButton = event.target.closest("[data-keyword-brand]");
        if (keywordButton) prepareKeywordSample(keywordButton.dataset.keywordBrand, keywordButton.dataset.keywordTerm);
      });
      $("patternPremiumRadar").addEventListener("click", (event) => {
        const patternButton = event.target.closest("[data-pattern-brand]");
        if (patternButton) prepareKeywordSample(patternButton.dataset.patternBrand, patternButton.dataset.patternKeyword);
      });
      $("marketActionDesk").addEventListener("click", (event) => {
        const actionButton = event.target.closest("[data-action-sample]");
        if (actionButton) prepareKeywordSample(actionButton.dataset.actionSample, actionButton.dataset.actionKeyword);
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
      applyLanguage();
      loadState().catch((error) => toast(error.message));
    </script>
  </body>
</html>"""
