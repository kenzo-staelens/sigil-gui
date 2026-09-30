"""In-memory model of a sigil project (or a single library fragment).

A *project* is a manifest plus the YAML files it lists. Manifest entries are
paths or globs relative to the manifest itself (e.g. ``yml/*.yml``), so a
project can live anywhere. Files are keyed by that relative path.

All mutations go through ``transaction``: state is snapshotted, the change is
applied, and it is rolled back if it raises or introduces a *new* fatal
structural issue (dangling parent, cycle, duplicate key). Snapshots double as
the undo history. Configs are small, so whole-state snapshots are fine.

A *fragment* (kind="fragment") is one library file edited with the very same
machinery, which is how the library can be managed without opening a project.
"""
from __future__ import annotations

import contextlib
import copy
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from . import manifest as mf
from . import scripts as sc
from . import tree, yamlio
from .draft import Draft
from .model import Fragment, Node
from .validate import Issue, validate


class WorkspaceError(Exception):
    pass


def _rekey(doc: dict, old: str, new: str) -> None:
    """Rename a mapping key, keeping its position."""
    items = [(new if k == old else k, v) for k, v in doc.items()]
    doc.clear()
    doc.update(items)


def _safe_filename(key: str) -> str:
    return re.sub(r"[^\w.-]+", "_", key).strip("._") or "node"


