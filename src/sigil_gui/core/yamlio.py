"""YAML I/O on plain PyYAML (same library sigil itself uses).

Comments are not preserved and anchors/aliases are expanded on write, so the
workspace only rewrites files whose *data* actually changed (see Workspace.save).
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:      # never emit &id001 / *id001
        return True

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> Any:
        return super().increase_indent(flow, False)   # indent list items under their key


def loads(text: str) -> Any:
    return yaml.safe_load(text)


def dumps(data: Any, kind: str = "config") -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, default_flow_style=False,
                     allow_unicode=True, width=4096, indent=2)


def read(path: Path) -> Any:
    return loads(Path(path).read_text(encoding="utf-8"))


def write(path: Path, data: Any, kind: str = "config") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(data, kind), encoding="utf-8")


def new_mapping() -> dict:
    return {}


def to_plain(obj: Any) -> Any:
    """Deep copy into plain dict/list/scalars (also breaks any shared references)."""
    if isinstance(obj, Mapping):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    return obj
