from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

_MODULE_DIR = Path(__file__).resolve().parent
_DATA_SOURCES = _MODULE_DIR.parent
DEFAULT_STORE_ROOT = _DATA_SOURCES / "edgar_disclosures"


def company_folder_slug(ticker: str | None, cik10: str) -> str:
    """
    Sanitized folder name for a company (under a run folder): ticker, or
    ``CIK_{cik10}`` if no ticker is known.
    """
    if ticker and str(ticker).strip():
        t = str(ticker).strip().upper()
        slug = re.sub(r"[^A-Z0-9._-]+", "_", t)
        slug = slug.strip("._")[:40]
        return slug or f"CIK_{cik10}"
    return f"CIK_{cik10}"


def utc_run_folder_name(when: datetime | None = None) -> str:
    """UTC folder name for a single sync run (``YYYY-mm-ddTHHMMSSZ``)."""
    ts = when or datetime.now(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H%M%SZ")


def company_output_dir(store_root: Path, run_folder: str, folder_slug: str) -> Path:
    """``{store_root}/{run_folder}/{ticker_or_cik_slug}/``."""
    return store_root / run_folder / folder_slug


def ensure_company_output_dir(store_root: Path, run_folder: str, folder_slug: str) -> Path:
    d = company_output_dir(store_root, run_folder, folder_slug)
    d.mkdir(parents=True, exist_ok=True)
    return d
