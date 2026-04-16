from __future__ import annotations

import re
from typing import Any

from .client import EdgarClient
from .submissions import accession_no_dashes, archives_document_url, cik_for_archives_path


def archives_index_json_url(*, cik10: str, accession_number: str) -> str:
    cik_path = cik_for_archives_path(cik10)
    acc = accession_no_dashes(accession_number)
    return f"https://www.sec.gov/Archives/edgar/data/{cik_path}/{acc}/index.json"


def _index_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (data.get("directory") or {}).get("item")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    return []


def ranked_index_pdf_names(
    client: EdgarClient,
    *,
    cik10: str,
    accession_number: str,
    primary_document: str,
    max_pdfs: int = 15,
) -> list[str]:
    """
    Return PDF filenames from the filing ``index.json``, ordered with likely
    10-K body PDFs first (many 10-Ks are HTML-only; PDFs are often absent).
    """
    try:
        data = client.get_json(archives_index_json_url(cik10=cik10, accession_number=accession_number))
    except Exception:
        return []
    names = [str(it.get("name") or "") for it in _index_items(data)]
    pdfs = [n for n in names if n.lower().endswith(".pdf")]
    if not pdfs:
        return []

    primary_u = primary_document.upper()

    def score(n: str) -> int:
        u = n.upper()
        s = 0
        if re.search(r"10[-\s]?K", u):
            s += 20
        if "ANNUAL" in u:
            s += 8
        if primary_u and primary_u.rsplit(".", 1)[0] in u.replace("-", "").upper():
            s += 5
        return s

    pdfs.sort(key=score, reverse=True)
    return pdfs[: max(1, max_pdfs)]


def archives_file_url(*, cik10: str, accession_number: str, filename: str) -> str:
    return archives_document_url(
        cik10=cik10,
        accession_number=accession_number,
        primary_document=filename,
    )
