"""Tree helpers over a plain ``{key: raw_mapping}`` dict.

Sigil links commands through ``parent: <key>`` (the YAML top-level key, not the
``name``). Everything here works on that flat mapping, so the same code serves
the workspace, drafts and library fragments.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def parent_of(data: Any) -> str | None:
    p = data.get("parent") if isinstance(data, Mapping) else None
    return p if isinstance(p, str) else None


def child_map(nodes: Mapping[str, Any]) -> dict[str | None, list[str]]:
    out: dict[str | None, list[str]] = {}
    for key, data in nodes.items():
        out.setdefault(parent_of(data), []).append(key)
    return out


def subtree_keys(nodes: Mapping[str, Any], key: str) -> list[str]:
    """`key` and all descendants, parents before children. Cycle-safe."""
    cm = child_map(nodes)
    out: list[str] = []
    seen: set[str] = set()
    stack = [key]
    while stack:
        k = stack.pop()
        if k in seen or k not in nodes:
            continue
        seen.add(k)
        out.append(k)
        stack.extend(reversed(cm.get(k, [])))
    return out


def find_cycles(nodes: Mapping[str, Any]) -> set[str]:
    """Keys that sit on a parent-loop."""
    bad: set[str] = set()
    for start in nodes:
        seen: list[str] = []
        k: str | None = start
        while k is not None and k in nodes and k not in seen:
            seen.append(k)
            k = parent_of(nodes[k])
        if k is not None and k in seen:
            bad.update(seen[seen.index(k):])
    return bad


def root_of(nodes: Mapping[str, Any], key: str) -> str:
    seen = {key}
    while True:
        p = parent_of(nodes.get(key))
        if p is None or p not in nodes or p in seen:
            return key
        seen.add(p)
        key = p
