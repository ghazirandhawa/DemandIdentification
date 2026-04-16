from __future__ import annotations

from typing import Any, Iterator

_US_GAAP = "us-gaap"

# Primary tags used in IO / accounting literature for consolidated sales.
_REVENUE_TAGS: tuple[str, ...] = (
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
)

# Default “large firm” cutoff for IO-style samples (exclude when revenue is above this).
DEFAULT_MAX_REVENUE_USD = 1_000_000_000.0


def _usd_rows_for_tag(facts: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    block = ((facts.get("facts") or {}).get(_US_GAAP) or {}).get(tag) or {}
    units = block.get("units") or {}
    out: list[dict[str, Any]] = []
    if not isinstance(units, dict):
        return out
    for unit_key, rows in units.items():
        if not isinstance(unit_key, str) or not isinstance(rows, list):
            continue
        uk = unit_key.upper()
        if "USD" not in uk or "PER" in uk:
            continue
        for row in rows:
            if isinstance(row, dict):
                out.append(row)
    return out


def _iter_revenue_rows(facts: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    seen: set[tuple[Any, ...]] = set()
    for tag in _REVENUE_TAGS:
        for row in _usd_rows_for_tag(facts, tag):
            key = (tag, row.get("accn"), row.get("end"), row.get("val"), row.get("fy"))
            if key in seen:
                continue
            seen.add(key)
            yield tag, row


def estimate_latest_annual_revenue_usd(facts: dict[str, Any]) -> float | None:
    """
    Best-effort consolidated annual revenue in USD from ``companyfacts`` JSON.

    Uses ``us-gaap:Revenues`` or ``SalesRevenueNet`` (then contract-revenue tag)
    in **USD** units, restricted to **10-K / 10-K/A** rows, preferring ``fp ==
    "FY"`` and the **latest fiscal year** ``fy`` (ties broken by ``filed`` date).

    Returns ``None`` if no suitable fact is found (caller should not treat as
    mega-cap for exclusion purposes).
    """
    candidates: list[tuple[dict[str, Any], float]] = []
    for _tag, row in _iter_revenue_rows(facts):
        if str(row.get("form", "")) not in ("10-K", "10-K/A"):
            continue
        fp = row.get("fp")
        if fp is not None and str(fp).strip() != "" and str(fp).strip().upper() != "FY":
            continue
        fy = row.get("fy")
        if not isinstance(fy, int):
            continue
        raw = row.get("val")
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val <= 0:
            continue
        candidates.append((row, val))

    if not candidates:
        for _tag, row in _iter_revenue_rows(facts):
            if str(row.get("form", "")) not in ("10-K", "10-K/A"):
                continue
            fy = row.get("fy")
            if not isinstance(fy, int):
                continue
            raw = row.get("val")
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if val <= 0:
                continue
            candidates.append((row, val))

    if not candidates:
        return None

    def filed_key(item: tuple[dict[str, Any], float]) -> str:
        f = item[0].get("filed")
        return str(f) if isinstance(f, str) else ""

    best_fy = max(row.get("fy", -1) for row, _v in candidates if isinstance(row.get("fy"), int))
    same_fy = [(row, v) for row, v in candidates if row.get("fy") == best_fy]
    same_fy.sort(key=filed_key, reverse=True)
    return float(same_fy[0][1])


def revenue_exceeds_threshold(
    facts: dict[str, Any],
    max_revenue_usd: float,
) -> tuple[bool, float | None]:
    """
    Returns ``(excluded, revenue_or_none)`` where ``excluded`` is True iff
    revenue is known and strictly greater than ``max_revenue_usd``.
    """
    rev = estimate_latest_annual_revenue_usd(facts)
    if rev is None:
        return False, None
    return rev > max_revenue_usd, rev
