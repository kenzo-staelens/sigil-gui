"""Structural checks. Deliberately never rejects unknown keys (see model.py)."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import tree
from . import scripts as sc
from .model import ARG_KINDS

# parser kwargs sigil drops with a warning (models/command.py: UNSUPPORTED_KWARGS)
UNSUPPORTED = {"dest", "formatter_class", "parents"}


@dataclass(frozen=True)
class Issue:
    severity: str          # "error" | "warning"
    code: str
    key: str | None
    message: str
    fatal: bool = False    # fatal issues make a transaction roll back


def resolve_script(ws: Any, key: str) -> Path | None:
    """Best-effort location of a node's script file, or None if not determinable."""
    return sc.script_path(ws.root, ws.raw, key)


def _check_args(args: Any, key: str, trail: str, out: list[Issue]) -> None:
    if not isinstance(args, list):
        out.append(Issue("error", "bad-args", key, f"{trail} must be a list"))
        return
    for i, a in enumerate(args):
        where = f"{trail}[{i}]"
        if not isinstance(a, Mapping):
            out.append(Issue("error", "bad-args", key, f"{where} must be a mapping"))
            continue
        kind = a.get("kind", "argument")
        if kind == "argument":
            if not a.get("name"):
                out.append(Issue("error", "arg-name", key, f"{where} has no name"))
        elif kind in ARG_KINDS:
            if a.get("args") is None:
                out.append(Issue("warning", "empty-group", key, f"{where} ({kind}) has no args"))
            else:
                _check_args(a["args"], key, f"{where}.args", out)
        else:
            out.append(Issue("error", "arg-kind", key, f"{where} has unknown kind {kind!r}"))


def validate(ws: Any) -> list[Issue]:
    out: list[Issue] = []
    raw = {k: n.data for k, n in ws.index.items()}

    for key, f1, f2 in ws.duplicates:
        out.append(Issue("error", "duplicate-key", key, f"{key!r} defined in {f1} and {f2}", True))
    for entry in sorted(ws.missing_files):
        out.append(Issue("warning", "missing-file", None, f"manifest lists {entry} but it does not exist"))
    for pattern in ws.empty_patterns:
        out.append(Issue("warning", "empty-glob", None, f"manifest pattern {pattern!r} matches no files"))
    if ws.kind == "fragment" and not ws.index:
        out.append(Issue("warning", "empty-fragment", None, "fragment has no nodes"))

    roots: list[str] = []
    for key, node in ws.index.items():
        d = node.data
        p = d.get("parent")
        if p is not None and not isinstance(p, str):
            out.append(Issue("error", "bad-parent", key, "parent must be a key (string)", True))
            continue
        if not node.loads:
            continue
        if p is None:
            roots.append(key)
        elif p not in raw:
            out.append(Issue("error", "dangling-parent", key, f"parent {p!r} does not exist", True))
        elif not ws.index[p].loads:
            out.append(Issue("warning", "parent-not-loaded", key, f"parent {p!r} has load: false"))
        if not d.get("name"):
            out.append(Issue("error", "missing-name", key, "command has no name (sigil requires one)"))
        for bad in sorted(UNSUPPORTED & set(d)):
            hint = " (did you mean `parent`?)" if bad == "parents" else ""
            out.append(Issue("warning", "unsupported-kwarg", key, f"`{bad}` is ignored by sigil{hint}"))
        if "args" in d:
            _check_args(d["args"], key, "args", out)
        path = resolve_script(ws, key) if ws.kind == "project" else None
        if path is not None and not path.exists():
            out.append(Issue("warning", "missing-script", key, f"script not found: {path}"))

    for key in sorted(tree.find_cycles(raw)):
        out.append(Issue("error", "cycle", key, "parent chain loops back on itself", True))
    for extra in roots[1:]:
        out.append(Issue("error", "multiple-roots", extra, "more than one top-level command"))

    by_parent: dict[str | None, list[str]] = {}
    for key in raw:
        if ws.index[key].loads:
            by_parent.setdefault(tree.parent_of(raw[key]), []).append(key)
    for parent, kids in by_parent.items():
        if parent is None:
            continue
        defaults = [k for k in kids if raw[k].get("default") is True]
        if len(defaults) > 1:
            out.append(Issue("warning", "multiple-defaults", defaults[-1],
                             f"several default subcommands under {parent!r}; sigil keeps the last"))
        counts = Counter(str(raw[k].get("name")) for k in kids)
        for k in kids:
            if raw[k].get("name") is not None and counts[str(raw[k].get("name"))] > 1:
                out.append(Issue("error", "name-clash", k, f"sibling name {raw[k].get('name')!r} used more than once"))
    return out
