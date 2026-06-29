from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .adapters import SourceConfig
from .config import load_sources
from .models import RadarEvent
from .runner import check_sources
from .storage import connect, list_events, list_items, storage_counts


DEFAULT_WEB_PORT = 8766


def run_web(
    config_path: Path,
    db_path: Path,
    host: str = "127.0.0.1",
    port: int = DEFAULT_WEB_PORT,
) -> int:
    handler = make_handler(config_path=config_path, db_path=db_path)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Lolita Premium Radar web UI: http://{host}:{port}")
    print(f"Config: {config_path.resolve()}")
    print(f"Database: {db_path.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web UI")
    finally:
        server.server_close()
    return 0


def make_handler(config_path: Path, db_path: Path) -> type[BaseHTTPRequestHandler]:
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
                    self.send_json(get_dashboard_state(config_path, db_path))
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
                state = get_dashboard_state(config_path, db_path)
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


def get_dashboard_state(config_path: Path, db_path: Path) -> dict[str, Any]:
    sources = load_sources(config_path)
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
        "counts": {
            **counts,
            "sources": len(sources),
            "enabled_sources": sum(1 for source in sources.values() if source.enabled),
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
        --bg: #eef3f5;
        --panel: #ffffff;
        --text: #16242d;
        --muted: #64747f;
        --line: #d8e2e7;
        --accent: #0f766e;
        --accent-dark: #115e59;
        --warn: #a44322;
        --shadow: 0 12px 30px rgba(24, 38, 48, .08);
      }
      * { box-sizing: border-box; }
      body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
      a { color: var(--accent-dark); text-decoration: none; }
      a:hover { text-decoration: underline; }
      button { min-height: 36px; border: 0; border-radius: 6px; padding: 0 13px; color: #fff; background: var(--accent); cursor: pointer; font: inherit; }
      button.secondary { background: #315d78; }
      button[disabled] { background: #93a3ab; }
      button:disabled { opacity: .65; cursor: wait; }
      .topbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 18px 22px; background: #14242d; color: #fff; }
      .topbar h1 { margin: 0; font-size: 23px; }
      .topbar p { margin: 3px 0 0; color: #b7c7cf; word-break: break-all; }
      .actions { display: flex; gap: 9px; flex-wrap: wrap; justify-content: flex-end; }
      .metrics { display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 10px; padding: 14px 18px; }
      .metric, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); }
      .metric { min-height: 76px; display: grid; align-content: center; gap: 3px; padding: 10px 13px; }
      .metric strong { font-size: 26px; line-height: 1; }
      .metric span, .muted { color: var(--muted); }
      .workspace { display: grid; grid-template-columns: 320px 1fr; gap: 14px; padding: 0 18px 18px; }
      .panel { min-width: 0; overflow: hidden; }
      .panel h2 { margin: 0; padding: 13px 14px; border-bottom: 1px solid var(--line); font-size: 16px; }
      .source-list, .event-list, .item-list { display: grid; gap: 8px; padding: 12px; }
      .source-card, .row { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fbfdfe; }
      .source-card header, .row header { display: flex; justify-content: space-between; align-items: start; gap: 10px; margin-bottom: 6px; }
      .source-card strong, .row strong { overflow-wrap: anywhere; }
      .pill { display: inline-flex; align-items: center; min-height: 24px; padding: 0 8px; border-radius: 999px; background: #e8f5f2; color: var(--accent-dark); font-size: 12px; white-space: nowrap; }
      .pill.off { background: #eef1f3; color: var(--muted); }
      .pill.warn { background: #fff1ed; color: var(--warn); }
      .row p, .source-card p { margin: 0; color: var(--muted); overflow-wrap: anywhere; }
      .main-stack { display: grid; gap: 14px; }
      .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--line); }
      .toolbar h2 { border: 0; padding: 0; }
      .toast { position: fixed; right: 16px; bottom: 16px; max-width: min(440px, calc(100vw - 32px)); padding: 10px 12px; border-radius: 8px; background: #16242d; color: #fff; box-shadow: var(--shadow); opacity: 0; transform: translateY(8px); transition: .16s; pointer-events: none; }
      .toast.show { opacity: 1; transform: translateY(0); }
      @media (max-width: 860px) {
        .topbar { align-items: stretch; flex-direction: column; }
        .actions { justify-content: flex-start; }
        .metrics, .workspace { grid-template-columns: 1fr; }
      }
    </style>
  </head>
  <body>
    <header class="topbar">
      <div>
        <h1>Lolita Premium Radar</h1>
        <p id="paths">Loading...</p>
      </div>
      <div class="actions">
        <button id="checkAllBtn">检查全部</button>
        <button id="refreshBtn" class="secondary">刷新</button>
      </div>
    </header>
    <section class="metrics" id="metrics"></section>
    <main class="workspace">
      <section class="panel">
        <h2>数据源</h2>
        <div id="sources" class="source-list"></div>
      </section>
      <div class="main-stack">
        <section class="panel">
          <div class="toolbar">
            <h2>最近事件</h2>
            <span id="eventCount" class="muted"></span>
          </div>
          <div id="events" class="event-list"></div>
        </section>
        <section class="panel">
          <div class="toolbar">
            <h2>跟踪条目</h2>
            <span id="itemCount" class="muted"></span>
          </div>
          <div id="items" class="item-list"></div>
        </section>
      </div>
    </main>
    <div id="toast" class="toast"></div>
    <script>
      const $ = (id) => document.getElementById(id);
      let currentState = null;

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
          ["Sources", `${counts.enabled_sources || 0}/${counts.sources || 0}`],
          ["Tracked Items", counts.items || 0],
          ["Events", counts.events || 0],
          ["Latest Event", state.events?.[0]?.event_type || "-"],
          ["Latest Source", state.events?.[0]?.source || "-"],
        ].map(([label, value]) => `<article class="metric"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></article>`).join("");
        $("sources").innerHTML = state.sources.length ? state.sources.map(renderSource).join("") : `<div class="row">No sources configured.</div>`;
        $("eventCount").textContent = `${state.events.length} shown`;
        $("events").innerHTML = state.events.length ? state.events.map(renderEvent).join("") : `<div class="row">No events yet. Run a check to build the baseline.</div>`;
        $("itemCount").textContent = `${state.items.length} shown`;
        $("items").innerHTML = state.items.length ? state.items.map(renderItem).join("") : `<div class="row">No tracked items yet.</div>`;
      }

      function renderSource(source) {
        const keywords = source.keywords.length ? source.keywords.join(", ") : "no keywords";
        return `<article class="source-card">
          <header>
            <strong>${escapeHtml(source.name)}</strong>
            <span class="pill ${source.enabled ? "" : "off"}">${source.enabled ? "enabled" : "disabled"}</span>
          </header>
          <p>${escapeHtml(source.type)} · ${escapeHtml(keywords)}</p>
          <p><a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(source.url)}</a></p>
          <button data-source="${escapeHtml(source.name)}" ${source.enabled ? "" : "disabled"}>${source.enabled ? "检查此源" : "已停用"}</button>
        </article>`;
      }

      function renderEvent(event) {
        return `<article class="row">
          <header>
            <strong><a href="${escapeHtml(event.url)}" target="_blank" rel="noreferrer">${escapeHtml(event.title)}</a></strong>
            <span class="pill ${event.event_type === "update" ? "warn" : ""}">${escapeHtml(event.event_type)}</span>
          </header>
          <p>${escapeHtml(event.source)} · ${escapeHtml(event.status)} · ${escapeHtml(event.created_at || "")}</p>
        </article>`;
      }

      function renderItem(item) {
        return `<article class="row">
          <header>
            <strong><a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a></strong>
            <span class="pill">${escapeHtml(item.status)}</span>
          </header>
          <p>${escapeHtml(item.source)} · ${escapeHtml(item.published_at || "undated")} · seen ${escapeHtml(item.last_seen_at || "")}</p>
        </article>`;
      }

      async function runCheck(source = null) {
        setBusy(true);
        try {
          const payload = await api("/api/check", { method: "POST", body: JSON.stringify({ source, notify: false }) });
          currentState = payload;
          render(payload);
          toast(`${source || "all"} checked: ${payload.new_event_count || 0} new events`);
        } catch (error) {
          toast(error.message);
        } finally {
          setBusy(false);
        }
      }

      function setBusy(busy) {
        document.querySelectorAll("button").forEach((button) => button.disabled = busy);
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

      $("checkAllBtn").addEventListener("click", () => runCheck(null));
      $("refreshBtn").addEventListener("click", () => loadState().then(() => toast("refreshed")).catch((error) => toast(error.message)));
      $("sources").addEventListener("click", (event) => {
        const button = event.target.closest("button[data-source]");
        if (button) runCheck(button.dataset.source);
      });
      loadState().catch((error) => toast(error.message));
    </script>
  </body>
</html>"""
