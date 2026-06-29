from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .adapters import SourceConfig
from .brands import build_focus_queue, default_brand_weights_path, load_brand_weights
from .config import load_sources
from .market import (
    append_market_observation,
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
        "focus_queue": build_focus_queue(brand_weights, items, events, market_summary["brands"]),
        "market": {
            "observations": market_observations,
            "summary": market_summary,
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
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background:
          radial-gradient(circle at 16px 16px, rgba(180,87,111,.09) 0 2px, transparent 2px),
          linear-gradient(90deg, rgba(97,27,49,.045) 1px, transparent 1px),
          linear-gradient(rgba(15,103,96,.035) 1px, transparent 1px),
          repeating-linear-gradient(90deg, rgba(255,255,255,.34) 0 18px, rgba(255,255,255,0) 18px 36px),
          var(--bg);
        background-size: 32px 32px, 28px 28px, 28px 28px, 72px 72px, auto;
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
      button.secondary { background: linear-gradient(180deg, #3f5a63, #2f424a); }
      button[disabled] { background: #ad9fa5; }
      button:disabled { opacity: .65; cursor: wait; }
      .topbar {
        position: relative;
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 18px;
        align-items: center;
        padding: 22px 24px 18px;
        color: #fff;
        background:
          radial-gradient(circle at 18% 16%, rgba(255,255,255,.14) 0 1px, transparent 2px),
          repeating-linear-gradient(90deg, rgba(255,255,255,.045) 0 12px, rgba(255,255,255,0) 12px 24px),
          linear-gradient(135deg, rgba(136,59,80,.92), rgba(32,21,29,.96) 54%, rgba(15,111,106,.86)),
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
      .eyebrow { margin: 0 0 3px; color: #f1dad7; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }
      .topbar h1 { margin: 0; font: 600 30px/1.05 Georgia, "Times New Roman", serif; }
      .topbar p { margin: 6px 0 0; max-width: 820px; color: #f2e8e6; word-break: break-word; }
      .actions { display: flex; gap: 9px; flex-wrap: wrap; justify-content: flex-end; }
      .language-switch { display: inline-flex; align-items: center; gap: 2px; padding: 2px; border: 1px solid rgba(255,255,255,.18); border-radius: 7px; background: rgba(255,255,255,.08); }
      .language-switch button { min-height: 32px; padding: 0 10px; border-radius: 5px; background: transparent; color: #c9d6dc; }
      .language-switch button.active { background: #fff; color: #14242d; }
      .metrics { display: grid; grid-template-columns: repeat(5, minmax(132px, 1fr)); gap: 12px; padding: 22px 20px 12px; }
      .metric, .panel, .atelier {
        background:
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
      .atelier { margin: 0 20px 14px; padding: 14px; display: grid; grid-template-columns: minmax(220px, .7fr) 1fr; gap: 14px; }
      .atelier h2, .panel h2 { margin: 0; font: 650 17px/1.2 Georgia, "Times New Roman", serif; }
      .watch-grid { display: grid; grid-template-columns: repeat(4, minmax(125px, 1fr)); gap: 9px; }
      .brand-chip {
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 9px 10px;
        background:
          linear-gradient(90deg, rgba(180,87,111,.1), transparent 42%),
          var(--bg-soft);
      }
      .brand-chip strong { display: block; color: var(--wine); }
      .brand-chip span { color: var(--muted); font-size: 12px; }
      .focus-list { display: grid; gap: 8px; }
      .focus-card { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: linear-gradient(135deg, #fff7f7, #f8fbfa); }
      .focus-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .focus-card strong { color: var(--wine); }
      .signal-strip { display: grid; gap: 8px; align-content: start; }
      .signal-bar { height: 11px; overflow: hidden; border-radius: 999px; background: var(--lace); box-shadow: inset 0 0 0 1px rgba(97,27,49,.06); }
      .signal-bar span { display: block; height: 100%; width: var(--score); background: linear-gradient(90deg, var(--teal), var(--rose), var(--gold)); }
      .workspace { display: grid; grid-template-columns: 340px 1fr; gap: 14px; padding: 0 20px 20px; }
      .panel { min-width: 0; overflow: hidden; }
      .panel h2 { padding: 14px 15px; border-bottom: 1px solid var(--line); background: linear-gradient(90deg, #fff7f7, #f8fbfa); }
      .toolbar, .panel > h2 {
        background:
          radial-gradient(circle at 12px 100%, rgba(180,87,111,.12) 0 6px, transparent 6px) 0 100% / 24px 10px repeat-x,
          linear-gradient(90deg, #fff7f7, #f8fbfa);
      }
      .source-list, .event-list, .item-list, .status-list { display: grid; gap: 9px; padding: 12px; }
      .source-card, .row, .status-card { border: 1px solid var(--line); border-radius: 8px; padding: 11px; background: #fffaf8; }
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
      .market-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 11px;
        background:
          linear-gradient(90deg, rgba(169,120,44,.11), transparent 30%),
          #fffaf8;
      }
      .market-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
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
      .toast { position: fixed; right: 16px; bottom: 16px; max-width: min(440px, calc(100vw - 32px)); padding: 10px 12px; border-radius: 8px; background: #16242d; color: #fff; box-shadow: var(--shadow); opacity: 0; transform: translateY(8px); transition: .16s; pointer-events: none; }
      .toast.show { opacity: 1; transform: translateY(0); }
      @media (max-width: 860px) {
        .topbar, .atelier, .workspace, .market-grid { grid-template-columns: 1fr; }
        .actions { justify-content: flex-start; }
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
        <h2 data-i18n="brandWeights">品牌权重</h2>
        <div id="brandWeights" class="watch-grid"></div>
      </div>
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
        <button id="addMarketBtn" type="submit" data-i18n="addSample">加入样本</button>
      </form>
      <div class="market-grid">
        <div>
          <h2 data-i18n="premiumByBrand">品牌溢价排行</h2>
          <div id="premiumBrands" class="market-list"></div>
        </div>
        <div>
          <h2 data-i18n="premiumRecords">高溢价样本</h2>
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
          focusQueue: "重点关注队列",
          marketPremium: "二手溢价观察",
          premiumByBrand: "品牌溢价排行",
          premiumRecords: "高溢价样本",
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
          radarScore: "雷达分",
          observed: "已捕捉",
          noFocusQueue: "暂无关注队列",
          noMarket: "暂无价格样本",
          samples: "样本",
          avgPremium: "均值",
          maxPremium: "最高",
          priorityScore: "权重修正分",
          retailPrice: "原价",
          resalePrice: "二手价",
          brandAlias: "品牌",
          itemName: "款名",
          currency: "币种",
          condition: "成色",
          sourceName: "来源",
          observedAt: "日期",
          addSample: "加入样本",
          sampleAdded: "价格样本已加入",
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
          focusQueue: "Focus Queue",
          marketPremium: "Resale Premium Watch",
          premiumByBrand: "Premium by Brand",
          premiumRecords: "High-Premium Samples",
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
          radarScore: "radar score",
          observed: "observed",
          noFocusQueue: "No focus queue yet",
          noMarket: "No price samples yet",
          samples: "samples",
          avgPremium: "avg",
          maxPremium: "max",
          priorityScore: "weighted score",
          retailPrice: "retail",
          resalePrice: "resale",
          brandAlias: "brand",
          itemName: "item",
          currency: "currency",
          condition: "condition",
          sourceName: "source",
          observedAt: "date",
          addSample: "Add Sample",
          sampleAdded: "price sample added",
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
        renderBrandWeights(state.brand_weights || []);
        renderFocusQueue(state.focus_queue || []);
        renderMarketSignal(state.events || [], state.items || []);
        renderMarketPremium(state.market || {});
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
          <p class="muted">${escapeHtml(t("weightLabel"))} ${escapeHtml(brand.weight)} · ${escapeHtml(tierLabel(brand.tier))} · ${escapeHtml(styleLabel(brand.style))}</p>
        </article>`).join("");
      }

      function renderMarketForm(weights) {
        const select = $("marketBrand");
        const current = select.value;
        select.innerHTML = weights.map((brand) => `<option value="${escapeHtml(brand.alias)}">${escapeHtml(brand.alias)} · ${escapeHtml(brand.name)}</option>`).join("");
        if (current && Array.from(select.options).some((option) => option.value === current)) {
          select.value = current;
        }
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
        $("premiumRecords").innerHTML = records.length ? records.map((record) => `<article class="market-card">
          <header>
            <strong>${escapeHtml(record.brand_alias)} · ${escapeHtml(record.item_name)}</strong>
            <span class="premium-rate">${formatPercent(record.premium_rate)}</span>
          </header>
          <p class="muted">${escapeHtml(t("priorityScore"))} ${escapeHtml(record.priority_score)} · ${escapeHtml(t("weightLabel"))} ${escapeHtml(record.brand_weight)}</p>
          <p class="muted">${escapeHtml(t("retailPrice"))} ${formatMoney(record.retail_price, record.currency)} · ${escapeHtml(t("resalePrice"))} ${formatMoney(record.resale_price, record.currency)}</p>
          <p class="muted">${escapeHtml([record.condition, record.source, record.observed_at].filter(Boolean).join(" · "))}</p>
        </article>`).join("") : `<div class="row">${escapeHtml(t("noMarket"))}</div>`;
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
          toast(t("sampleAdded"));
        } catch (error) {
          toast(error.message);
        } finally {
          setBusy(false);
        }
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

      function tierLabel(tier) {
        if (tier === "core") return t("tierCore");
        if (tier === "watch") return t("tierWatch");
        if (tier === "archive") return t("tierArchive");
        return tier;
      }

      function styleLabel(style) {
        return valueLabel("brandStyle", style);
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
        document.querySelectorAll("[data-language]").forEach((button) => {
          const active = button.dataset.language === currentLanguage;
          button.classList.toggle("active", active);
          button.setAttribute("aria-pressed", active ? "true" : "false");
        });
        if (currentState) render(currentState);
      }

      $("checkAllBtn").addEventListener("click", () => runCheck(null));
      $("marketForm").addEventListener("submit", addMarketObservation);
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
