"""Small awaitable dialogs (``result = await ask_form(...)``)."""
from __future__ import annotations

from nicegui import ui


async def ask_form(title: str, fields: list[tuple[str, str, str]], ok: str = "OK") -> dict[str, str] | None:
    """fields: (key, label, default). Returns {key: value} or None if cancelled."""
    with ui.dialog() as dlg, ui.card().classes("min-w-[26rem]"):
        ui.label(title).classes("text-h6")
        inputs = {}
        for i, (key, label, default) in enumerate(fields):
            inp = ui.input(label, value=default).props("outlined dense" + (" autofocus" if i == 0 else ""))
            inp.classes("w-full")
            inp.on("keydown.enter", lambda: dlg.submit({k: w.value for k, w in inputs.items()}))
            inputs[key] = inp
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: dlg.submit(None)).props("flat")
            ui.button(ok, on_click=lambda: dlg.submit({k: w.value for k, w in inputs.items()}))
    result = await dlg
    dlg.delete()
    return result


async def ask_text(title: str, label: str, value: str = "") -> str | None:
    res = await ask_form(title, [("v", label, value)])
    text = (res or {}).get("v", "").strip()
    return text or None


async def confirm(title: str, text: str = "", ok: str = "OK") -> bool:
    with ui.dialog() as dlg, ui.card():
        ui.label(title).classes("text-h6")
        if text:
            ui.label(text)
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: dlg.submit(False)).props("flat")
            ui.button(ok, on_click=lambda: dlg.submit(True))
    result = await dlg
    dlg.delete()
    return bool(result)


async def choose(title: str, text: str, choices: dict[str, str]) -> str | None:
    """One button per choice (value -> label); returns the value or None."""
    with ui.dialog() as dlg, ui.card():
        ui.label(title).classes("text-h6")
        ui.label(text)
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: dlg.submit(None)).props("flat")
            for value, label in choices.items():
                ui.button(label, on_click=lambda v=value: dlg.submit(v))
    result = await dlg
    dlg.delete()
    return result


async def pick(title: str, options: dict[str, str], value: str | None = None) -> str | None:
    """Searchable select (value -> label). Returns the chosen value or None."""
    with ui.dialog() as dlg, ui.card().classes("min-w-[26rem]"):
        ui.label(title).classes("text-h6")
        sel = ui.select(options, value=value, with_input=True).props("outlined dense").classes("w-full")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: dlg.submit(None)).props("flat")
            ui.button("OK", on_click=lambda: dlg.submit(sel.value))
    result = await dlg
    dlg.delete()
    return result
