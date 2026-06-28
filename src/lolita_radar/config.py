from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters import SourceConfig


class ConfigError(ValueError):
    pass


def load_sources(path: Path) -> dict[str, SourceConfig]:
    with path.open("r", encoding="utf-8") as fh:
        text = fh.read()
    raw = parse_yaml(text)
    raw_sources = raw.get("sources")
    if not isinstance(raw_sources, dict):
        raise ConfigError("sources.yaml must contain a sources mapping")
    sources: dict[str, SourceConfig] = {}
    for name, value in raw_sources.items():
        if not isinstance(value, dict):
            raise ConfigError(f"sources.{name} must be an object")
        url = str(value.get("url", "")).strip()
        source_type = str(value.get("type", "")).strip()
        if not url:
            raise ConfigError(f"sources.{name}.url is required")
        if not source_type:
            raise ConfigError(f"sources.{name}.type is required")
        keywords = value.get("keywords") or []
        if not isinstance(keywords, list):
            raise ConfigError(f"sources.{name}.keywords must be a list")
        options = value.get("options") or {}
        if not isinstance(options, dict):
            raise ConfigError(f"sources.{name}.options must be an object")
        sources[str(name)] = SourceConfig(
            name=str(name),
            type=source_type,
            url=url,
            enabled=bool(value.get("enabled", True)),
            keywords=[str(item) for item in keywords],
            options={str(key): item for key, item in options.items()},
        )
    return sources


def default_config_path() -> Path:
    return Path("config") / "sources.yaml"


def parse_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]

        parsed = yaml.safe_load(text) or {}
        if not isinstance(parsed, dict):
            raise ConfigError("YAML root must be an object")
        return parsed
    except ModuleNotFoundError:
        return parse_sources_yaml_fallback(text)


def parse_sources_yaml_fallback(text: str) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    current_name = ""
    current_list_key = ""
    in_sources = False
    for raw_line in text.splitlines():
        line_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not line_without_comment.strip():
            continue
        stripped = line_without_comment.strip()
        indent = len(line_without_comment) - len(line_without_comment.lstrip(" "))
        if indent == 0:
            in_sources = stripped == "sources:"
            continue
        if not in_sources:
            continue
        if indent == 2 and stripped.endswith(":"):
            current_name = stripped[:-1]
            sources[current_name] = {}
            current_list_key = ""
            continue
        if not current_name:
            continue
        if indent == 4 and ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            value = parse_scalar(raw_value.strip())
            if value == "":
                value = [] if key == "keywords" else {}
            sources[current_name][key] = value
            current_list_key = key if isinstance(value, list) else ""
            continue
        if indent == 6 and stripped.startswith("- ") and current_list_key:
            sources[current_name].setdefault(current_list_key, []).append(parse_scalar(stripped[2:].strip()))
            continue
        if indent == 6 and ":" in stripped and isinstance(sources[current_name].get("options"), dict):
            key, raw_value = stripped.split(":", 1)
            sources[current_name]["options"][key] = parse_scalar(raw_value.strip())
    return {"sources": sources}


def parse_scalar(value: str) -> Any:
    if value == "":
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value == "[]":
        return []
    if value == "{}":
        return {}
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value
