"""Drafts: detached working copies of a node (or a whole subtree).

The UI edits a draft freely; nothing touches the workspace until
``Workspace.commit(draft)``, which applies it as a single transaction.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from . import tree, yamlio
from .model import Fragment


class DraftError(ValueError):
    pass


@dataclass
class Draft:
    origin: str                    # workspace key the draft was started from
    subtree: bool                  # True: whole subtree is one unit; False: single node
    fragment: Fragment
    renames: dict[str, str] = field(default_factory=dict)   # workspace key -> new key
    _baseline: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._baseline = yamlio.to_plain(self.fragment.nodes)

    @property
    def root(self) -> str:
        return self.fragment.root

    @property
    def data(self) -> Any:
        """The mapping of the draft's root node (edit in place)."""
        return self.fragment.nodes[self.fragment.root]

    @property
    def dirty(self) -> bool:
        return yamlio.to_plain(self.fragment.nodes) != self._baseline

    @property
    def workspace_key(self) -> str:
        """Key of the origin node in the workspace once ``renames`` are applied."""
        return self.renames.get(self.origin, self.origin)

    def rename_root(self, new: str) -> None:
        old = self.fragment.root
        if new == old:
            return
        if not new or new != new.strip():
            raise DraftError("key must be non-empty and have no surrounding whitespace")
        if new in self.fragment.nodes:
            raise DraftError(f"key {new!r} already used inside this draft")
        nodes = {(new if k == old else k): d for k, d in self.fragment.nodes.items()}
        for d in nodes.values():
            if d.get("parent") == old:
                d["parent"] = new
        self.fragment.nodes = nodes
        self.fragment.root = new
        orig = next((o for o, n in self.renames.items() if n == old), old)
        if new == orig:
            self.renames.pop(orig, None)
        else:
            self.renames[orig] = new

    # -- whole-subtree text editing -------------------------------------------------
    def to_text(self) -> str:
        doc = yamlio.new_mapping()
        for k, d in self.fragment.nodes.items():
            doc[k] = d
        return yamlio.dumps(doc)

    def replace_from_text(self, text: str) -> None:
        """Replace the draft's contents with parsed YAML (``key: node`` mapping)."""
        try:
            doc = yamlio.loads(text)
        except Exception as e:  # yaml errors vary by backend
            raise DraftError(f"invalid YAML: {e}") from e
        if not isinstance(doc, Mapping) or not doc:
            raise DraftError("expected a non-empty mapping of key: node")
        for k, d in doc.items():
            if not isinstance(k, str) or not isinstance(d, Mapping):
                raise DraftError(f"{k!r} must be a string key mapping to a mapping")
        nodes = dict(doc)
        tops = [k for k, d in nodes.items() if tree.parent_of(d) not in nodes]
        if len(tops) != 1:
            raise DraftError(f"subtree needs exactly one top node, found {tops or 'none'}")
        ordered = {k: nodes[k] for k in tree.subtree_keys(nodes, tops[0])}
        if len(ordered) != len(nodes):
            raise DraftError(f"unreachable nodes (parent loop?): {sorted(set(nodes) - set(ordered))}")
        self.fragment = Fragment(tops[0], ordered, self.fragment.meta)
        # a changed top key is a rename of the origin node (keeps file placement, re-points children)
        self.renames = {self.origin: tops[0]} if tops[0] != self.origin else {}
