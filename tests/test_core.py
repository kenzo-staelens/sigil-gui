import pytest

from sigil_gui.core import preview
from sigil_gui.core.library import Library
from sigil_gui.core.workspace import Workspace, WorkspaceError


def test_load_and_tree(project):
    ws = Workspace.open(project)
    assert [n.key for n in ws.top_level()] == ["root"]
    assert [n.key for n in ws.children("root")] == ["root_run"]
    assert ws.subtree_keys("root") == ["root", "root_run", "run_fast"]
    assert ws.issues() == []


def test_save_untouched_is_noop(project):
    before = {p: p.read_text() for p in project.rglob("*.yml")}
    ws = Workspace.open(project)
    assert ws.save() == []
    assert before == {p: p.read_text() for p in project.rglob("*.yml")}


def test_only_changed_files_are_written(project):
    ws = Workspace.open(project)
    ws.get("run_fast").data["help"] = "x"          # bypassing transactions is fine for this check
    assert ws.save() == ["yml/root_run.yml"]


def test_rename_rewrites_children(project):
    ws = Workspace.open(project)
    ws.rename("root_run", "run_cmd")
    assert ws.get("run_fast").parent == "run_cmd"
    assert ws.issues() == []
    assert ws.undo()
    assert ws.get("run_fast").parent == "root_run"


def test_move_cycle_rolls_back(project):
    ws = Workspace.open(project)
    with pytest.raises(WorkspaceError):
        ws.move("root_run", "run_fast")
    assert ws.get("root_run").parent == "root"
    assert not ws.can_undo


def test_remove_subtree_and_files(project):
    ws = Workspace.open(project)
    ws.remove("root_run")
    assert set(ws.index) == {"root"}
    assert "yml/root_run.yml" not in ws.docs
    ws.save()
    assert not (project / "yml" / "root_run.yml").exists()


def test_reparent_on_remove(project):
    ws = Workspace.open(project)
    ws.remove("root_run", reparent_children=True)
    assert ws.get("run_fast").parent == "root"


def test_draft_node_rename_and_edit(project):
    ws = Workspace.open(project)
    d = ws.begin_edit("root_run")
    d.data["help"] = "changed"
    d.rename_root("start")
    assert d.dirty
    ws.commit(d)
    assert ws.get("start").data["help"] == "changed"
    assert ws.get("run_fast").parent == "start"
    assert ws.issues() == []


def test_draft_subtree_add_drop(project):
    ws = Workspace.open(project)
    d = ws.begin_edit("root_run", subtree=True)
    del d.fragment.nodes["run_fast"]
    d.fragment.nodes["run_slow"] = {"name": "slow", "parent": "root_run"}
    ws.commit(d)
    assert set(ws.index) == {"root", "root_run", "run_slow"}


def test_draft_text_roundtrip(project):
    ws = Workspace.open(project)
    d = ws.begin_edit("root_run", subtree=True)
    d.replace_from_text(d.to_text().replace("go fast", "very fast"))
    ws.commit(d)
    assert ws.get("run_fast").data["help"] == "very fast"


def test_failed_commit_leaves_workspace_untouched(project):
    ws = Workspace.open(project)
    d = ws.begin_edit("run_fast")
    d.data["parent"] = "nope"
    with pytest.raises(WorkspaceError):
        ws.commit(d)
    assert ws.get("run_fast").parent == "root_run"


def test_library_roundtrip_and_insert(project, tmp_path):
    ws = Workspace.open(project)
    lib = Library(tmp_path / "lib")
    lib.save("runner", ws.extract("root_run"), "run stuff", ["docker"])
    assert [(e.name, e.size, e.tags) for e in lib.list()] == [("runner", 2, ("docker",))]
    ws.insert(lib.load("runner"), "root")       # keys collide -> auto-suffixed
    assert {"root_run_2", "run_fast_2"} <= set(ws.index)
    assert ws.get("run_fast_2").parent == "root_run_2"
    assert ws.issues() == [] or all(i.code == "name-clash" for i in ws.issues())


def test_duplicate_gets_distinct_name(project):
    ws = Workspace.open(project)
    new = ws.duplicate("root_run")
    assert ws.get(new).data["name"] == "run-copy"
    assert ws.issues() == []


def test_validation_findings(project):
    ws = Workspace.open(project)
    ws.get("root_run").data["script"] = "missing"
    ws.get("run_fast").data["args"] = [{"kind": "wat"}]
    codes = {i.code for i in ws.issues()}
    assert {"missing-script", "arg-kind"} <= codes


