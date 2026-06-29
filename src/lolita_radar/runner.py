from __future__ import annotations

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
from .models import RadarEvent
from .notifiers import build_notifiers_from_env, notify_all
from .storage import connect, diff_and_store, record_source_run


ADAPTERS: dict[str, type[SourceAdapter]] = {
    "alice_and_the_pirates": AliceAndThePiratesAdapter,
    "angelic_pretty": AngelicPrettyAdapter,
    "baby_ssb": BabySsbAdapter,
    "metamorphose": MetamorphoseAdapter,
    "generic_page": GenericPageAdapter,
    "innocent_world": InnocentWorldAdapter,
    "moitie": MoitieAdapter,
}


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
                events = diff_and_store(connection, items)
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
    if notify:
        notify_all(build_notifiers_from_env(), all_events)
    return all_events


def select_sources(sources: dict[str, SourceConfig], source_name: str | None) -> list[SourceConfig]:
    if source_name:
        source = sources.get(source_name)
        if source is None:
            raise ValueError(f"Source not found: {source_name}")
        return [source]
    return [source for source in sources.values() if source.enabled]
