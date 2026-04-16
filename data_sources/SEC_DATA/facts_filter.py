from __future__ import annotations

from datetime import date
from typing import Any

from .filing_window import companyfacts_row_in_window

TENK_FORMS = frozenset({"10-K", "10-K/A"})


def filter_companyfacts_by_forms(
    data: dict[str, Any],
    forms: frozenset[str] | None = None,
) -> dict[str, Any]:
    """
    Return a copy of companyfacts JSON keeping only fact observations whose
    ``form`` is in ``forms`` (default: 10-K and 10-K/A).
    """
    if forms is None:
        forms = TENK_FORMS
    facts_in = data.get("facts") or {}
    out_facts: dict[str, dict[str, Any]] = {}
    if not isinstance(facts_in, dict):
        return {"cik": data.get("cik"), "entityName": data.get("entityName"), "facts": {}}

    for tax, tags in facts_in.items():
        if not isinstance(tags, dict):
            continue
        out_tags: dict[str, Any] = {}
        for tag, meta in tags.items():
            if not isinstance(meta, dict):
                continue
            units = meta.get("units")
            if not isinstance(units, dict):
                continue
            out_units: dict[str, list[Any]] = {}
            for unit_key, rows in units.items():
                if not isinstance(rows, list):
                    continue
                kept = [
                    r
                    for r in rows
                    if isinstance(r, dict) and str(r.get("form", "")) in forms
                ]
                if kept:
                    out_units[str(unit_key)] = kept
            if out_units:
                new_meta = {k: v for k, v in meta.items() if k != "units"}
                new_meta["units"] = out_units
                out_tags[str(tag)] = new_meta
        if out_tags:
            out_facts[str(tax)] = out_tags

    return {
        "cik": data.get("cik"),
        "entityName": data.get("entityName"),
        "facts": out_facts,
    }


def filter_companyfacts_by_forms_and_filed_on_or_after(
    data: dict[str, Any],
    forms: frozenset[str] | None,
    earliest: date,
) -> dict[str, Any]:
    """
    Like :func:`filter_companyfacts_by_forms`, but each kept observation must
    also fall on or after ``earliest`` using ``filed`` / ``end`` / ``fy`` (see
    ``companyfacts_row_in_window`` in ``filing_window.py``).
    """
    if forms is None:
        forms = TENK_FORMS
    facts_in = data.get("facts") or {}
    out_facts: dict[str, dict[str, Any]] = {}
    if not isinstance(facts_in, dict):
        return {"cik": data.get("cik"), "entityName": data.get("entityName"), "facts": {}}

    for tax, tags in facts_in.items():
        if not isinstance(tags, dict):
            continue
        out_tags: dict[str, Any] = {}
        for tag, meta in tags.items():
            if not isinstance(meta, dict):
                continue
            units = meta.get("units")
            if not isinstance(units, dict):
                continue
            out_units: dict[str, list[Any]] = {}
            for unit_key, rows in units.items():
                if not isinstance(rows, list):
                    continue
                kept = [
                    r
                    for r in rows
                    if isinstance(r, dict)
                    and str(r.get("form", "")) in forms
                    and companyfacts_row_in_window(r, earliest)
                ]
                if kept:
                    out_units[str(unit_key)] = kept
            if out_units:
                new_meta = {k: v for k, v in meta.items() if k != "units"}
                new_meta["units"] = out_units
                out_tags[str(tag)] = new_meta
        if out_tags:
            out_facts[str(tax)] = out_tags

    return {
        "cik": data.get("cik"),
        "entityName": data.get("entityName"),
        "facts": out_facts,
    }
