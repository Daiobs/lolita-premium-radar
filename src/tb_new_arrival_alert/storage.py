import json
from pathlib import Path
from typing import Dict, Iterable, Set

from .models import Item


class SeenStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: Dict[str, Set[str]] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.data = {}
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.data = {target: set(items) for target, items in raw.items()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {target: sorted(items) for target, items in self.data.items()}
        self.path.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def has_baseline(self, target_name: str) -> bool:
        return target_name in self.data

    def new_items(self, target_name: str, items: Iterable[Item]) -> list[Item]:
        seen = self.data.setdefault(target_name, set())
        return [item for item in items if item.item_id not in seen]

    def mark_seen(self, target_name: str, items: Iterable[Item]) -> None:
        seen = self.data.setdefault(target_name, set())
        for item in items:
            seen.add(item.item_id)

