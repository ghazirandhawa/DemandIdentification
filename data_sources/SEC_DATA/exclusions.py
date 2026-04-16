from __future__ import annotations

from pathlib import Path

BIG_TECH_CIK10: frozenset[str] = frozenset(
    {
        "0000320193",
        "0000789019",
        "0001652044",
        "0001326801",
        "0001018724",
        "0001045810",
        "0001318605",
        "0001065280",
        "0001730168",
        "0000050863",
        "0000002488",
        "0000051143",
        "0001341439",
        "0001108524",
        "0000858877",
        "0000796343",
        "0001633917",
        "0001512673",
    }
)


def load_extra_cik10(path: Path | None) -> frozenset[str]:
    if path is None or not path.is_file():
        return frozenset()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not s.isdigit():
            continue
        out.add(str(int(s)).zfill(10))
    return frozenset(out)


def combined_exclusions(*extra: frozenset[str]) -> frozenset[str]:
    s: set[str] = set(BIG_TECH_CIK10)
    for e in extra:
        s.update(e)
    return frozenset(s)
