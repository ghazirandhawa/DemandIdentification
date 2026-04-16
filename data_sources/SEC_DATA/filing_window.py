from __future__ import annotations

import copy
import re
from datetime import date, timedelta
from typing import Any


def rolling_earliest_date(*, years: int = 2) -> date:
    return date.today() - timedelta(days=int(365.25 * years))


def rolling_earliest_from_quarters(quarters: int) -> date:
    """Approximate calendar quarters (~91.31 days each) for filing-date cutoffs."""
    q = max(1, int(quarters))
    return date.today() - timedelta(days=int(round(91.3125 * q)))


def resolve_rolling_earliest(*, years: int | None, quarters: int | None) -> tuple[date, str]:
    """
    If ``years`` is set, use a year-based lookback; otherwise use ``quarters``
    (defaulting to 8 when ``quarters`` is None).
    """
    if years is not None:
        y = max(1, int(years))
        return rolling_earliest_date(years=y), f"{y}y"
    q = 8 if quarters is None else max(1, int(quarters))
    return rolling_earliest_from_quarters(q), f"{q}q"


def parse_iso_date(s: str) -> date | None:
    s = (s or "").strip()[:10]
    if len(s) < 10 or s[4] != "-" or s[7] != "-":
        return None
    try:
        return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except ValueError:
        return None


def filing_date_on_or_after(filing_date: str, earliest: date) -> bool:
    d = parse_iso_date(filing_date)
    if d is None:
        return False
    return d >= earliest


def trim_filings_recent_in_place(submissions: dict[str, Any], earliest: date) -> dict[str, Any]:
    """
    Return a deep copy of ``submissions`` with ``filings.recent`` columnar arrays
    restricted to rows whose ``filingDate`` is on or after ``earliest``.
    """
    data = copy.deepcopy(submissions)
    recent = (data.get("filings") or {}).get("recent")
    if not isinstance(recent, dict) or "filingDate" not in recent:
        return data
    dates = recent["filingDate"]
    if not isinstance(dates, list):
        return data
    n = len(dates)
    keep_idx = [i for i in range(n) if filing_date_on_or_after(str(dates[i]), earliest)]
    for k, col in list(recent.items()):
        if isinstance(col, list) and len(col) == n:
            recent[k] = [col[i] for i in keep_idx]
    return data


def companyfacts_row_in_window(row: dict[str, Any], earliest: date) -> bool:
    fd = row.get("filed")
    if isinstance(fd, str):
        d = parse_iso_date(fd)
        if d is not None:
            return d >= earliest
    end = row.get("end")
    if isinstance(end, str):
        d = parse_iso_date(end)
        if d is not None:
            return d >= earliest
    fy = row.get("fy")
    if isinstance(fy, int):
        return fy >= earliest.year
    return False


_NARRATIVE_CONCEPT = re.compile(
    r"(?i)(Business|Segment|Product|Service|Customer|Concentration|Supplier|Vendor|"
    r"Competition|Market|Overview|Description|Risk|Legal|Proceeding|Property|"
    r"Manufacturing|HumanCapital|Cybersecurity|Commitment|Contractual)",
)

_MIN_NARRATIVE_CHARS = 180


def narrative_concept_filter(concept_name: str, text: str) -> bool:
    if len(text) < _MIN_NARRATIVE_CHARS:
        return False
    return bool(_NARRATIVE_CONCEPT.search(concept_name))
