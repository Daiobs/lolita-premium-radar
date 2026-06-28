from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

from .models import Target


DEFAULT_CONFIG_FILE = "config.example.json"


class ConfigError(ValueError):
    pass


def load_config(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            config = json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON config {path}: {exc}") from exc

    if not isinstance(config, dict):
        raise ConfigError("Config root must be an object")
    return config


def init_config(output_path: Path) -> None:
    if output_path.exists():
        raise ConfigError(f"Refusing to overwrite existing file: {output_path}")
    package_root = Path(__file__).resolve().parents[2]
    example = package_root / DEFAULT_CONFIG_FILE
    shutil.copyfile(example, output_path)


def get_data_dir(config: Dict[str, Any], config_path: Path) -> Path:
    raw = str(config.get("data_dir", ".data"))
    path = Path(raw)
    if not path.is_absolute():
        path = config_path.parent / path
    return path


def get_poll_interval(config: Dict[str, Any]) -> int:
    interval = int(config.get("poll_interval_seconds", 90))
    if interval < 30:
        raise ConfigError("poll_interval_seconds should be at least 30")
    return interval


def get_targets(config: Dict[str, Any]) -> List[Target]:
    raw_targets = config.get("targets", [])
    if not isinstance(raw_targets, list):
        raise ConfigError("targets must be a list")

    targets = []
    for index, raw in enumerate(raw_targets):
        if not isinstance(raw, dict):
            raise ConfigError(f"targets[{index}] must be an object")
        name = str(raw.get("name", "")).strip()
        url = str(raw.get("url", "")).strip()
        if not name:
            raise ConfigError(f"targets[{index}].name is required")
        if not url:
            raise ConfigError(f"targets[{index}].url is required")

        targets.append(
            Target(
                name=name,
                url=url,
                enabled=bool(raw.get("enabled", True)),
                include_keywords=tuple(str(k) for k in raw.get("include_keywords", [])),
                exclude_keywords=tuple(str(k) for k in raw.get("exclude_keywords", [])),
                price_min=_optional_float(raw.get("price_min")),
                price_max=_optional_float(raw.get("price_max")),
            )
        )
    return targets


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