def test_preview_help(project):
    ws = Workspace.open(project)
    text = preview.render_help(ws.raw, "root_run")
    assert "usage: mycli run" in text and "--port" in text and "fast" in text
    ws.get("root_run").data["args"][0]["type"] = "nonsense"
    assert "cannot attach argument" in preview.render_help(ws.raw, "root_run")   # reported, not fatal


def test_create_skeleton(tmp_path):
    ws = Workspace.create(tmp_path / "new", "tool")
    assert ws.get("root").data["name"] == "tool"
    assert ws.manifest == ["yml/*.yml"] and ws.issues() == []


def test_create_refuses_relative_path():
    with pytest.raises(WorkspaceError):
        Workspace.create("relative/dir")


def test_draft_text_rename_of_root(project):
    ws = Workspace.open(project)
    d = ws.begin_edit("root_run")
    d.replace_from_text(d.to_text().replace("root_run:", "launch:"))
    ws.commit(d)
    assert "launch" in ws.index and "root_run" not in ws.index
    assert ws.get("run_fast").parent == "launch"
    assert ws.issues() == []


def test_new_nodes_land_in_glob_folder_and_manifest_is_untouched(project):
    ws = Workspace.open(project)
    ws.add_node("brand_new", parent="root")
    assert ws.get("brand_new").file == "yml/brand_new.yml"
    ws.save()
    assert (project / "yml" / "brand_new.yml").exists()
    assert (project / "manifest.yml").read_text() == "- yml/*.yml\n"
    assert Workspace.open(project).get("brand_new").parent == "root"


def test_explicit_entries_and_paths_outside_the_project(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "shared" / "tools.yml").write_text("root:\n  name: x\n")
    (tmp_path / "app" / "manifest.yml").write_text("- ../shared/tools.yml\n")
    ws = Workspace.open(tmp_path / "app")
    assert ws.get("root").file == "../shared/tools.yml"
    ws.add_node("child", parent="root")               # no glob -> new file needs an explicit entry
    ws.save()
    assert "child.yml" in ws.manifest
    assert Workspace.open(tmp_path / "app" / "manifest.yml").get("child").parent == "root"


def test_glob_helpers():
    from sigil_gui.core import manifest as mf
    assert mf.glob_match("yml/a.yml", "yml/*.yml") and not mf.glob_match("yml/s/a.yml", "yml/*.yml")
    assert mf.glob_match("yml/s/a.yml", "yml/**/*.yml") and mf.static_dir("yml/**/*.yml") == "yml"
    assert mf.norm("./yml/a.yml") == "yml/a.yml"


def test_library_fragment_edited_without_a_project(project, tmp_path):
    ws = Workspace.open(project)
    lib = Library(tmp_path / "lib")
    lib.save("runner", ws.extract("root_run"), "run stuff")
    frag_ws = Workspace.open_fragment(lib.path("runner"))
    assert frag_ws.kind == "fragment" and set(frag_ws.index) == {"root_run", "run_fast"}
    assert frag_ws.get("root_run").parent is None          # root parent stripped on save
    assert frag_ws.issues() == []
    d = frag_ws.begin_edit("root_run")
    d.rename_root("runner_cmd")
    d.data["help"] = "renamed"
    frag_ws.commit(d)
    frag_ws.add_node("extra", {"name": "extra"}, parent="runner_cmd")
    frag_ws.save()
    frag = lib.load("runner")
    assert frag.root == "runner_cmd" and set(frag.nodes) == {"runner_cmd", "run_fast", "extra"}
    assert frag.meta["description"] == "run stuff"
    assert [e.size for e in lib.list()] == [3]


def test_preview_matches_builder_semantics(project):
    ws = Workspace.open(project)
    ws.get("root").data["help"] = "not a parser kwarg"      # `help` must not reach ArgumentParser
    ws.get("root_run").data["dest"] = "ignored"
    text = preview.render_help(ws.raw, "root")
    assert "subcommands" in text and "run" in text and "cannot build" not in text
    sub = preview.render_help(ws.raw, "root_run")
    assert "usage: mycli run" in sub and "--port" in sub and "fast" in sub
    ws.get("run_fast").data["load"] = False
    assert "[load: false" in preview.render_help(ws.raw, "run_fast")


