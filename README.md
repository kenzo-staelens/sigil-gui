# sigil-gui

Local GUI for building and editing [sigil-cli](https://github.com/kenzo-staelens/sigil) command trees.
Pure Python (NiceGUI + PyYAML), no JS toolchain.

```bash
pip install -e .                       # or: pip install -e ".[native,dev]"
sigil-gui                              # start page: open / create a project, manage the library
sigil-gui path/to/project              # open directly (folder with manifest.yml, or a manifest file)
sigil-gui --native                     # own desktop window instead of a browser tab
sigil-gui --library ~/my-sigil-library # override the library folder for this run
```

## Disclaimer

AI generated because i couldn't be arsed to write a management tool for sigil myself

## Workflow

| Need | Where |
| --- | --- |
| open / create projects | start page; **file browser** dialogs, recent projects remembered. *New project* asks for a parent folder + name and creates only that folder (`manifest.yml`, `yml/root.yml`, `scripts/`); it never writes to the launch directory |
| compose commands | tree (left): add child, duplicate, move, delete; all undoable |
| edit a node **or a whole subtree atomically** | inspector: edit a *draft*, then **Apply** (one transaction) or **Discard**. "edit whole subtree" makes the subtree the unit; the YAML tab edits the draft as text |
| edit scripts | **Script** tab (CodeMirror, Python): edits the file behind the node's `script` (`<script_dir>/<script>.py`, `script_dir` relative to the manifest folder). "Create from template" writes the `run(args, ctx)` skeleton; a live check flags syntax errors / a missing `run`. Script edits go through the same Apply button and are written on Save |
| repository of subcommands | **Library**, usable with no project open: create, edit, delete fragments on the start page. With a project open, "insert under selection" and "save subtree to library" |
| live feedback | right pane: `--help` of the draft (built like sigil's `Builder`), its YAML, structural issues (click to jump) |

## Manifest handling

Entries are paths or globs **relative to the manifest**, e.g. `- yml/*.yml` or `- ../shared/tools.yml`.
Globs are expanded on load. New nodes go to `<static folder of the first glob>/<key>.yml`
(`yml/<key>.yml` for `yml/*.yml`), so the manifest normally does not change; an explicit entry is only
appended when a new file is not covered by any existing entry. Deleting a node's last sibling in a file
deletes the file.

## Design notes

* **No schema, no pydantic.** A node is the raw mapping from the YAML file. Widgets exist for the common
  keys (`name`, `help`, `script`, `script_dir`, `known_args`, `default`, `load`, `args`, and per-argument
  `type`/`action`/`default`/`required`); everything else is edited as raw YAML and passes through untouched.
* **PyYAML only.** Comments are not preserved and anchors/aliases are expanded on write. To keep diffs
  small, only files whose *data* changed are rewritten; opening and saving an untouched project writes nothing.
* **Parents are keys.** `parent:` is the YAML top-level key (not `name`). Renaming a key rewrites its
  children's `parent` in the same transaction.
* **Transactions.** Every workspace mutation snapshots state, applies, re-validates and rolls back if it
  introduces a dangling parent, cycle or duplicate key. Snapshots are the undo history.
* **Library fragments are valid sigil config**: a YAML file with the subtree plus one `_fragment` object
  (`load: false`) that also embeds the scripts (`scripts: {ref: source}`, as YAML block scalars), so a fragment is a
  single self-contained file. Editing a fragment uses the same workspace/draft/undo machinery as a project.
  Inserting auto-suffixes colliding keys and installs the scripts under the target's `script_dir`; an existing
  script with different content is never replaced silently (you choose keep / overwrite). Absolute `script` paths are
  not embedded. Unused embedded scripts are pruned when a fragment is saved. The library folder defaults to a per-user data dir
  (`$SIGIL_GUI_LIBRARY` / `--library` / the in-app folder picker override it). Needs a NiceGUI 2.x recent enough for `ui.codemirror`.
* `core/` has no UI imports and is what the tests cover; `ui/` is a thin NiceGUI layer.
* Help preview and validation follow `stages/builder.py` and `models/command.py`: `parser_kwargs` are the
  non-field keys, `dest`/`formatter_class`/`parents` are flagged as ignored, `required` only applies to
  option-style names, `load: false` commands are skipped, root requires a `name`. If `sigil` is
  importable, its `LibArgParser` is used for the preview.

## Tests

`pytest` runs `tests/test_core.py` (no NiceGUI needed).
