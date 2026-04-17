from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _latest_usd_row(facts: dict[str, Any], tax: str, tag: str) -> dict[str, Any] | None:
    block = ((facts.get("facts") or {}).get(tax) or {}).get(tag) or {}
    units = block.get("units") or {}
    rows = units.get("USD")
    if not isinstance(rows, list) or not rows:
        return None
    best: dict[str, Any] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("form", "")) not in ("10-K", "10-K/A"):
            continue
        if best is None:
            best = row
            continue
        fy = row.get("fy")
        bfy = best.get("fy")
        if isinstance(fy, int) and isinstance(bfy, int) and fy > bfy:
            best = row
        elif fy == bfy:
            fd = str(row.get("filed", ""))
            bfd = str(best.get("filed", ""))
            if fd > bfd:
                best = row
    return best


def extract_public_float_usd(companyfacts_path: Path) -> tuple[float | None, str]:
    """
    Latest ``dei:EntityPublicFloat`` in USD from trimmed companyfacts (10-K window).

    This is the SEC cover-page style aggregate market value of non-affiliate equity
    (often used as a market-capacity proxy, not identical to live trading cap).
    """
    if not companyfacts_path.is_file():
        return None, "no_companyfacts_file"
    try:
        data = json.loads(companyfacts_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "companyfacts_unreadable"
    row = _latest_usd_row(data, "dei", "EntityPublicFloat")
    if row is None:
        return None, "entity_public_float_missing"
    val = row.get("val")
    if isinstance(val, (int, float)):
        return float(val), "entity_public_float_usd"
    return None, "entity_public_float_bad_val"
