from __future__ import annotations

from typing import TextIO


def _bar(done: int, total: int, width: int = 22) -> str:
    if total <= 0:
        return "[" + "-" * width + "]"
    frac = max(0.0, min(1.0, done / total))
    n = int(round(frac * width))
    n = min(max(n, 0), width)
    return "[" + "#" * n + "-" * (width - n) + "]"


def write_pull_filing_progress(
    stream: TextIO,
    *,
    label: str,
    filing_index: int,
    filing_total: int,
    accession: str,
    width: int = 22,
) -> None:
    """``filing_index`` is 1-based for display."""
    if filing_total <= 0:
        return
    bar = _bar(filing_index, filing_total, width)
    acc = accession.replace("-", "")[:18]
    line = f"pull {label} 10-K {bar} {filing_index}/{filing_total} {acc}"
    stream.write("\r" + line + " " * max(0, 100 - len(line)))
    stream.flush()


def write_bulk_save_progress(
    stream: TextIO,
    *,
    saved: int,
    saved_goal: int,
    rows_seen: int,
    ticker: str,
    cik10: str,
    width: int = 22,
) -> None:
    bar = _bar(saved, saved_goal, width) if saved_goal > 0 else _bar(0, 1, width)
    tick = (ticker or "").strip().upper() or "—"
    line = f"bulk {bar} saved {saved}/{saved_goal}  ticker_rows≥{rows_seen}  last {tick} {cik10}"
    stream.write("\r" + line + " " * max(0, 130 - len(line)))
    stream.flush()


def progress_finish_line(stream: TextIO) -> None:
    stream.write("\n")
    stream.flush()
