from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from SEC_DATA.paths import company_folder_slug, company_output_dir
from SEC_DATA.submissions import format_cik10


def _clean(s: Any) -> str:
    if s is None:
        return ""
    return str(s).strip()


def format_address_block(block: Any) -> str:
    if not isinstance(block, dict):
        return ""
    lines: list[str] = []
    s1 = _clean(block.get("street1"))
    s2 = _clean(block.get("street2"))
    if s1:
        lines.append(s1)
    if s2:
        lines.append(s2)
    city = _clean(block.get("city"))
    region = _clean(block.get("stateOrCountry")) or _clean(block.get("stateOrCountryDescription"))
    z = _clean(block.get("zipCode"))
    country = _clean(block.get("country"))
    tail_parts = [p for p in (city, region, z) if p]
    tail = ", ".join(tail_parts)
    if country and country.upper() not in tail.upper():
        tail = f"{tail}, {country}" if tail else country
    if lines:
        head = ", ".join(lines)
        return f"{head}\n{tail}".strip() if tail else head
    return tail


def _country_from_block(block: Any) -> str:
    if not isinstance(block, dict):
        return ""
    c = _clean(block.get("country"))
    if c:
        return c
    cc = _clean(block.get("countryCode"))
    if cc and len(cc) > 2:
        return cc
    return ""


def country_from_submissions(data: dict[str, Any]) -> str | None:
    addrs = data.get("addresses")
    if not isinstance(addrs, dict):
        return None
    for key in ("business", "mailing"):
        b = addrs.get(key)
        c = _country_from_block(b)
        if c:
            return c
    return None


def website_from_submissions(data: dict[str, Any]) -> str | None:
    w = _clean(data.get("website"))
    if w:
        return w
    iw = _clean(data.get("investorWebsite"))
    return iw or None


def extract_submissions_contact_fields(data: dict[str, Any]) -> dict[str, str | None]:
    addrs = data.get("addresses") if isinstance(data.get("addresses"), dict) else {}
    mailing = addrs.get("mailing") if isinstance(addrs.get("mailing"), dict) else {}
    business = addrs.get("business") if isinstance(addrs.get("business"), dict) else {}
    m = format_address_block(mailing)
    b = format_address_block(business)
    return {
        "country": country_from_submissions(data),
        "mailing_address": m or None,
        "business_address": b or None,
        "website": website_from_submissions(data),
    }


def format_officers_position_name(officers_contacts: Any, *, max_chars: int = 12_000) -> str | None:
    if not isinstance(officers_contacts, dict):
        return None
    raw = officers_contacts.get("officers")
    if not isinstance(raw, list):
        return None
    lines: list[str] = []
    for o in raw:
        if not isinstance(o, dict):
            continue
        title = _clean(o.get("title"))
        name = _clean(o.get("name"))
        if not name and not title:
            continue
        if title and name:
            lines.append(f"{title} : {name}")
        elif name:
            lines.append(name)
        elif title:
            lines.append(f"{title}:")
    out = "\n".join(lines).strip()
    if not out:
        return None
    if len(out) > max_chars:
        return out[: max_chars - 40] + "\n[... truncated ...]"
    return out


def find_company_run_file(
    store_root: Path,
    *,
    run_folder: str,
    ticker: str,
    cik10: str | None,
    basename: str,
) -> Path | None:
    """Locate a file under the company sync dir (same folder rules as ``sync_company``)."""
    candidates: list[Path] = []
    cik_f: str | None = None
    if cik10 and str(cik10).strip():
        try:
            cik_f = format_cik10(cik10)
        except (TypeError, ValueError):
            cik_f = None
    tk = (ticker or "").strip()
    if cik_f:
        slug1 = company_folder_slug(tk or None, cik_f)
        candidates.append(company_output_dir(store_root, run_folder, slug1) / basename)
        candidates.append(
            company_output_dir(store_root, run_folder, company_folder_slug(None, cik_f)) / basename
        )
    if tk:
        safe = re.sub(r"[^A-Z0-9._-]+", "_", tk.upper()).strip("._")[:40] or tk
        candidates.append(store_root / run_folder / tk / basename)
        candidates.append(store_root / run_folder / safe / basename)
    seen: set[str] = set()
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            return p
    return None


def find_submissions_json(
    store_root: Path,
    *,
    run_folder: str,
    ticker: str,
    cik10: str | None,
) -> Path | None:
    """Locate ``submissions.json`` using the same folder rules as ``sync_company``."""
    return find_company_run_file(
        store_root,
        run_folder=run_folder,
        ticker=ticker,
        cik10=cik10,
        basename="submissions.json",
    )


def load_registry_fields_from_disk(
    store_root: Path,
    *,
    run_folder: str,
    ticker: str,
    cik10: str | None,
) -> dict[str, str | None]:
    """Parse ``submissions.json`` when present; else empty strings / None."""
    empty: dict[str, str | None] = {
        "country": None,
        "mailing_address": None,
        "business_address": None,
        "website": None,
    }
    path = find_submissions_json(store_root, run_folder=run_folder, ticker=ticker, cik10=cik10)
    if path is None:
        return dict(empty)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return dict(empty)
    if not isinstance(data, dict):
        return dict(empty)
    out = extract_submissions_contact_fields(data)
    return {**empty, **out}
