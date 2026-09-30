"""Repository of reusable subcommand fragments.

One YAML file per fragment (valid sigil config, see ``model.Fragment``) in a
directory of your choice. Default: ``$SIGIL_GUI_LIBRARY`` or a per-user data dir.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import yamlio
from .model import Fragment


def default_library_dir() -> Path:
    env = os.environ.get("SIGIL_GUI_LIBRARY")
    if env:
        return Path(env)
    try:
        from platformdirs import user_data_dir
        return Path(user_data_dir("sigil-gui")) / "library"
    except ImportError:
        return Path.home() / ".sigil-gui" / "library"


@dataclass(frozen=True)
class Entry:
    name: str
    path: Path
    description: str
    tags: tuple[str, ...]
    size: int              # number of nodes in the fragment


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-.")
    if not s:
        raise ValueError("empty fragment name")
    return s


class Library:
    def __init__(self, directory: Path | str | None = None):
        self.dir = Path(directory) if directory else default_library_dir()

    def list(self) -> list[Entry]:
        out = []
        for p in sorted(self.dir.glob("*.yml")) if self.dir.exists() else []:
            try:
                frag = Fragment.from_doc(yamlio.read(p))
            except Exception:
                continue            # not a fragment; ignore rather than crash the UI
            out.append(Entry(p.stem, p, str(frag.meta.get("description", "")),
                             tuple(frag.meta.get("tags", []) or []), len(frag.nodes)))
        return out

    def path(self, name: str) -> Path:
        return self.dir / f"{_slug(name)}.yml"

    def load(self, name: str) -> Fragment:
        return Fragment.from_doc(yamlio.read(self.dir / f"{_slug(name)}.yml"))

    def save(self, name: str, frag: Fragment, description: str = "",
             tags: list[str] | None = None, overwrite: bool = False) -> Path:
        path = self.dir / f"{_slug(name)}.yml"
        if path.exists() and not overwrite:
            raise FileExistsError(path.name)
        nodes = {k: dict(d) for k, d in frag.nodes.items()}
        nodes[frag.root].pop("parent", None)          # a fragment root is parentless by definition
        frag = Fragment(frag.root, nodes,
                        {**frag.meta, "description": description, "tags": list(tags or [])})
        path.parent.mkdir(parents=True, exist_ok=True)
        yamlio.write(path, frag.to_doc())
        return path

    def delete(self, name: str) -> None:
        (self.dir / f"{_slug(name)}.yml").unlink(missing_ok=True)
