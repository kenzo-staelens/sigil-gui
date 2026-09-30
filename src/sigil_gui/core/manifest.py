"""Manifest entries: paths or globs, relative to the manifest file itself."""
from __future__ import annotations

import fnmatch
import glob
import os
import re
from pathlib import Path, PurePosixPath

_MAGIC = re.compile(r"[*?\[]")


def has_magic(entry: str) -> bool:
    return bool(_MAGIC.search(entry))


def norm(entry: str) -> str:
    """Canonical posix spelling ("./yml/a.yml" -> "yml/a.yml"); globs are left alone."""
    return PurePosixPath(entry.replace("\\", "/")).as_posix()


def rel_of(root: Path, path: Path) -> str:
    try:
        return Path(os.path.relpath(path, root)).as_posix()
    except ValueError:          # different drive on Windows
        return Path(path).as_posix()


def expand(root: Path, pattern: str) -> list[str]:
    """Files matched by `pattern`, as sorted paths relative to `root`."""
    hits = glob.glob(str(root / pattern), recursive=True)
    return sorted(rel_of(root, Path(h)) for h in hits if Path(h).is_file())


def glob_match(rel: str, pattern: str) -> bool:
    """Would `glob.glob(pattern, recursive=True)` return `rel`? (`*` never crosses `/`.)"""
    def m(p: tuple[str, ...], g: tuple[str, ...]) -> bool:
        if not g:
            return not p
        if g[0] == "**":
            return any(m(p[i:], g[1:]) for i in range(len(p) + 1))
        return bool(p) and fnmatch.fnmatchcase(p[0], g[0]) and m(p[1:], g[1:])

    return m(PurePosixPath(rel).parts, PurePosixPath(norm(pattern)).parts)


def static_dir(pattern: str) -> str:
    """Directory part before the first wildcard: 'yml/**/*.yml' -> 'yml'."""
    parts = PurePosixPath(norm(pattern)).parts
    keep = []
    for part in parts[:-1]:
        if has_magic(part):
            break
        keep.append(part)
    return PurePosixPath(*keep).as_posix() if keep else ""
