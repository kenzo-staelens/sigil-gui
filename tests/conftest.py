import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def project(tmp_path):
    (tmp_path / "yml").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run.py").write_text("def run(args, ctx): pass\n")
    (tmp_path / "manifest.yml").write_text("- yml/*.yml\n")
    (tmp_path / "yml" / "root.yml").write_text(
        "root:\n  name: mycli\n  script_dir: scripts\n"
    )
    (tmp_path / "yml" / "root_run.yml").write_text(
        "root_run:\n"
        "  name: run\n  parent: root\n  help: run containers\n  script: run\n"
        "  args:\n"
        "    - help: port\n      name: [-p, --port]\n      type: int\n"
        "    - kind: mutex\n      args:\n"
        "        - name: --a\n          action: store_true\n"
        "        - name: --b\n          action: store_true\n"
        "run_fast:\n  name: fast\n  parent: root_run\n  help: go fast\n"
    )
    return tmp_path
