"""Form widgets that edit a raw mapping in place.

Convention: a field whose value is empty is *removed* from the mapping rather
than stored as ""/None, so the YAML stays minimal. Anything without a dedicated
widget (nargs, choices, metavar, future argparse options...) goes through
``extra_field`` as raw YAML, which is what keeps the editor version-proof.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from types import SimpleNamespace
from typing import Any

from nicegui import ui

from ..core import yamlio
from ..core.model import ARG_KINDS

BUILTIN_TYPES = ["", "str", "int", "float", "complex", "bool", "bytes"]
ACTIONS = ["", "store", "store_const", "store_true", "store_false", "append", "append_const",
           "count", "extend", "help", "version", "BooleanOptionalAction"]
ARG_MANAGED = {"kind", "name", "help", "type", "default", "required", "action"}
GROUP_MANAGED = {"kind", "args"}
COMMAND_MANAGED = {"name", "parent", "help", "script", "script_dir", "known_args", "default", "load", "args"}


def put(data: MutableMapping, key: str, value: Any) -> None:
    if value in (None, "", []):
        data.pop(key, None)
    else:
        data[key] = value


def text_field(data: MutableMapping, key: str, label: str, touch: Callable[[], None]) -> ui.input:
    def changed(e: Any) -> None:
        put(data, key, e.value)
        touch()

    cur = data.get(key)
    return ui.input(label, value="" if cur is None else str(cur), on_change=changed) \
        .props("outlined dense").classes("w-full")


def switch_field(data: MutableMapping, key: str, label: str, touch: Callable[[], None],
                 default: bool = False) -> ui.switch:
    """Stores only the non-default value, so untouched files stay untouched."""
    def changed(e: Any) -> None:
        if bool(e.value) == default:
            data.pop(key, None)
        else:
            data[key] = bool(e.value)
        touch()

    return ui.switch(label, value=bool(data.get(key, default)), on_change=changed)


def choice_field(data: MutableMapping, key: str, label: str, options: list[str],
                 touch: Callable[[], None]) -> ui.select:
    cur = data.get(key)
    cur = "" if cur is None else str(cur)
    opts = options if cur in options else [*options, cur]

    def changed(e: Any) -> None:
        put(data, key, e.value)
        touch()

    return ui.select(opts, value=cur, label=label, on_change=changed) \
        .props("outlined dense options-dense").classes("w-full")


def default_field(data: MutableMapping, touch: Callable[[], None]) -> ui.input:
    """`default` is typed: '8080' -> int, 'true' -> bool, anything unparseable -> str."""
    cur = data.get("default")

    def changed(e: Any) -> None:
        text = (e.value or "").strip()
        if not text:
            data.pop("default", None)
        else:
            try:
                data["default"] = yamlio.to_plain(yamlio.loads(text))
            except Exception:
                data["default"] = text
        touch()

    return ui.input("default (YAML value)", value="" if cur is None else str(cur), on_change=changed) \
        .props("outlined dense").classes("w-full")


def names_field(data: MutableMapping, touch: Callable[[], None]) -> ui.input:
    """Argument names: '-p, --port' <-> ['-p', '--port']; a single name stays a string."""
    cur = data.get("name")
    shown = ", ".join(cur) if isinstance(cur, list) else ("" if cur is None else str(cur))

    def changed(e: Any) -> None:
        parts = [p.strip() for p in (e.value or "").split(",") if p.strip()]
        put(data, "name", parts if len(parts) > 1 else (parts[0] if parts else None))
        touch()

    return ui.input("name(s), comma separated", value=shown, on_change=changed) \
        .props("outlined dense").classes("w-full")


def extra_field(data: MutableMapping, managed: set[str], touch: Callable[[], None],
                label: str = "other options (YAML)") -> ui.textarea:
    extra = {k: v for k, v in data.items() if k not in managed}
    area = ui.textarea(label, value=yamlio.dumps(extra).strip() if extra else "") \
        .props("outlined dense autogrow input-class=font-mono").classes("w-full")

    def commit() -> None:
        try:
            text = area.value or ""
            new = yamlio.loads(text) if text.strip() else {}
            if not isinstance(new, Mapping):
                raise ValueError("must be a mapping of key: value")
            clash = sorted(set(new) & managed)
            if clash:
                raise ValueError(f"{clash} have their own fields above")
        except Exception as e:
            ui.notify(f"{label}: {e}", type="negative")
            return
        if yamlio.to_plain(new) == yamlio.to_plain(extra):
            return
        for k in list(data.keys()):
            if k not in managed:
                del data[k]
        for k, v in new.items():
            data[k] = v
        extra.clear()
        extra.update(yamlio.to_plain(new))
        touch()

    area.on("blur", commit)
    return area


# -- argument list ------------------------------------------------------------------
def args_editor(owner: MutableMapping, ctx: SimpleNamespace, depth: int = 0) -> None:
    """Edit ``owner['args']`` recursively. ctx.touch(): value edited; ctx.restructure(): list changed."""
    items = owner.get("args")
    items = items if isinstance(items, list) else []
    for i, arg in enumerate(items):
        if isinstance(arg, MutableMapping):
            _arg_card(owner, items, i, arg, ctx, depth)

    def add(kind: str) -> None:
        if not isinstance(owner.get("args"), list):
            owner["args"] = []
        owner["args"].append({"name": "--new"} if kind == "argument" else {"kind": kind, "args": []})
        ctx.restructure()

    with ui.row().classes("gap-1").style(f"margin-left:{depth * 14}px"):
        for kind in ARG_KINDS:
            ui.button(f"add {kind}", icon="add", on_click=lambda k=kind: add(k)).props("flat dense no-caps size=sm")


def _arg_card(owner: MutableMapping, items: list, i: int, arg: MutableMapping,
              ctx: SimpleNamespace, depth: int) -> None:
    kind = arg.get("kind", "argument")

    def set_kind(e: Any) -> None:
        if e.value == "argument":
            arg.pop("kind", None)
        else:
            arg["kind"] = e.value
            arg.setdefault("args", [])
        ctx.restructure()

    def move(delta: int) -> None:
        j = i + delta
        if 0 <= j < len(items):
            items[i], items[j] = items[j], items[i]
            ctx.restructure()

    def delete() -> None:
        del items[i]
        if not items:
            owner.pop("args", None)
        ctx.restructure()

    with ui.card().classes("w-full q-pa-sm").style(f"margin-left:{depth * 14}px"):
        with ui.row().classes("items-center w-full no-wrap"):
            ui.select(list(ARG_KINDS), value=kind if kind in ARG_KINDS else "argument", on_change=set_kind) \
                .props("dense options-dense borderless")
            ui.space()
            ui.button(icon="arrow_upward", on_click=lambda: move(-1)).props("flat dense size=sm")
            ui.button(icon="arrow_downward", on_click=lambda: move(1)).props("flat dense size=sm")
            ui.button(icon="delete", on_click=delete).props("flat dense size=sm color=negative")
        if kind == "argument":
            names_field(arg, ctx.touch)
            text_field(arg, "help", "help", ctx.touch)
            with ui.row().classes("w-full no-wrap"):
                choice_field(arg, "type", "type", BUILTIN_TYPES, ctx.touch).classes("col")
                choice_field(arg, "action", "action", ACTIONS, ctx.touch).classes("col")
            with ui.row().classes("w-full no-wrap items-center"):
                default_field(arg, ctx.touch).classes("col")
                switch_field(arg, "required", "required", ctx.touch)
            extra_field(arg, ARG_MANAGED, ctx.touch)
        else:
            extra_field(arg, GROUP_MANAGED, ctx.touch, f"{kind} options (YAML, e.g. title/description/required)")
            args_editor(arg, ctx, depth + 1)
