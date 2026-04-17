from __future__ import annotations

import re
from pathlib import Path

_RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}Z$")


def is_run_folder_name(name: str) -> bool:
    return bool(_RUN_DIR_RE.match(name))


def latest_run_dir(store_root: Path) -> Path | None:
    """Return newest UTC run directory under ``store_root`` (lexicographic sort matches time)."""
    if not store_root.is_dir():
        return None
    candidates = [p for p in store_root.iterdir() if p.is_dir() and is_run_folder_name(p.name)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def iter_company_dirs(run_dir: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(run_dir.iterdir()):
        if p.is_dir() and not p.name.startswith("."):
            out.append(p)
    return out
