from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from SEC_DATA.filing_window import parse_iso_date
from SEC_DATA.submissions import TenKFiling, list_top_annual_filings_from_trimmed, primary_document_saved_basename


def _earliest_date_from_sync_meta(company_dir: Path) -> date | None:
    meta_path = company_dir / "sync_meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = str(meta.get("earliest_date_inclusive") or "").strip()[:10]
    return parse_iso_date(raw)


def _load_submissions(company_dir: Path) -> dict | None:
    p = company_dir / "submissions.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _narrative_for_accessions(narrative_path: Path, accessions: set[str]) -> str:
    if not narrative_path.is_file() or not accessions:
        return ""
    try:
        data = json.loads(narrative_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    chunks: list[str] = []
    for f in data.get("filings") or []:
        if not isinstance(f, dict):
            continue
        acc = str(f.get("accession_number") or "")
        if acc not in accessions:
            continue
        for c in f.get("concepts") or []:
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                chunks.append(c["text"])
    return "\n\n".join(chunks)


def select_bronze_disclosures(
    company_dir: Path,
    *,
    max_annual: int = 1,
) -> tuple[list[Path], list[TenKFiling], str]:
    """
    Primary document paths for the latest ``max_annual`` 10-K / 10-K/A filing(s) (default one
    annual), plus resolved ``TenKFiling`` rows and a note.

    Falls back to newest files under ``tenk_documents/`` when submissions are missing.
    """
    tenk_dir = company_dir / "tenk_documents"
    notes: list[str] = []
    subs = _load_submissions(company_dir)
    earliest = _earliest_date_from_sync_meta(company_dir)
    targets: list[TenKFiling] = []
    if subs is not None:
        eff_earliest = earliest
        if eff_earliest is None:
            eff_earliest = date.today() - timedelta(days=int(365.25 * 12))
            notes.append("synthetic_earliest_for_disclosure_pick")
        targets = list_top_annual_filings_from_trimmed(subs, eff_earliest, limit=max_annual)
    else:
        notes.append("no_submissions_json")

    paths: list[Path] = []
    if targets:
        for t in targets:
            name = primary_document_saved_basename(t)
            p = tenk_dir / name
            if p.is_file():
                paths.append(p)
            else:
                notes.append(f"missing_primary:{name}")
    if not paths and tenk_dir.is_dir():
        notes.append("fallback_glob_tenk_documents")
        cands = sorted(
            [p for p in tenk_dir.iterdir() if p.is_file() and p.suffix.lower() in (".htm", ".html", ".pdf")],
            key=lambda x: x.name,
            reverse=True,
        )
        paths = cands[: max(1, max_annual)]

    return paths, targets, ";".join(notes) if notes else "ok"


def narrative_blob_for_selected_filings(company_dir: Path, targets: list[TenKFiling]) -> str:
    acc = {t.accession_number for t in targets}
    return _narrative_for_accessions(company_dir / "ALL_NARRATIVE_INFO.json", acc)
