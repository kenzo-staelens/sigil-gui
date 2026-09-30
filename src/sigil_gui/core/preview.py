"""Render ``--help`` for a node by building the same argparse tree sigil would.

Mirrors ``sigil/stages/builder.py`` + ``models/command.py``:
  * root  -> ``Parser(name, **parser_kwargs)``; children -> ``add_subparsers(dest=<parent name>,
    title='subcommands')`` + ``add_parser(name, help=help, **parser_kwargs)``
  * ``parser_kwargs`` = every key that is not a ParserConfig field (``dest``, ``formatter_class``
    and ``parents`` are dropped, as sigil does)
  * arguments: ``add_argument(*names, help=help, **kw)``; ``required`` is only passed for
    option-style names (any name starting with '-')
  * groups: ``kind: mutex`` -> ``add_mutually_exclusive_group(**kw)`` else ``add_argument_group(**kw)``
  * ``load: false`` commands are skipped

Uses sigil's own ``LibArgParser`` when sigil is installed. Errors are collected per command
(sigil logs and skips them too) and shown above the help text.
"""
from __future__ import annotations

import argparse
import builtins
import contextlib
import io
from collections.abc import Mapping
from typing import Any

from . import tree

try:                                    # optional: exact parser class if sigil is importable
    from sigil.models import LibArgParser as _Parser   # type: ignore
except Exception:                       # pragma: no cover - depends on the environment
    _Parser = argparse.ArgumentParser

PARSER_FIELDS = {"name", "script_dir", "known_args", "help", "args", "script", "parent",
                 "default", "subparsers", "parser_kwargs", "load"}
UNSUPPORTED = {"dest", "formatter_class", "parents"}
ARG_FIELDS = {"kind", "name", "help", "required", "args"}


def _parser_kwargs(node: Mapping) -> dict[str, Any]:
    return {k: v for k, v in node.items() if k not in PARSER_FIELDS and k not in UNSUPPORTED}


def _resolve_types(kw: dict[str, Any]) -> dict[str, Any]:
    t = kw.get("type")
    if isinstance(t, str):
        obj = getattr(builtins, t, None)
        if not callable(obj):
            raise ValueError(f"unsupported type {t!r} (builtins only)")
        kw = {**kw, "type": obj}
    return kw


def _attach(target: Any, arg: Mapping) -> None:
    if arg.get("kind", "argument") in ("group", "mutex"):
        kw = {k: v for k, v in arg.items() if k not in ARG_FIELDS}
        make = target.add_mutually_exclusive_group if arg["kind"] == "mutex" else target.add_argument_group
        grp = make(**kw)
        for sub in arg.get("args") or []:
            _attach(grp, sub)
        return
    names = arg["name"] if isinstance(arg["name"], list) else [arg["name"]]
    kw = _resolve_types({k: v for k, v in arg.items() if k not in ARG_FIELDS})
    if any(str(n).startswith("-") for n in names):
        kw["required"] = bool(arg.get("required", False))
    target.add_argument(*names, help=arg.get("help"), **kw)


def _build_subparsers(parser: Any, nodes: Mapping[str, Any], key: str, errors: list[str],
                      path: frozenset[str]) -> None:
    kids = [k for k, d in nodes.items() if tree.parent_of(d) == key and d.get("load", True) is not False
            and k not in path]
    if not kids:
        return
    group = parser.add_subparsers(dest=str(nodes[key].get("name", key)), title="subcommands")
    for k in kids:
        d = nodes[k]
        try:
            sub = group.add_parser(str(d["name"]), help=d.get("help"), **_parser_kwargs(d))
        except Exception as e:
            errors.append(f"{k}: cannot create parser: {e}")
            continue
        for a in d.get("args") or []:
            try:
                _attach(sub, a)
            except Exception as e:
                errors.append(f"{k}: cannot attach argument {a.get('name') if isinstance(a, Mapping) else a!r}: {e}")
        _build_subparsers(sub, nodes, k, errors, path | {key})


def build_parser(nodes: Mapping[str, Any], root: str) -> tuple[Any, list[str]]:
    errors: list[str] = []
    d = nodes[root]
    parser = _Parser(str(d.get("name", root)), **_parser_kwargs(d))
    for a in d.get("args") or []:
        try:
            _attach(parser, a)
        except Exception as e:
            errors.append(f"{root}: cannot attach argument {a.get('name') if isinstance(a, Mapping) else a!r}: {e}")
    _build_subparsers(parser, nodes, root, errors, frozenset())
    return parser, errors


def _descend(parser: Any, names: list[str]) -> Any:
    for name in names:
        action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
        if action is None or name not in action.choices:
            return None
        parser = action.choices[name]
    return parser


def render_help(nodes: Mapping[str, Any], key: str) -> str:
    """Help text for `key` as `<prog> ... --help` would print it, plus any build errors."""
    try:
        chain: list[str] = []
        k: str | None = key
        while k is not None and k in nodes and k not in chain:
            chain.append(k)
            k = tree.parent_of(nodes[k])
        chain.reverse()
        if nodes[key].get("load", True) is False:
            return "[load: false — sigil skips this command]"
        parser, errors = build_parser(nodes, chain[0])
        target = _descend(parser, [str(nodes[c]["name"]) for c in chain[1:]])
        if target is None:
            return "[not reachable from the root: check parents, names and load flags]"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            target.print_help()
        head = "".join(f"! {e}\n" for e in errors)
        return (head + "\n" if head else "") + buf.getvalue()
    except Exception as e:
        return f"[cannot build preview: {type(e).__name__}: {e}]"