class Workspace:
    def __init__(self, root: Path | str, manifest: str = "manifest.yml", kind: str = "project"):
        self.root = Path(root)
        self.manifest_name = manifest
        self.kind = kind
        self.manifest: list[str] = []
        self.docs: dict[str, dict] = {}
        self.index: dict[str, Node] = {}
        self.duplicates: list[tuple[str, str, str]] = []
        self.missing_files: set[str] = set()
        self.empty_patterns: list[str] = []
        self.dirty = False
        self._disk: dict[str, Any] = {}          # file -> data as last read/written
        self._manifest_disk: list[str] = []
        self._undo: list[tuple[str, Any]] = []
        self._redo: list[tuple[str, Any]] = []
        self._depth = 0
        self._scripts: dict[Path, str] = {}       # project: script edits not yet written to disk

    # -- construction ---------------------------------------------------------------
    @classmethod
    def open(cls, path: Path | str) -> "Workspace":
        """`path` is a project folder (containing manifest.yml) or a manifest file."""
        p = Path(path).expanduser()
        root, name = (p, "manifest.yml") if p.is_dir() else (p.parent, p.name)
        ws = cls(root, name)
        ws._load()
        return ws

    @classmethod
    def open_fragment(cls, path: Path | str) -> "Workspace":
        p = Path(path).expanduser()
        ws = cls(p.parent, p.name, kind="fragment")
        doc = yamlio.read(p)
        if not isinstance(doc, Mapping):
            raise WorkspaceError(f"{p.name}: not a fragment file")
        ws.docs[p.name] = doc if isinstance(doc, dict) else dict(doc)
        ws._disk[p.name] = copy.deepcopy(ws.docs[p.name])
        ws._reindex()
        return ws

    @classmethod
    def create(cls, root: Path | str, program: str = "mycli") -> "Workspace":
        """Write a minimal project into `root` (an absolute path; never the cwd by accident)."""
        root = Path(root).expanduser()
        if not root.is_absolute():
            raise WorkspaceError("project folder must be an absolute path")
        if root.exists() and not root.is_dir():
            raise WorkspaceError(f"{root} exists and is not a folder")
        if (root / "manifest.yml").exists():
            raise WorkspaceError(f"{root} already contains a sigil project")
        (root / "yml").mkdir(parents=True, exist_ok=True)
        (root / "scripts").mkdir(exist_ok=True)
        yamlio.write(root / "yml" / "root.yml", {"root": {"name": program, "script_dir": "scripts"}})
        yamlio.write(root / "manifest.yml", ["yml/*.yml"])
        return cls.open(root)

    def _load(self) -> None:
        mpath = self.root / self.manifest_name
        if not mpath.is_file():
            raise WorkspaceError(f"no {self.manifest_name} in {self.root}")
        try:
            loaded = yamlio.read(mpath)
        except Exception as e:
            raise WorkspaceError(f"{self.manifest_name}: {e}") from e
        if loaded is None:
            loaded = []
        if not isinstance(loaded, list):
            raise WorkspaceError(f"{self.manifest_name} must be a list of file paths or globs")
        self.manifest = [e for e in loaded if isinstance(e, str)]
        self._manifest_disk = list(self.manifest)
        for entry in self.manifest:
            if mf.has_magic(entry):
                hits = mf.expand(self.root, entry)
                if not hits:
                    self.empty_patterns.append(entry)
                for rel in hits:
                    self._read_doc(rel)
            else:
                rel = mf.norm(entry)
                if (self.root / rel).is_file():
                    self._read_doc(rel)
                else:
                    self.docs.setdefault(rel, {})
                    self.missing_files.add(rel)
        self._reindex()

    def _read_doc(self, rel: str) -> None:
        if rel in self.docs:
            return
        try:
            doc = yamlio.read(self.root / rel)
        except Exception as e:
            raise WorkspaceError(f"{rel}: {e}") from e
        if doc is None:
            doc = {}
        if not isinstance(doc, dict):
            raise WorkspaceError(f"{rel}: top level must be a mapping")
        self.docs[rel] = doc
        self._disk[rel] = copy.deepcopy(doc)

    def _reindex(self) -> None:
        idx: dict[str, Node] = {}
        dupes: list[tuple[str, str, str]] = []
        for f, doc in self.docs.items():
            for k, d in doc.items():
                if not isinstance(k, str) or not isinstance(d, dict):
                    continue
                if self.kind == "fragment" and k == Fragment.META_KEY:
                    continue
                if k in idx:
                    dupes.append((k, idx[k].file, f))
                else:
                    idx[k] = Node(k, d, f)
        self.index, self.duplicates = idx, dupes

    # -- manifest coverage ------------------------------------------------------------
    def _covered(self, rel: str) -> bool:
        return any((mf.glob_match(rel, e) if mf.has_magic(e) else mf.norm(e) == rel) for e in self.manifest)

    def _new_dir(self) -> str:
        """Where new files go: the static folder of the first glob entry (e.g. 'yml'), else the root."""
        for e in self.manifest:
            if mf.has_magic(e):
                return mf.static_dir(e)
        return ""

    def _default_file(self, key: str) -> str:
        if self.kind == "fragment":
            return next(iter(self.docs))
        name = f"{_safe_filename(key)}.yml"
        d = self._new_dir()
        return f"{d}/{name}" if d else name

    # -- queries --------------------------------------------------------------------
    @property
    def raw(self) -> dict[str, Any]:
        return {k: n.data for k, n in self.index.items()}

    @property
    def label(self) -> str:
        return Path(self.manifest_name).stem if self.kind == "fragment" else str(self.root)

    def get(self, key: str) -> Node:
        try:
            return self.index[key]
        except KeyError:
            raise WorkspaceError(f"no such node: {key!r}") from None

    def children(self, key: str | None) -> list[Node]:
        return [self.index[k] for k in tree.child_map(self.raw).get(key, [])]

    def top_level(self) -> list[Node]:
        """Nodes with no parent, or whose parent is missing (shown so they can be fixed)."""
        return [n for n in self.index.values() if n.parent is None or n.parent not in self.index]

    def subtree_keys(self, key: str) -> list[str]:
        return tree.subtree_keys(self.raw, key)

    def issues(self) -> list[Issue]:
        return validate(self)

    # -- transactions / history -----------------------------------------------------
    def _snapshot(self) -> tuple[Any, Any, Any]:
        return copy.deepcopy(self.manifest), copy.deepcopy(self.docs), dict(self._scripts)

    def _restore(self, snap: tuple[Any, Any, Any]) -> None:
        self.manifest, self.docs, self._scripts = snap
        self._reindex()

    def _fatal(self) -> set[tuple[str, str | None, str]]:
        return {(i.code, i.key, i.message) for i in validate(self) if i.fatal}

    @contextlib.contextmanager
    def transaction(self, label: str) -> Iterator[None]:
        if self._depth:            # nested: the outermost transaction owns rollback
            yield
            return
        before = self._snapshot()
        fatal_before = self._fatal()
        self._depth = 1
        try:
            yield
            self._reindex()
            new = self._fatal() - fatal_before
            if new:
                raise WorkspaceError("; ".join(sorted(m for _, _, m in new)))
        except BaseException:
            self._restore(before)
            raise
        finally:
            self._depth = 0
        self._undo.append((label, before))
        self._redo.clear()
        self.dirty = True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> str | None:
        if not self._undo:
            return None
        label, snap = self._undo.pop()
        self._redo.append((label, self._snapshot()))
        self._restore(snap)
        self.dirty = True
        return label

    def redo(self) -> str | None:
        if not self._redo:
            return None
        label, snap = self._redo.pop()
        self._undo.append((label, self._snapshot()))
        self._restore(snap)
        self.dirty = True
        return label

    # -- low-level helpers (call inside a transaction) -------------------------------
    def _require_new_key(self, key: str) -> None:
        if not isinstance(key, str) or not key or key != key.strip():
            raise WorkspaceError(f"invalid key {key!r}")
        if key in self.index or (self.kind == "fragment" and key == Fragment.META_KEY):
            raise WorkspaceError(f"key {key!r} already exists")

    def _put(self, key: str, data: Any, file: str | None = None) -> None:
        self._require_new_key(key)
        file = file or self._default_file(key)
        self.docs.setdefault(file, {})[key] = data
        self._reindex()

    def _delete_key(self, key: str) -> None:
        node = self.get(key)
        doc = self.docs[node.file]
        del doc[key]
        if len(doc) == 0 and self.kind == "project":
            del self.docs[node.file]
            self.manifest[:] = [e for e in self.manifest if mf.has_magic(e) or mf.norm(e) != node.file]
        self._reindex()

    # -- tree operations ------------------------------------------------------------
    def add_node(self, key: str, data: dict | None = None, parent: str | None = None,
                 file: str | None = None) -> Node:
        with self.transaction(f"add {key}"):
            data = dict(data) if data is not None else {"name": key}
            if parent is not None:
                if parent not in self.index:
                    raise WorkspaceError(f"no such parent: {parent!r}")
                data["parent"] = parent
            self._put(key, data, file)
        return self.get(key)

    def remove(self, key: str, reparent_children: bool = False) -> None:
        with self.transaction(f"remove {key}"):
            node = self.get(key)
            if reparent_children:
                for child in self.children(key):
                    if node.parent is None:
                        child.data.pop("parent", None)
                    else:
                        child.data["parent"] = node.parent
                self._delete_key(key)
            else:
                for k in reversed(self.subtree_keys(key)):
                    self._delete_key(k)

    def rename(self, old: str, new: str) -> None:
        with self.transaction(f"rename {old} -> {new}"):
            node = self.get(old)
            self._require_new_key(new)
            _rekey(self.docs[node.file], old, new)
            for n in self.index.values():
                if n.parent == old:
                    n.data["parent"] = new
            self._reindex()

    def move(self, key: str, new_parent: str | None) -> None:
        with self.transaction(f"move {key}"):
            node = self.get(key)
            if new_parent is None:
                node.data.pop("parent", None)
            else:
                self.get(new_parent)
                node.data["parent"] = new_parent

    def duplicate(self, key: str) -> str:
        frag = self.extract(key)
        with self.transaction(f"duplicate {key}"):
            new_keys = self.insert(frag, self.get(key).parent, suffix="_copy")
            root = self.get(new_keys[key])
            if root.data.get("name") is not None:
                root.data["name"] = f"{root.data['name']}-copy"
        return new_keys[key]

    # -- fragments ------------------------------------------------------------------
    def extract(self, key: str, subtree: bool = True, with_scripts: bool = True) -> Fragment:
        keys = self.subtree_keys(key) if subtree else [key]
        self.get(key)
        frag = Fragment(key, {k: copy.deepcopy(self.index[k].data) for k in keys})
        if with_scripts:
            for k in keys:
                ref = sc.script_ref(frag.nodes[k])
                if ref and not sc.is_absolute(ref) and ref not in frag.scripts:
                    text = self.read_script(self.raw, k)
                    if text is not None:
                        frag.scripts[ref] = text
        return frag

    def insert(self, frag: Fragment, parent: str | None, prefix: str = "", suffix: str = "",
               scripts: str = "keep", report: list[str] | None = None) -> dict[str, str]:
        """Add a fragment under `parent`. Colliding keys get a numeric suffix.

        Embedded scripts are installed under the target's ``script_dir``. ``scripts="keep"`` never
        replaces an existing, different file; ``"overwrite"`` does. Notes go to `report`.
        Returns the old-key -> new-key mapping.
        """
        with self.transaction(f"insert {frag.root}"):
            if parent is not None:
                self.get(parent)
            mapping: dict[str, str] = {}
            for k in frag.nodes:
                base = new = f"{prefix}{k}{suffix}"
                i = 2
                while new in self.index or new in mapping.values() or new == Fragment.META_KEY:
                    new = f"{base}_{i}"
                    i += 1
                mapping[k] = new
            for k, d in frag.nodes.items():
                d = copy.deepcopy(d)
                if k == frag.root:
                    if parent is None:
                        d.pop("parent", None)
                    else:
                        d["parent"] = parent
                elif d.get("parent") in mapping:
                    d["parent"] = mapping[d["parent"]]
                self._put(mapping[k], d)
            used = {sc.script_ref(d) for d in frag.nodes.values()}
            for ref, text in frag.scripts.items():
                if ref in used:
                    self._install_script(ref, text, parent, scripts == "overwrite", report)
        return mapping

    # -- scripts --------------------------------------------------------------------
    def _frag_scripts(self) -> dict[str, str]:
        doc = next(iter(self.docs.values()))
        meta = doc.setdefault(Fragment.META_KEY, {"load": False})
        scripts = meta.get("scripts")
        if not isinstance(scripts, dict):
            scripts = meta["scripts"] = {}
        return scripts

    def _current_text(self, path: Path) -> str | None:
        if path in self._scripts:
            return self._scripts[path]
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def _script_target(self, ref: str, parent: str | None) -> Path | None:
        if parent is None or sc.is_absolute(ref):
            return None
        base = sc.script_dir_for(self.root, self.raw, parent)
        return sc.module_path(base, ref) if base else None

    def _install_script(self, ref: str, text: str, parent: str | None, overwrite: bool,
                        report: list[str] | None) -> None:
        note = report.append if report is not None else (lambda _m: None)
        if self.kind == "fragment":
            store = self._frag_scripts()
            if ref in store and store[ref] != text and not overwrite:
                note(f"kept existing script {ref}")
            else:
                store[ref] = text
            return
        target = self._script_target(ref, parent)
        if target is None:
            note(f"script {ref!r} not installed: no script_dir above the insert point")
            return
        existing = self._current_text(target)
        if existing is not None and existing != text and not overwrite:
            note(f"kept existing {mf.rel_of(self.root, target)} (differs from the fragment's)")
            return
        self._scripts[target] = text

    def script_conflicts(self, frag: Fragment, parent: str | None) -> list[str]:
        """Refs whose target file already exists with different content."""
        out = []
        used = {sc.script_ref(d) for d in frag.nodes.values()}
        for ref, text in frag.scripts.items():
            if ref not in used:
                continue
            if self.kind == "fragment":
                cur = self._frag_scripts().get(ref)
            else:
                target = self._script_target(ref, parent)
                cur = self._current_text(target) if target else None
            if cur is not None and cur != text:
                out.append(ref)
        return out

    def script_info(self, nodes: Mapping[str, Any], key: str) -> tuple[str | None, str]:
        """(location label, state) for the script of `key`; state in unset|unsupported|no-dir|missing|ok."""
        ref = sc.script_ref(nodes.get(key))
        if not ref:
            return None, "unset"
        if self.kind == "fragment":
            if sc.is_absolute(ref):
                return ref, "unsupported"
            return f"{ref} (embedded)", "ok" if ref in self._frag_scripts() else "missing"
        path = sc.script_path(self.root, nodes, key)
        if path is None:
            return ref, "no-dir"
        label = mf.rel_of(self.root, path)
        return label, "ok" if self._current_text(path) is not None else "missing"

    def read_script(self, nodes: Mapping[str, Any], key: str) -> str | None:
        ref = sc.script_ref(nodes.get(key))
        if not ref:
            return None
        if self.kind == "fragment":
            return self._frag_scripts().get(ref)
        path = sc.script_path(self.root, nodes, key)
        return self._current_text(path) if path else None

    def write_script(self, nodes: Mapping[str, Any], key: str, text: str) -> None:
        ref = sc.script_ref(nodes.get(key))
        if not ref:
            raise WorkspaceError("this command has no `script` set")
        with self.transaction(f"edit script {ref}"):
            if self.kind == "fragment":
                if sc.is_absolute(ref):
                    raise WorkspaceError("absolute script paths cannot be embedded in a fragment")
                self._frag_scripts()[ref] = text
            else:
                path = sc.script_path(self.root, nodes, key)
                if path is None:
                    raise WorkspaceError("no script_dir on the root command, so the script location is unknown")
                self._scripts[path] = text

    # -- drafts ---------------------------------------------------------------------
    def begin_edit(self, key: str, subtree: bool = False) -> Draft:
        return Draft(origin=key, subtree=subtree, fragment=self.extract(key, subtree, with_scripts=False))

    def commit(self, draft: Draft) -> None:
        """Apply a draft atomically. Unchanged nodes are left untouched."""
        with self.transaction(f"edit {draft.origin}"):
            for old, new in draft.renames.items():
                self.rename(old, new)
            origin = draft.workspace_key
            self.get(origin)
            current = self.subtree_keys(origin) if draft.subtree else [origin]
            target = draft.fragment.nodes
            for k in reversed(current):            # 1) drop nodes the draft removed (deepest first)
                if k not in target:
                    self._delete_key(k)
            for k, data in target.items():         # 2) update changed nodes, add new ones
                if k in self.index:
                    if self.index[k].data != data:
                        self.docs[self.index[k].file][k] = copy.deepcopy(data)
                else:
                    self._put(k, copy.deepcopy(data))
            self._reindex()

    # -- persistence ----------------------------------------------------------------
    def _sync_fragment_meta(self) -> None:
        doc = next(iter(self.docs.values()))
        meta = doc.setdefault(Fragment.META_KEY, {"load": False})
        if isinstance(meta, dict):
            meta["load"] = False
            tops = self.top_level()
            if len(tops) == 1:
                meta["root"] = tops[0].key
            used = {sc.script_ref(n.data) for n in self.index.values()}
            store = meta.get("scripts")
            if isinstance(store, dict):
                for ref in [r for r in store if r not in used]:
                    del store[ref]
                if not store:
                    del meta["scripts"]

    @staticmethod
    def _disk_text(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def save(self) -> list[str]:
        """Write only files whose data changed; delete dropped files. Returns touched paths."""
        touched: list[str] = []
        if self.kind == "fragment":
            self._sync_fragment_meta()
        else:
            for rel in list(self.docs):                      # new files not matched by any entry
                if not self._covered(rel) and (self.docs[rel] or rel in self._disk):
                    self.manifest.append(rel)
            for rel in [r for r in self._disk if r not in self.docs]:
                (self.root / rel).unlink(missing_ok=True)
                self._disk.pop(rel)
                touched.append(f"deleted {rel}")
        for rel, doc in self.docs.items():
            if not doc and rel not in self._disk:
                continue                                     # never create empty files
            if doc != self._disk.get(rel):
                yamlio.write(self.root / rel, doc)
                self._disk[rel] = copy.deepcopy(doc)
                touched.append(rel)
        for path, text in self._scripts.items():
            if self._disk_text(path) != text:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                touched.append(mf.rel_of(self.root, path))
        self._scripts.clear()
        if self.kind == "project" and self.manifest != self._manifest_disk:
            yamlio.write(self.root / self.manifest_name, self.manifest, "manifest")
            self._manifest_disk = list(self.manifest)
            touched.append(self.manifest_name)
        self.missing_files = {r for r in self.missing_files if r in self.docs and not (self.root / r).exists()}
        self.dirty = False
        return touched
