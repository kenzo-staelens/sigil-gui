"""Small per-user state: recent projects and the library folder."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


def _config_dir() -> Path:
    try:
        from platformdirs import user_config_dir
        return Path(user_config_dir("sigil-gui"))
    except ImportError:
        return Path.home() / ".config" / "sigil-gui"


@dataclass
class Settings:
    recent: list[str] = field(default_factory=list)
    library_dir: str | None = None
    path: Path = field(default_factory=lambda: _config_dir() / "settings.json", repr=False)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        s = cls(path=path) if path else cls()
        try:
            raw = json.loads(s.path.read_text(encoding="utf-8"))
            s.recent = [r for r in raw.get("recent", []) if isinstance(r, str)]
            s.library_dir = raw.get("library_dir")
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"recent": self.recent, "library_dir": self.library_dir}, indent=2),
                                 encoding="utf-8")
        except OSError:
            pass                    # settings are a convenience, never fatal

    def add_recent(self, project: Path | str) -> None:
        p = str(project)
        self.recent = [p, *[r for r in self.recent if r != p]][:10]
        self.save()
