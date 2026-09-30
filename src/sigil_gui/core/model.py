"""Thin wrappers around raw config mappings.

Sigil forwards unknown keys straight to argparse, so nothing here restricts
which keys a node may have. ``Node.data`` is the live mapping inside its file
document (``Node.file`` is that file's path relative to the manifest); the properties are convenience accessors only. The GUI has dedicated
widgets for ``COMMAND_KEYS``/``ARG_KEYS`` and edits everything else as raw YAML.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from . import tree

ARG_KINDS = ("argument", "group", "mutex")
COMMAND_KEYS = ("name", "parent", "help", "script", "script_dir", "known_args", "default", "load", "args")
ARG_KEYS = ("kind", "name", "help", "type", "default", "required", "action", "args")


class Node:
    __slots__ = ("key", "data", "file")

    def __init__(self, key: str, data: Any, file: str):
        self.key = key
        self.data = data
        self.file = file

    @property
    def parent(self) -> str | None:
        return tree.parent_of(self.data)

    @property
    def name(self) -> str:
        n = self.data.get("name")
        return str(n) if n is not None else self.key

    @property
    def loads(self) -> bool:
        return self.data.get("load", True) is not False


@dataclass
class Fragment:
    """A self-contained subtree: ``nodes`` are deep copies, parents first.

    Serialised as an ordinary sigil YAML file with one extra top-level object
    (``_fragment``, ``load: false``) so the file is still valid sigil config.
    """

    root: str
    nodes: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    META_KEY: ClassVar[str] = "_fragment"

    def to_doc(self) -> Any:
        doc: dict[str, Any] = {}
        meta: dict[str, Any] = {}
        meta["load"] = False
        meta["root"] = self.root
        for k, v in self.meta.items():
            if k not in ("load", "root"):
                meta[k] = v
        doc[self.META_KEY] = meta
        for k, d in self.nodes.items():
            doc[k] = d
        return doc

    @classmethod
    def from_doc(cls, doc: Mapping) -> "Fragment":
        meta = dict(doc.get(cls.META_KEY) or {})
        nodes = {k: d for k, d in doc.items() if k != cls.META_KEY and isinstance(d, Mapping)}
        root = meta.get("root")
        if root not in nodes:
            tops = [k for k, d in nodes.items() if tree.parent_of(d) not in nodes]
            if len(tops) != 1:
                raise ValueError(f"fragment needs exactly one root, found {tops or 'none'}")
            root = tops[0]
        ordered = {k: nodes[k] for k in tree.subtree_keys(nodes, root)}
        if len(ordered) != len(nodes):
            extra = sorted(set(nodes) - set(ordered))
            raise ValueError(f"nodes not reachable from root {root!r}: {extra}")
        meta.pop("load", None)
        meta.pop("root", None)
        return cls(root=root, nodes=ordered, meta=meta)
