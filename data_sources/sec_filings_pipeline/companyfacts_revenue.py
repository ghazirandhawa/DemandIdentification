from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from SEC_DATA.revenue_filter import estimate_latest_annual_revenue_usd

from .submissions_registry import find_company_run_file


def load_latest_revenue_usd_from_disk(
    store_root: Path,
    *,
    run_folder: str,
    ticker: str,
    cik10: str | None,
) -> float | None:
    """
    Latest fiscal-year consolidated revenue in USD from trimmed ``companyfacts_10k.json``.

    Uses the same deterministic logic as ``estimate_latest_annual_revenue_usd`` (10-K / FY USD
    facts). Returns ``None`` if the file is missing or no suitable fact exists.
    """
    path = find_company_run_file(
        store_root,
        run_folder=run_folder,
        ticker=ticker,
        cik10=cik10,
        basename="companyfacts_10k.json",
    )
    if path is None:
        return None
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    return estimate_latest_annual_revenue_usd(data)
