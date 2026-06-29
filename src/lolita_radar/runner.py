from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .adapters import (
    AliceAndThePiratesAdapter,
    AngelicPrettyAdapter,
    BabySsbAdapter,
    GenericPageAdapter,
    InnocentWorldAdapter,
    MetamorphoseAdapter,
    MoitieAdapter,
    SourceAdapter,
    SourceConfig,
)
from .config import load_sources
from .models import RadarEvent, RadarItem
from .notifiers import build_notifiers_from_env, notify_all
from .storage import connect, diff_and_store, list_latest_source_runs, record_source_run


ADAPTERS: dict[str, type[SourceAdapter]] = {
    "alice_and_the_pirates": AliceAndThePiratesAdapter,
    "angelic_pretty": AngelicPrettyAdapter,
    "baby_ssb": BabySsbAdapter,
    "metamorphose": MetamorphoseAdapter,
    "generic_page": GenericPageAdapter,
    "innocent_world": InnocentWorldAdapter,
    "moitie": MoitieAdapter,
}


@dataclass(frozen=True)
class InspectResult:
    source: SourceConfig
    ok: bool
    items: list[RadarItem]
    error_message: str = ""
    warnings: tuple[str, ...] = ()


def build_adapter(config: SourceConfig) -> SourceAdapter:
    adapter_cls = ADAPTERS.get(config.type)
    if adapter_cls is None:
        raise ValueError(f"Unknown source type for {config.name}: {config.type}")
    return adapter_cls(config)


def check_sources(
    config_path: Path,
    db_path: Path,
    source_name: str | None = None,
    notify: bool = True,
    baseline_only: bool = False,
) -> list[RadarEvent]:
    sources = load_sources(config_path)
    selected = select_sources(sources, source_name)
    connection = connect(db_path)
    try:
        all_events: list[RadarEvent] = []
        for source in selected:
            try:
                adapter = build_adapter(source)
                items = adapter.fetch_items()
                events = diff_and_store(connection, items, write_events=not baseline_only)
            except Exception as exc:
                record_source_run(connection, source.name, ok=False, error_message=str(exc))
                connection.commit()
                if source_name:
                    raise
                continue
            record_source_run(connection, source.name, ok=True, item_count=len(items), event_count=len(events))
            connection.commit()
            all_events.extend(events)
    finally:
        connection.close()
    if notify and not baseline_only:
        notify_all(build_notifiers_from_env(), all_events)
    return all_events


def inspect_sources(config_path: Path, source_name: str | None = None) -> list[InspectResult]:
    sources = load_sources(config_path)
    selected = select_sources(sources, source_name)
    results: list[InspectResult] = []
    for source in selected:
        try:
            adapter = build_adapter(source)
            items = adapter.fetch_items()
        except Exception as exc:
            results.append(
                InspectResult(
                    source=source,
                    ok=False,
                    items=[],
                    error_message=str(exc),
                    warnings=(f"fetch failed: {exc}",),
                )
            )
            continue
        results.append(InspectResult(source=source, ok=True, items=items, warnings=inspect_warnings(items)))
    return results


def inspect_warnings(items: list[RadarItem]) -> tuple[str, ...]:
    warnings: list[str] = []
    if not items:
        warnings.append("empty result")
    missing_date_count = sum(1 for item in items if not item.published_at)
    if missing_date_count:
        warnings.append(f"missing dates: {missing_date_count} item(s)")
    navigation_tokens = ("login", "cart", "privacy", "contact", "shop list", "company", "account")
    possible_navigation = [
        item.title
        for item in items
        if any(token in f"{item.title} {item.url}".lower() for token in navigation_tokens)
    ]
    if possible_navigation:
        warnings.append(f"possible navigation links: {len(possible_navigation)}")
    return tuple(warnings)


def latest_source_health(config_path: Path, db_path: Path) -> list[dict[str, object]]:
    sources = load_sources(config_path)
    connection = connect(db_path)
    try:
        latest = {row["source"]: row for row in list_latest_source_runs(connection)}
    finally:
        connection.close()
    rows: list[dict[str, object]] = []
    for source in sources.values():
        run = latest.get(source.name)
        rows.append(
            {
                "source": source.name,
                "enabled": source.enabled,
                "ok": run["ok"] if run else None,
                "item_count": run["item_count"] if run else 0,
                "event_count": run["event_count"] if run else 0,
                "checked_at": run["checked_at"] if run else "",
                "error_message": run["error_message"] if run else "no run recorded",
            }
        )
    return rows


def select_sources(sources: dict[str, SourceConfig], source_name: str | None) -> list[SourceConfig]:
    if source_name:
        source = sources.get(source_name)
        if source is None:
            raise ValueError(f"Source not found: {source_name}")
        return [source]
    return [source for source in sources.values() if source.enabled]
