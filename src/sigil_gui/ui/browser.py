"""Server-side file browser dialog (the app is local, so its filesystem is yours)."""
from __future__ import annotations

import os
from pathlib import Path

from nicegui import ui

MANIFEST = "manifest.yml"


def _start_dir(start: str | Path | None) -> Path:
    p = Path(start).expanduser() if start else Path.home()
    while not p.is_dir() and p != p.parent:
        p = p.parent
    return p if p.is_dir() else Path.home()


async def browse(title: str, start: str | Path | None = None, *, mode: str = "dir",
                 new_folder: bool = False) -> Path | None:
    """Pick a path.

    mode="dir"     -> a folder. With new_folder=True a name field is shown and the result is
                      <current folder>/<name> (not created here).
    mode="project" -> a folder containing manifest.yml, or a .yml manifest file.
    Returns None when cancelled.
    """
    cur = {"p": _start_dir(start).resolve()}
    show_hidden = {"v": False}

    with ui.dialog() as dlg, ui.card().classes("w-[42rem] max-w-full"):
        ui.label(title).classes("text-h6")
        path_in = ui.input("location", value=str(cur["p"])).props("outlined dense").classes("w-full")
        with ui.row().classes("gap-1 items-center"):
            ui.button(icon="arrow_upward", on_click=lambda: go(cur["p"].parent)).props("flat dense").tooltip("Parent folder")
            ui.button(icon="home", on_click=lambda: go(Path.home())).props("flat dense").tooltip("Home")
            ui.switch("show hidden", on_change=lambda e: (show_hidden.update(v=bool(e.value)), listing.refresh()))
        with ui.scroll_area().classes("w-full h-72 border rounded"):
            @ui.refreshable
            def listing() -> None:
                p = cur["p"]
                try:
                    entries = sorted(os.scandir(p), key=lambda e: (not _is_dir(e), e.name.lower()))
                except OSError as ex:
                    ui.label(f"cannot read folder: {ex}").classes("text-negative q-pa-sm")
                    return
                shown = 0
                for e in entries:
                    if e.name.startswith(".") and not show_hidden["v"]:
                        continue
                    if _is_dir(e):
                        try:
                           icon = "token" if mode == "project" and (Path(e.path) / MANIFEST).is_file() else "folder"
                        except PermissionError:
                            continue
                    elif e.name in {'manifest.yml', 'manifest.yaml'}:
                        icon = "token"
                    elif mode == "project" and e.name.endswith((".yml", ".yaml")):
                        icon = "description"
                    else:
                        continue
                    shown += 1
                    ui.button(e.name, icon=icon, on_click=lambda e=e: entry_clicked(e)) \
                        .props("flat no-caps align=left dense").classes("w-full")
                if not shown:
                    ui.label("(nothing to show here)").classes("text-caption q-pa-sm")

            listing()
        name_in = None
        if new_folder:
            name_in = ui.input("new folder name").props("outlined dense").classes("w-full")
        status = ui.label().classes("text-caption")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: dlg.submit(None)).props("flat")
            ui.button("Select" if mode == "dir" else "Open", on_click=lambda: choose())

    def refresh_status() -> None:
        p = cur["p"]
        if mode == "project":
            status.set_text("✓ sigil project here" if (p / MANIFEST).is_file() else f"no {MANIFEST} in this folder")
        elif name_in is not None:
            status.set_text(f"will create: {p / (name_in.value or '<name>')}")
        else:
            status.set_text(str(p))

    def go(p: Path) -> None:
        p = Path(p)
        if not p.is_dir():
            ui.notify(f"not a folder: {p}", type="warning")
            path_in.set_value(str(cur["p"]))
            return
        cur["p"] = p.resolve()
        path_in.set_value(str(cur["p"]))
        listing.refresh()
        refresh_status()

    def entry_clicked(e: os.DirEntry) -> None:
        if _is_dir(e):
            go(Path(e.path))
        else:                                   # a manifest file in project mode
            dlg.submit(Path(e.path))

    def choose() -> None:
        p = cur["p"]
        if mode == "project":
            if (p / MANIFEST).is_file():
                dlg.submit(p)
            else:
                ui.notify(f"no {MANIFEST} in {p}", type="warning")
            return
        if name_in is not None:
            name = (name_in.value or "").strip()
            if not name or any(c in name for c in "/\\") or name in {".", ".."}:
                ui.notify("enter a plain folder name", type="warning")
                return
            dlg.submit(p / name)
        else:
            dlg.submit(p)

    path_in.on("keydown.enter", lambda: go(Path(path_in.value).expanduser()))
    if name_in is not None:
        name_in.on("update:model-value", lambda: refresh_status())
    refresh_status()
    result = await dlg
    dlg.delete()
    return result


def _is_dir(e: os.DirEntry) -> bool:
    try:
        return e.is_dir()
    except OSError:
        return False
