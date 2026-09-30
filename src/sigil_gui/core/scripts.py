"""Command scripts: location, template and light sanity checks.

Contract (sigil README): a script defines
``def run(args: argparse.Namespace, ctx: dict[str, Any]) -> None``.
``script`` is a module name (dots = packages) relative to the root's ``script_dir``, which is
relative to the config root (the folder holding the manifest); absolute paths are allowed.
"""
from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import tree

TEMPLATE = '''import argparse
from typing import Any


def run(args: argparse.Namespace, ctx: dict[str, Any]) -> None:
    pass
'''


def script_ref(data: Any) -> str | None:
    s = data.get("script") if isinstance(data, Mapping) else None
    return s if isinstance(s, str) and s.strip() else None


def is_absolute(ref: str) -> bool:
    return Path(ref).is_absolute()


def module_path(base: Path, ref: str) -> Path:
    flat = base / f"{ref}.py"
    nested = base / (ref.replace(".", "/") + ".py")
    return flat if flat.exists() or not nested.exists() else nested


def script_dir_for(root: Path, nodes: Mapping[str, Any], key: str) -> Path | None:
    """The script folder that applies to `key` (declared on the top of its parent chain)."""
    if key not in nodes:
        return None
    sd = nodes[tree.root_of(nodes, key)].get("script_dir")
    return root / sd if isinstance(sd, str) and sd else None


def script_path(root: Path, nodes: Mapping[str, Any], key: str, ref: str | None = None) -> Path | None:
    """Where `key`'s script lives, or None if it has none / it cannot be determined."""
    ref = ref or script_ref(nodes.get(key))
    if not ref:
        return None
    if is_absolute(ref):
        p = Path(ref)
        return p if p.suffix else p.with_suffix(".py")
    base = script_dir_for(root, nodes, key)
    return module_path(base, ref) if base else None


def check_source(text: str) -> str | None:
    """None if the source looks fine, else a short problem description."""
    try:
        tree_ = ast.parse(text)
    except SyntaxError as e:
        return f"syntax error line {e.lineno}: {e.msg}"
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run" for n in tree_.body):
        return "no top-level run(args, ctx) function"
    return None
