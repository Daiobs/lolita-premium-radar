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
from .models import RadarEvent
from .runner import check_sources
from .storage import connect, list_events, list_items, storage_counts


DEFAULT_WEB_PORT = 8766


def run_web(
    config_path: Path,
    db_path: Path,
    brands_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = DEFAULT_WEB_PORT,
) -> int:
    if brands_path is None:
        brands_path = default_brand_weights_path()
    handler = make_handler(config_path=config_path, db_path=db_path, brands_path=brands_path)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Lolita Premium Radar web UI: http://{host}:{port}")
    print(f"Config: {config_path.resolve()}")
    print(f"Brand weights: {brands_path.resolve()}")
    print(f"Database: {db_path.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web UI")
    finally:
        server.server_close()
    return 0


def make_handler(config_path: Path, db_path: Path, brands_path: Path | None = None) -> type[BaseHTTPRequestHandler]:
    if brands_path is None:
        brands_path = default_brand_weights_path()

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
                    self.send_json(get_dashboard_state(config_path, db_path, brands_path))
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            except Exception as exc:
                self.send_exception(exc)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path != "/api/check":
                    self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                    return
                payload = self.read_json(default={})
                source_name = text_value(payload.get("source")) or None
                notify = bool(payload.get("notify", False))
                events = check_sources(config_path=config_path, db_path=db_path, source_name=source_name, notify=notify)
                state = get_dashboard_state(config_path, db_path, brands_path)
                state.update(
                    {
                        "checked_source": source_name or "all",
                        "new_events": [event_to_dict(event) for event in events],
                        "new_event_count": len(events),
                    }
                )
                self.send_json(state)
            except Exception as exc:
                self.send_exception(exc)

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


def get_dashboard_state(config_path: Path, db_path: Path, brands_path: Path | None = None) -> dict[str, Any]:
    sources = load_sources(config_path)
    brand_weights = load_brand_weights(brands_path)
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
        "counts": {
            **counts,
            "sources": len(sources),
            "enabled_sources": sum(1 for source in sources.values() if source.enabled),
        },
        "brand_weights": brand_weights,
        "focus_queue": build_focus_queue(brand_weights, items, events),
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
        --bg: #f5f1ee;
        --bg-soft: #fbf8f6;
        --panel: #fffdfb;
        --text: #241c21;
        --muted: #766971;
        --line: #e7dad7;
        --lace: #f0e6e3;
        --ink: #20151d;
        --rose: #b85b72;
        --rose-dark: #883b50;
        --wine: #6e1f35;
        --teal: #0f6f6a;
        --gold: #a9782c;
        --warn: #a44322;
        --shadow: 0 18px 45px rgba(61, 39, 45, .12);
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background:
          linear-gradient(90deg, rgba(110,31,53,.05) 1px, transparent 1px),
          linear-gradient(rgba(110,31,53,.04) 1px, transparent 1px),
          var(--bg);
        background-size: 28px 28px;
        color: var(--text);
        font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      a { color: var(--teal); text-decoration: none; }
      a:hover { text-decoration: underline; }
      button { min-height: 36px; border: 0; border-radius: 6px; padding: 0 13px; color: #fff; background: var(--rose-dark); cursor: pointer; font: inherit; }
      button.secondary { background: #344c59; }
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
          linear-gradient(135deg, rgba(136,59,80,.92), rgba(32,21,29,.96) 54%, rgba(15,111,106,.86)),
          #241c21;
        border-bottom: 5px double rgba(255,255,255,.24);
      }
      .topbar::after {
        content: "";
        position: absolute;
        left: 0;
        right: 0;
        bottom: -9px;
        height: 9px;
        background: repeating-linear-gradient(90deg, rgba(255,255,255,.55) 0 10px, transparent 10px 20px);
        opacity: .55;
      }
      .eyebrow { margin: 0 0 3px; color: #ead6d6; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }
      .topbar h1 { margin: 0; font: 600 28px/1.05 Georgia, "Times New Roman", serif; }
      .topbar p { margin: 6px 0 0; max-width: 820px; color: #f2e8e6; word-break: break-word; }
      .actions { display: flex; gap: 9px; flex-wrap: wrap; justify-content: flex-end; }
      .language-switch { display: inline-flex; align-items: center; gap: 2px; padding: 2px; border: 1px solid rgba(255,255,255,.18); border-radius: 7px; background: rgba(255,255,255,.08); }
      .language-switch button { min-height: 32px; padding: 0 10px; border-radius: 5px; background: transparent; color: #c9d6dc; }
      .language-switch button.active { background: #fff; color: #14242d; }
      .metrics { display: grid; grid-template-columns: repeat(5, minmax(132px, 1fr)); gap: 12px; padding: 22px 20px 12px; }
      .metric, .panel, .atelier { background: rgba(255,253,251,.96); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); }
      .metric { min-height: 88px; display: grid; align-content: center; gap: 5px; padding: 13px 15px; border-top: 4px solid var(--rose); }
      .metric strong { font: 650 27px/1 Georgia, "Times New Roman", serif; color: var(--wine); }
      .metric span, .muted { color: var(--muted); }
      .atelier { margin: 0 20px 14px; padding: 14px; display: grid; grid-template-columns: minmax(220px, .7fr) 1fr; gap: 14px; }
      .atelier h2, .panel h2 { margin: 0; font: 650 17px/1.2 Georgia, "Times New Roman", serif; }
      .watch-grid { display: grid; grid-template-columns: repeat(4, minmax(125px, 1fr)); gap: 9px; }
      .brand-chip { border: 1px solid var(--line); border-radius: 8px; padding: 9px 10px; background: var(--bg-soft); }
      .brand-chip strong { display: block; color: var(--wine); }
      .brand-chip span { color: var(--muted); font-size: 12px; }
      .focus-list { display: grid; gap: 8px; }
      .focus-card { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fff7f7; }
      .focus-card header { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
      .focus-card strong { color: var(--wine); }
      .signal-strip { display: grid; gap: 8px; align-content: start; }
      .signal-bar { height: 11px; overflow: hidden; border-radius: 999px; background: var(--lace); }
      .signal-bar span { display: block; height: 100%; width: var(--score); background: linear-gradient(90deg, var(--teal), var(--rose), var(--gold)); }
      .workspace { display: grid; grid-template-columns: 340px 1fr; gap: 14px; padding: 0 20px 20px; }
      .panel { min-width: 0; overflow: hidden; }
      .panel h2 { padding: 14px 15px; border-bottom: 1px solid var(--line); background: linear-gradient(90deg, #fff7f7, #f8fbfa); }
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
      .toast { position: fixed; right: 16px; bottom: 16px; max-width: min(440px, calc(100vw - 32px)); padding: 10px 12px; border-radius: 8px; background: #16242d; color: #fff; box-shadow: var(--shadow); opacity: 0; transform: translateY(8px); transition: .16s; pointer-events: none; }
      .toast.show { opacity: 1; transform: translateY(0); }
      @media (max-width: 860px) {
        .topbar, .atelier, .workspace { grid-template-columns: 1fr; }
        .actions { justify-content: flex-start; }
        .metrics, .watch-grid, .event-list, .item-list { grid-template-columns: 1fr; }
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
        renderBrandWeights(state.brand_weights || []);
        renderFocusQueue(state.focus_queue || []);
        renderMarketSignal(state.events || [], state.items || []);
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