def test_validation_new_rules(project):
    ws = Workspace.open(project)
    ws.get("root_run").data["parents"] = ["x"]
    del ws.get("root").data["name"]
    codes = {i.code for i in ws.issues()}
    assert {"unsupported-kwarg", "missing-name"} <= codes


# ---- scripts ---------------------------------------------------------------------
def test_script_read_write_save_and_undo(project):
    from sigil_gui.core import scripts as sc
    ws = Workspace.open(project)
    assert ws.script_info(ws.raw, "root_run") == ("scripts/run.py", "ok")
    assert ws.read_script(ws.raw, "root_run").startswith("def run")
    ws.write_script(ws.raw, "root_run", sc.TEMPLATE)
    assert ws.dirty and ws.read_script(ws.raw, "root_run") == sc.TEMPLATE
    assert (project / "scripts" / "run.py").read_text().startswith("def run")        # not on disk yet
    ws.undo()
    assert ws.read_script(ws.raw, "root_run").startswith("def run")
    ws.redo()
    assert "scripts/run.py" in ws.save()
    assert (project / "scripts" / "run.py").read_text() == sc.TEMPLATE


def test_script_state_variants(project):
    ws = Workspace.open(project)
    assert ws.script_info(ws.raw, "run_fast") == (None, "unset")
    ws.get("run_fast").data["script"] = "sub.mod"
    label, state = ws.script_info(ws.raw, "run_fast")
    assert state == "missing" and label == "scripts/sub.mod.py"        # flat path until a nested file exists
    ws.write_script(ws.raw, "run_fast", "def run(args, ctx): pass\n")
    ws.save()
    assert (project / "scripts" / "sub.mod.py").exists()
    del ws.get("root").data["script_dir"]
    assert ws.script_info(ws.raw, "run_fast")[1] == "no-dir"


def test_fragments_carry_scripts(project, tmp_path):
    ws = Workspace.open(project)
    frag = ws.extract("root_run")
    assert frag.scripts == {"run": (project / "scripts" / "run.py").read_text()}
    lib = Library(tmp_path / "lib")
    lib.save("runner", frag)
    assert "scripts:" in lib.path("runner").read_text() and "|" in lib.path("runner").read_text()
    assert [e.scripts for e in lib.list()] == [1]

    other = Workspace.create(tmp_path / "other", "o")
    loaded = lib.load("runner")
    assert loaded.scripts == frag.scripts                              # YAML round trip is exact
    report = []
    other.insert(loaded, "root", report=report)
    assert report == []
    other.save()
    assert (tmp_path / "other" / "scripts" / "run.py").read_text() == frag.scripts["run"]


def test_insert_never_clobbers_unless_asked(project, tmp_path):
    ws = Workspace.open(project)
    frag = ws.extract("root_run")
    frag.scripts["run"] = "def run(args, ctx):\n    print('new')\n"
    assert ws.script_conflicts(frag, "root") == ["run"]
    report = []
    ws.insert(frag, "root", report=report)
    assert report and "kept existing" in report[0]
    assert ws.read_script(ws.raw, "root_run").startswith("def run(args, ctx): pass")
    ws.insert(frag, "root", scripts="overwrite")
    assert "print('new')" in ws.read_script(ws.raw, "root_run")


def test_insert_without_script_dir_reports(project, tmp_path):
    ws = Workspace.open(project)
    frag = ws.extract("root_run")
    report = []
    ws.insert(frag, None, report=report)          # would become a second root; script has nowhere to go
    assert any("no script_dir" in r for r in report)


def test_fragment_workspace_edits_scripts(project, tmp_path):
    ws = Workspace.open(project)
    lib = Library(tmp_path / "lib")
    lib.save("runner", ws.extract("root_run"))
    fw = Workspace.open_fragment(lib.path("runner"))
    assert fw.script_info(fw.raw, "root_run") == ("run (embedded)", "ok")
    fw.write_script(fw.raw, "root_run", "def run(args, ctx):\n    return None\n")
    fw.get("run_fast").data["script"] = "fast"
    fw.write_script(fw.raw, "run_fast", "def run(args, ctx): pass\n")
    fw.get("run_fast").data.pop("script")                 # unused script is pruned on save
    fw.save()
    frag = lib.load("runner")
    assert set(frag.scripts) == {"run"} and "return None" in frag.scripts["run"]


def test_check_source():
    from sigil_gui.core import scripts as sc
    assert sc.check_source(sc.TEMPLATE) is None
    assert "syntax error" in sc.check_source("def run(:\n")
    assert "no top-level run" in sc.check_source("x = 1\n")
