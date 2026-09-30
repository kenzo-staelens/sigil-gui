"""The application: one Studio instance owns the workspace, selection and draft.

Local single-user tool, so state is process-global rather than per-browser-session.
Every edit goes through a Draft; nothing reaches the workspace until Apply.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nicegui import ui

from ..core import preview
from ..core import scripts as sc
from ..core.draft import Draft, DraftError
from ..core.library import Library
from ..core.model import Fragment
from ..core.settings import Settings
from ..core.workspace import Workspace, WorkspaceError
from . import forms
from .browser import browse
from .dialogs import ask_form, ask_text, choose, confirm, pick

MONO = "font-mono text-xs whitespace-pre overflow-auto w-full"


class Studio:
    def __init__(self, project: str | None = None, library: str | None = None):
        self.settings = Settings.load()
        self.lib = Library(library or self.settings.library_dir)
        self.ws: Workspace | None = None        # what the editor shows: a project or a library fragment
        self.stash: Workspace | None = None     # the project kept in memory while a fragment is edited
        self.selected: str | None = None
        self.draft: Draft | None = None
        self.subtree_mode = False
        self.script_text: str | None = None      # edited script source (None = untouched)
        self.script_ref: str | None = None       # the script ref that text belongs to
        self.ctx = SimpleNamespace(touch=self.touch, restructure=self._restructure)
        if project:
            try:
                self._adopt(Workspace.open(Path(project).expanduser()))
            except WorkspaceError as e:
                print(f"cannot open {project}: {e}")

    # ------------------------------------------------------------------ page shell
    def build(self) -> None:
        with ui.header().classes("items-center gap-2 q-py-xs"):
            self.b_home = ui.button(icon="home", on_click=self.go_home).props("flat dense color=white").tooltip("Close and go to start page")
            ui.label("Sigil Studio").classes("text-h6")
            self.title = ui.label().classes("text-caption")
            ui.space()
            self.status = ui.label().classes("text-caption")
            self.b_undo = ui.button(icon="undo", on_click=self.undo).props("flat dense color=white").tooltip("Undo")
            self.b_redo = ui.button(icon="redo", on_click=self.redo).props("flat dense color=white").tooltip("Redo")
            self.b_save = ui.button(icon="save", on_click=self.save).props("flat dense color=white").tooltip("Write files to disk")
        self.main_view()

    @ui.refreshable
    def main_view(self) -> None:
        if self.ws is None:
            self._welcome()
            self._sync()
            return
        with ui.splitter(value=24).classes("w-full").style("height: calc(100vh - 80px)") as outer:
            with outer.before:
                self._left_pane()
            with outer.after:
                with ui.splitter(value=58).classes("w-full h-full") as inner:
                    with inner.before:
                        self.inspector_view()
                    with inner.after:
                        self._preview_pane()
        self.touch()

    def _welcome(self) -> None:
        with ui.row().classes("w-full q-pa-md gap-6 items-start no-wrap"):
            with ui.card().classes("col"):
                ui.label("Projects").classes("text-h6")
                with ui.row().classes("gap-2"):
                    ui.button("Open project…", icon="folder_open", on_click=self.open_dialog)
                    ui.button("New project…", icon="create_new_folder", on_click=self.new_dialog).props("outline")
                ui.label("Recent").classes("text-subtitle2 q-mt-md")
                recent = [r for r in self.settings.recent if Path(r).is_dir()]
                if not recent:
                    ui.label("nothing yet").classes("text-caption")
                for r in recent:
                    ui.button(r, icon="token", on_click=lambda r=r: self.open_project(r)).props("flat no-caps align=left dense").classes("w-full")
            with ui.card().classes("col"):
                ui.label("Library").classes("text-h6")
                self.library_view()

    async def _leave_ok(self, what: str = "leave") -> bool:
        """True when it is fine to replace the current view (no lost edits, or user agreed)."""
        if self.ws is None:
            return True
        if self.has_edits and not await confirm("Discard edits?", f"Unapplied edits will be lost if you {what}.", "Discard"):
            return False
        if self.ws.dirty and not await confirm("Unsaved changes", f"{self.ws.label} has unsaved changes. Discard them and {what}?", "Discard"):
            return False
        return True

    def _adopt(self, ws: Workspace) -> None:
        self.ws = ws
        self.subtree_mode = False
        tops = ws.top_level()
        self.selected = tops[0].key if tops else None
        self._new_draft()

    # ------------------------------------------------------------------ project / fragment lifecycle
    async def open_dialog(self) -> None:
        if not await self._leave_ok("open another project"):
            return
        start = self.settings.recent[0] if self.settings.recent else None
        path = await browse("Open sigil project", start, mode="project")
        if path:
            self.open_project(path)

    async def new_dialog(self) -> None:
        if not await self._leave_ok("start a new project"):
            return
        start = self.settings.recent[0] if self.settings.recent else None
        folder = await browse("New project: pick a parent folder and name the new project folder", start, mode="dir", new_folder=True)
        if not folder:
            return
        program = await ask_text("Program name", "name shown in --help", folder.name)
        if program:
            self.open_project(folder, create=program)

    def open_project(self, path: Path | str, create: str | None = None) -> None:
        try:
            p = Path(path).expanduser()
            ws = Workspace.create(p, create) if create else Workspace.open(p)
        except (WorkspaceError, OSError) as e:
            ui.notify(str(e), type="negative")
            return
        self.stash = None
        self._adopt(ws)
        self.settings.add_recent(ws.root)
        self.main_view.refresh()

    async def go_home(self) -> None:
        if self.ws is None:
            return
        if self.ws.kind == "fragment":
            await self.close_fragment()
        elif await self._leave_ok("close the project"):
            self.ws, self.draft, self.selected = None, None, None
            self.main_view.refresh()

    async def edit_fragment(self, name: str) -> None:
        if self.ws is not None and self.ws.kind == "fragment" and not await self._leave_ok("open another fragment"):
            return
        if self.has_edits and not await confirm("Discard edits?", "Unapplied edits will be lost.", "Discard"):
            return
        try:
            frag_ws = Workspace.open_fragment(self.lib.path(name))
        except (WorkspaceError, OSError, ValueError) as e:
            ui.notify(str(e), type="negative")
            return
        if self.ws is not None and self.ws.kind == "project":
            self.stash = self.ws
        self._adopt(frag_ws)
        self.main_view.refresh()

    async def close_fragment(self) -> None:
        if not await self._leave_ok("close the fragment"):
            return
        back = self.stash
        self.stash = None
        if back is not None:
            self._adopt(back)
        else:
            self.ws, self.draft, self.selected = None, None, None
        self.main_view.refresh()

    async def new_fragment(self) -> None:
        res = await ask_form("New library fragment", [("name", "fragment name", ""), ("key", "root command key", "")], "Create")
        if not res or not res["name"].strip() or not res["key"].strip():
            return
        key = res["key"].strip()
        try:
            self.lib.save(res["name"], Fragment(key, {key: {"name": key}}), "")
        except (FileExistsError, ValueError, OSError) as e:
            ui.notify(f"cannot create: {e}", type="negative")
            return
        self.library_view.refresh()
        await self.edit_fragment(res["name"].strip())

    async def change_library_dir(self) -> None:
        folder = await browse("Choose library folder", self.lib.dir, mode="dir")
        if folder:
            self.settings.library_dir = str(folder)
            self.settings.save()
            self.lib = Library(folder)
            self.library_view.refresh()

    def save(self) -> None:
        try:
            touched = self.ws.save()
        except OSError as e:
            ui.notify(f"save failed: {e}", type="negative")
            return
        ui.notify(f"wrote {len(touched)} file(s)" if touched else "nothing to write", type="positive")
        self.library_view.refresh()
        self._sync()

    def undo(self) -> None:
        if self.ws and self.ws.can_undo and self._draft_clean_or_warn():
            ui.notify(f"undid: {self.ws.undo()}")
            self._after_change(self.selected)

    def redo(self) -> None:
        if self.ws and self.ws.can_redo and self._draft_clean_or_warn():
            ui.notify(f"redid: {self.ws.redo()}")
            self._after_change(self.selected)

    def _draft_clean_or_warn(self) -> bool:
        if self.has_edits:
            ui.notify("apply or discard the current edit first", type="warning")
            return False
        return True

    # ------------------------------------------------------------------ left pane
    def _left_pane(self) -> None:
        is_project = self.ws.kind == "project"
        if not is_project:
            with ui.row().classes("items-center w-full q-pa-xs"):
                ui.icon("bookmarks")
                ui.label(f"library fragment: {self.ws.label}").classes("text-caption")
        with ui.tabs().classes("w-full") as tabs:
            t_tree = ui.tab("Tree")
            t_lib = ui.tab("Library") if is_project else None
        with ui.tab_panels(tabs, value=t_tree).classes("w-full"):
            with ui.tab_panel(t_tree).classes("q-pa-xs"):
                with ui.row().classes("gap-0"):
                    buttons = [("add", "Add child", self.add_child), ("content_copy", "Duplicate subtree", self.duplicate),
                               ("drive_file_move", "Move to another parent", self.move), ("delete", "Delete", self.delete)]
                    if is_project:
                        buttons.append(("bookmark_add", "Save subtree to library", self.save_to_library))
                    for icon, tip, fn in buttons:
                        ui.button(icon=icon, on_click=fn).props("flat dense").tooltip(tip)
                self.tree = ui.tree(self._tree_nodes(), node_key="id", label_key="label",
                                    on_select=lambda e: self._select(e.value)).classes("w-full")
                self.tree.expand()
                if self.selected:
                    self.tree.select(self.selected)
            if t_lib is not None:
                with ui.tab_panel(t_lib).classes("q-pa-xs"):
                    self.library_view()

    def _tree_nodes(self) -> list[dict[str, Any]]:
        ws = self.ws

        def label(n: Any) -> str:
            text = n.key if n.name == n.key else f"{n.name}  ·  {n.key}"
            if not n.loads:
                text += "  (load: false)"
            if n.parent is not None and n.parent not in ws.index:
                text = f"⚠ {text}"
            return text

        def build(key: str, seen: frozenset[str]) -> dict[str, Any]:
            kids = [c.key for c in ws.children(key) if c.key not in seen]
            out: dict[str, Any] = {"id": key, "label": label(ws.index[key])}
            if kids:
                out["children"] = [build(k, seen | {key}) for k in kids]
            return out

        return [build(n.key, frozenset()) for n in ws.top_level()]

    async def _select(self, key: str | None) -> None:
        if key is None or key == self.selected or key not in self.ws.index:
            if self.selected and getattr(self, "tree", None):
                self.tree.select(self.selected)     # ignore "deselect" clicks
            return
        if self.has_edits and not await confirm("Discard edits?", f"Unapplied changes to {self.draft.origin} will be lost.", "Discard"):
            self.tree.select(self.selected)
            return
        self.selected = key
        self.tree.select(key)
        self._new_draft()
        self.inspector_view.refresh()
        self.touch()

    def _after_change(self, select: str | None) -> None:
        """Refresh everything after the workspace changed."""
        self.selected = select if select in self.ws.index else (self.ws.top_level()[0].key if self.ws.index else None)
        self._new_draft()
        self.tree.props["nodes"] = self._tree_nodes()
        self.tree.update()
        self.tree.expand()
        if self.selected:
            self.tree.select(self.selected)
        self.inspector_view.refresh()
        self.touch()

    async def _mutate(self, fn: Any, select: str | None = None) -> None:
        if self.has_edits and not await confirm("Discard edits?", "Unapplied edits will be lost.", "Discard"):
            return
        try:
            result = fn()
        except (WorkspaceError, DraftError) as e:
            ui.notify(str(e), type="negative")
            return
        self._after_change(result if isinstance(result, str) else select or self.selected)

    async def add_child(self) -> None:
        key = await ask_text("Add child" if self.selected else "Add root", "key (unique id; file will be <key>.yml)")
        if key:
            await self._mutate(lambda: self.ws.add_node(key, {"name": key.split("_")[-1]}, self.selected).key)

    async def duplicate(self) -> None:
        if self.selected:
            await self._mutate(lambda: self.ws.duplicate(self.selected))

    async def move(self) -> None:
        if not self.selected:
            return
        banned = set(self.ws.subtree_keys(self.selected))
        options = {"": "(no parent — top level)", **{k: k for k in self.ws.index if k not in banned}}
        target = await pick(f"Move {self.selected} under…", options, self.ws.get(self.selected).parent or "")
        if target is not None:
            sel = self.selected
            await self._mutate(lambda: self.ws.move(sel, target or None), sel)

    async def delete(self) -> None:
        if not self.selected:
            return
        sel, n = self.selected, len(self.ws.subtree_keys(self.selected))
        parent = self.ws.get(sel).parent
        choices = {"all": f"Delete with {n - 1} descendant(s)" if n > 1 else "Delete"}
        if n > 1:
            choices["keep"] = "Delete, keep children"
        how = await choose(f"Delete {sel}?", "This can be undone until you close the app.", choices)
        if how:
            await self._mutate(lambda: self.ws.remove(sel, reparent_children=(how == "keep")), parent)

    async def save_to_library(self) -> None:
        if not self.selected:
            return
        res = await ask_form("Save subtree to library", [("name", "name", self.selected), ("desc", "description", "")], "Save")
        if not res or not res["name"].strip():
            return
        try:
            frag = self.ws.extract(self.selected)
            try:
                self.lib.save(res["name"], frag, res["desc"])
            except FileExistsError:
                if not await confirm("Overwrite?", f"{res['name']} already exists in the library.", "Overwrite"):
                    return
                self.lib.save(res["name"], frag, res["desc"], overwrite=True)
        except (ValueError, OSError) as e:
            ui.notify(str(e), type="negative")
            return
        self.library_view.refresh()
        ui.notify("saved to library", type="positive")

    # ------------------------------------------------------------------ library
    @ui.refreshable
    def library_view(self) -> None:
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.label(str(self.lib.dir)).classes("text-caption ellipsis")
            ui.button(icon="folder_open", on_click=self.change_library_dir).props("flat dense size=sm").tooltip("Change library folder")
            ui.button(icon="add", on_click=self.new_fragment).props("flat dense size=sm").tooltip("New fragment")
        entries = self.lib.list()
        if not entries:
            ui.label("Empty. Create a fragment, or select a node and use “Save subtree to library”.").classes("text-caption")
        can_insert = self.ws is not None and self.ws.kind == "project"
        for e in entries:
            with ui.card().classes("w-full q-pa-sm"):
                ui.label(e.name).classes("text-bold")
                ui.label(f"{e.size} node(s)" + (f", {e.scripts} script(s)" if e.scripts else "") + (f" · {e.description}" if e.description else "")).classes("text-caption")
                with ui.row().classes("gap-1"):
                    ui.button("edit", icon="edit", on_click=lambda n=e.name: self.edit_fragment(n)).props("flat dense no-caps size=sm")
                    if can_insert:
                        ui.button("insert under selection", icon="input", on_click=lambda n=e.name: self.insert_from_library(n)) \
                            .props("flat dense no-caps size=sm")
                    ui.button(icon="delete", on_click=lambda n=e.name: self.delete_from_library(n)).props("flat dense size=sm color=negative")

    async def insert_from_library(self, name: str) -> None:
        try:
            frag = self.lib.load(name)
        except (ValueError, OSError) as e:
            ui.notify(str(e), type="negative")
            return
        parent = self.selected
        policy = "keep"
        conflicts = self.ws.script_conflicts(frag, parent)
        if conflicts:
            policy = await choose("Script files already exist", "Different content already exists for: " + ", ".join(conflicts),
                                  {"keep": "Keep existing", "overwrite": "Overwrite"})
            if policy is None:
                return
        report: list[str] = []
        await self._mutate(lambda: self.ws.insert(frag, parent, scripts=policy, report=report)[frag.root], parent)
        if report:
            ui.notify("\n".join(report), type="warning", multi_line=True)

    async def delete_from_library(self, name: str) -> None:
        if await confirm("Delete from library?", name, "Delete"):
            self.lib.delete(name)
            self.library_view.refresh()

    # ------------------------------------------------------------------ inspector
    @property
    def has_edits(self) -> bool:
        return bool(self.draft and self.draft.dirty) or self._script_dirty()

    def _script_dirty(self) -> bool:
        return self.script_text is not None and self.draft is not None and \
            self.script_text != (self.ws.read_script(self._preview_nodes(), self.draft.root) or "")

    def _new_draft(self) -> None:
        self.script_text = self.script_ref = None
        self.draft = self.ws.begin_edit(self.selected, self.subtree_mode) if self.ws and self.selected in (self.ws.index or {}) else None

    @ui.refreshable
    def inspector_view(self) -> None:
        d = self.draft
        if d is None:
            ui.label("Select a node in the tree.").classes("q-pa-md")
            return
        with ui.column().classes("w-full q-pa-sm gap-1"):
            with ui.row().classes("items-center w-full no-wrap"):
                ui.label(d.origin).classes("text-subtitle1 text-bold")
                node = self.ws.index.get(d.origin)
                if node is not None:
                    ui.label(node.file).classes("text-caption")
                ui.space()
                ui.switch("edit whole subtree", value=self.subtree_mode, on_change=self._toggle_scope)
            with ui.tabs(on_change=self._tab_changed).classes("w-full") as tabs:
                ui.tab("form", label="Form")
                ui.tab("yaml", label="YAML")
                ui.tab("script", label="Script", icon="code")
            with ui.tab_panels(tabs, value="form").classes("w-full"):
                with ui.tab_panel("form").classes("q-pa-xs"):
                    self._form(d)
                with ui.tab_panel("yaml").classes("q-pa-xs"):
                    self.yaml_panel()
                with ui.tab_panel("script").classes("q-pa-xs"):
                    self.script_panel()
            with ui.row().classes("gap-2"):
                self.b_apply = ui.button("Apply", icon="check", on_click=self.apply)
                self.b_discard = ui.button("Discard", icon="close", on_click=self.discard).props("flat")
        self._sync()

    def _form(self, d: Draft) -> None:
        data = d.data
        key = ui.input("key (id used by children's `parent`)", value=d.root).props("outlined dense").classes("w-full")

        def rename() -> None:
            try:
                d.rename_root(key.value.strip())
            except DraftError as e:
                ui.notify(str(e), type="negative")
                key.value = d.root
                return
            self.touch()

        key.on("blur", rename)
        key.on("keydown.enter", rename)
        forms.text_field(data, "name", "name", self.touch)
        forms.text_field(data, "help", "help", self.touch)
        forms.text_field(data, "script", "script (module in script_dir)", self.touch)
        if data.get("parent") is None and self.ws.kind == "project":
            forms.text_field(data, "script_dir", "script_dir", self.touch)
        else:
            ui.label(f"parent: {data.get('parent')}   (use “Move” in the tree to change)").classes("text-caption")
        with ui.row().classes("gap-4"):
            forms.switch_field(data, "default", "default subcommand", self.touch)
            forms.switch_field(data, "known_args", "known_args (tolerate unknown flags)", self.touch)
            forms.switch_field(data, "load", "load", self.touch, default=True)
        ui.label("Arguments").classes("text-subtitle2 q-mt-sm")
        self.args_section()
        forms.extra_field(data, forms.COMMAND_MANAGED, self.touch, "other parser options (YAML)")

    @ui.refreshable
    def args_section(self) -> None:
        if self.draft:
            forms.args_editor(self.draft.data, self.ctx)

    def _restructure(self) -> None:
        self.args_section.refresh()
        self.touch()

    def _tab_changed(self, e: Any) -> None:
        """Panels that mirror the draft are rebuilt when opened, so they never show stale content."""
        if e.value == "yaml":
            self.yaml_panel.refresh()
        elif e.value == "script":
            self.script_panel.refresh()

    @ui.refreshable
    def yaml_panel(self) -> None:
        d = self.draft
        if d is None:
            return
        ui.label("Raw YAML of this draft — " + ("all nodes in the subtree." if d.subtree else "this node (add child nodes here too).")).classes("text-caption")
        editor = ui.codemirror(d.to_text(), language="YAML").classes("w-full").style("min-height: 22rem")

        def load() -> None:
            try:
                d.replace_from_text(editor.value or "")
            except DraftError as e:
                ui.notify(str(e), type="negative")
                return
            self.inspector_view.refresh()
            self.touch()

        ui.button("Update draft from YAML", on_click=load).props("flat no-caps")

    @ui.refreshable
    def script_panel(self) -> None:
        d = self.draft
        if d is None:
            return
        nodes, key = self._preview_nodes(), d.root
        label, state = self.ws.script_info(nodes, key)
        ref = sc.script_ref(nodes[key])
        if self.script_ref != ref:                     # the `script` field changed: drop text of the old one
            self.script_text, self.script_ref = None, ref
        if state == "unset":
            ui.label("This command has no `script`. Set one in the Form tab.").classes("text-caption")
            return
        if state == "unsupported":
            ui.label(f"{label}: absolute script paths are not embedded in library fragments.").classes("text-caption")
            return
        if state == "no-dir":
            ui.label(f"Cannot locate “{label}”: the root command has no `script_dir`.").classes("text-caption text-negative")
            return
        stored = self.ws.read_script(nodes, key)
        text = self.script_text if self.script_text is not None else stored
        ui.label(label).classes("text-caption")
        if text is None:
            ui.label("This script file does not exist yet.").classes("text-caption")

            def create() -> None:
                self.script_text = sc.TEMPLATE
                self.script_panel.refresh()
                self.touch()

            ui.button("Create from template", icon="note_add", on_click=create).props("flat no-caps")
            return
        problem = ui.label(sc.check_source(text) or "✓ defines run(args, ctx)").classes("text-caption")

        def changed(e: Any) -> None:
            self.script_text = e.value
            problem.set_text(sc.check_source(e.value or "") or "✓ defines run(args, ctx)")
            self.touch()

        ui.codemirror(text, language="Python", on_change=changed).classes("w-full").style("min-height: 24rem")

    async def _toggle_scope(self, e: Any) -> None:
        if self.has_edits and not await confirm("Discard edits?", "Switching scope restarts the draft.", "Discard"):
            self.inspector_view.refresh()
            return
        self.subtree_mode = bool(e.value)
        self._new_draft()
        self.inspector_view.refresh()
        self.touch()

    def apply(self) -> None:
        d = self.draft
        if not d:
            return
        script = self.script_text if self._script_dirty() else None
        try:
            if d.dirty:
                self.ws.commit(d)
            if script is not None:
                self.ws.write_script(self.ws.raw, d.root, script)   # after commit: `script` may have changed
        except (WorkspaceError, DraftError) as e:
            ui.notify(f"not applied: {e}", type="negative")
            return
        ui.notify("applied" + (" (script is written on Save)" if script is not None and self.ws.kind == "project" else ""),
                  type="positive")
        if script is not None and (problem := sc.check_source(script)):
            ui.notify(f"script: {problem}", type="warning")
        self._after_change(d.root)

    def discard(self) -> None:
        self._new_draft()
        self.inspector_view.refresh()
        self.touch()

    # ------------------------------------------------------------------ preview / status
    def _preview_pane(self) -> None:
        with ui.tabs().classes("w-full") as tabs:
            ui.tab("Help")
            ui.tab("YAML")
            ui.tab("Issues")
        with ui.tab_panels(tabs, value="Help").classes("w-full"):
            with ui.tab_panel("Help"):
                self.help_label = ui.label().classes(MONO)
            with ui.tab_panel("YAML"):
                self.yaml_label = ui.label().classes(MONO)
            with ui.tab_panel("Issues"):
                ui.button("run `sigil validate` on saved files", on_click=self.run_sigil_validate).props("flat dense no-caps size=sm")
                self.issues_box = ui.column().classes("w-full gap-0")

    def _preview_nodes(self) -> dict[str, Any]:
        """Workspace nodes with the current draft substituted in (so preview shows unapplied edits)."""
        d = self.draft
        nodes = {k: v for k, v in self.ws.raw.items()}
        for k in (self.ws.subtree_keys(d.origin) if d.subtree else [d.origin]):
            nodes.pop(k, None)
        if d.root != d.origin:
            nodes = {k: ({**v, "parent": d.root} if v.get("parent") == d.origin else v) for k, v in nodes.items()}
        nodes.update(d.fragment.nodes)
        return nodes

    def touch(self) -> None:
        """Cheap refresh after any edit: buttons, previews, issue list. Never rebuilds inputs."""
        self._sync()
        if not (self.ws and getattr(self, "help_label", None)):
            return
        if self.draft:
            self.help_label.set_text(preview.render_help(self._preview_nodes(), self.draft.root))
            self.yaml_label.set_text(self.draft.to_text())
        self._render_issues()

    def _sync(self) -> None:
        ws = self.ws
        if getattr(self, "title", None) is None:
            return
        self.title.set_text((("Library ▸ " if ws.kind == "fragment" else "") + ws.label) if ws else "")
        self.status.set_text(("● unsaved  " if ws and ws.dirty else "") + ("● unapplied edits" if self.has_edits else ""))
        for btn, on in [(self.b_undo, bool(ws and ws.can_undo)), (self.b_redo, bool(ws and ws.can_redo)), (self.b_save, bool(ws and ws.dirty))]:
            btn.set_enabled(on)
        if getattr(self, "b_apply", None) is not None:
            try:
                self.b_apply.set_enabled(self.has_edits)
                self.b_discard.set_enabled(self.has_edits)
            except Exception:   # element belongs to a refreshed-away inspector
                pass

    def _render_issues(self) -> None:
        self.issues_box.clear()
        with self.issues_box:
            issues = self.ws.issues()
            if not issues:
                ui.label("✓ no structural issues").classes("text-positive")
            for i in issues:
                ui.button(f"{i.key or '-'}: {i.message}", icon="error" if i.severity == "error" else "warning",
                          on_click=lambda k=i.key: self._select(k)).props("flat dense no-caps align=left").classes("w-full")

    def run_sigil_validate(self) -> None:
        exe = shutil.which("sigil")
        if not exe:
            ui.notify("`sigil` executable not found on PATH", type="warning")
            return
        r = subprocess.run([exe, "validate", str(self.ws.root)], capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        ui.notify(out or "sigil validate: no output (valid)", type="negative" if r.returncode else "positive", multi_line=True)
