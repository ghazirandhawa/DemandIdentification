from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from .client import EdgarClient

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"

_TENK_FORMS = frozenset({"10-K", "10-K/A"})


def format_cik10(cik: str | int) -> str:
    """Return CIK as a 10-digit string with leading zeros (submissions API path)."""
    s = str(int(cik)) if isinstance(cik, str) and cik.isdigit() else str(cik).lstrip("0") or "0"
    if not s.isdigit():
        raise ValueError(f"CIK must be numeric, got {cik!r}")
    return s.zfill(10)


def cik_for_archives_path(cik10: str) -> str:
    """CIK used in /Archives/edgar/data/{cik}/... URLs (no leading zeros)."""
    return str(int(cik10))


def accession_no_dashes(accession_number: str) -> str:
    return accession_number.replace("-", "")


def archives_document_url(
    *,
    cik10: str,
    accession_number: str,
    primary_document: str,
) -> str:
    """
    Direct URL to the primary filing document on www.sec.gov/Archives.

    primary_document comes from the submissions JSON (e.g. 'aapl-20250927.htm').
    """
    cik_path = cik_for_archives_path(cik10)
    acc = accession_no_dashes(accession_number)
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_path}/{acc}/{primary_document}"
    )


@dataclass(frozen=True)
class TenKFiling:
    cik10: str
    name: str
    form: str
    filing_date: str
    report_date: str | None
    accession_number: str
    primary_document: str
    primary_doc_description: str | None
    filing_index_url: str
    primary_document_url: str
    is_xbrl: bool | None
    is_inline_xbrl: bool | None
    file_number: str | None = None
    film_number: str | None = None
    size_bytes: int | None = None
    acceptance_datetime: str | None = None
    act: str | None = None
    items: str | None = None
    core_type: str | None = None
    is_xbrl_numeric: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cik10": self.cik10,
            "name": self.name,
            "form": self.form,
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "primary_document": self.primary_document,
            "primary_doc_description": self.primary_doc_description,
            "filing_index_url": self.filing_index_url,
            "primary_document_url": self.primary_document_url,
            "is_xbrl": self.is_xbrl,
            "is_inline_xbrl": self.is_inline_xbrl,
            "file_number": self.file_number,
            "film_number": self.film_number,
            "size_bytes": self.size_bytes,
            "acceptance_datetime": self.acceptance_datetime,
            "act": self.act,
            "items": self.items,
            "core_type": self.core_type,
            "is_xbrl_numeric": self.is_xbrl_numeric,
        }


def _filing_index_url(cik10: str, accession_number: str) -> str:
    cik_int = str(int(cik10))
    acc = accession_number.replace("-", "")
    return (
        "https://www.sec.gov/cgi-bin/viewer?action=view&cik="
        f"{cik_int}&accession_number={accession_number}&xbrl_type=v"
    )


def fetch_submissions(client: EdgarClient, cik: str | int) -> dict[str, Any]:
    cik10 = format_cik10(cik)
    url = _SUBMISSIONS_URL.format(cik10=cik10)
    return client.get_json(url)


def _recent_columnar_block(data: dict[str, Any]) -> dict[str, list[Any]]:
    """
    Main submissions file nests columnar arrays under filings.recent.
    Supplemental files (e.g. CIK...-submissions-001.json) store the same
    arrays at the top level.
    """
    nested = (data.get("filings") or {}).get("recent")
    if isinstance(nested, dict) and nested.get("form"):
        return nested
    forms = data.get("form")
    if not isinstance(forms, list) or not forms:
        return {}
    n = len(forms)
    out: dict[str, list[Any]] = {}
    for k, v in data.items():
        if isinstance(v, list) and len(v) == n:
            out[k] = v
    return out


def _recent_rows(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    recent = _recent_columnar_block(data)
    if not recent:
        return
    n = len(recent.get("form") or [])
    keys = [k for k in recent if isinstance(recent[k], list)]
    for i in range(n):
        row: dict[str, Any] = {}
        for k in keys:
            col = recent[k]
            if i < len(col):
                row[k] = col[i]
        yield row


def _merge_additional_files(
    client: EdgarClient, data: dict[str, Any]
) -> list[dict[str, Any]]:
    """
    Older filings may live in additional JSON files listed under filings.files.
    """
    out: list[dict[str, Any]] = []
    files = (data.get("filings") or {}).get("files") or []
    name = data.get("name", "")
    cik10 = format_cik10(data.get("cik", ""))
    for entry in files:
        name_part = entry.get("name")
        if not name_part:
            continue
        url = f"https://data.sec.gov/submissions/{name_part}"
        extra = client.get_json(url)
        if "name" not in extra:
            extra["name"] = name
        if "cik" not in extra:
            extra["cik"] = cik10
        out.append(extra)
    return out


def iter_10k_filings(
    client: EdgarClient,
    cik: str | int,
    *,
    include_additional_files: bool = True,
    base_submissions: dict[str, Any] | None = None,
) -> Iterator[TenKFiling]:
    """
    Yield 10-K and 10-K/A filings from the company submissions API, newest first
    within each JSON segment (SEC stores recent filings in columnar form).
    """
    base = base_submissions if base_submissions is not None else fetch_submissions(client, cik)
    cik10 = format_cik10(base.get("cik", cik))
    company_name = str(base.get("name", ""))

    chunks: list[dict[str, Any]] = [base]
    if include_additional_files:
        chunks.extend(_merge_additional_files(client, base))

    for chunk in chunks:
        cik10 = format_cik10(chunk.get("cik", cik10))
        company_name = str(chunk.get("name", company_name))
        for row in _recent_rows(chunk):
            form = row.get("form")
            if form not in _TENK_FORMS:
                continue
            acc = row.get("accessionNumber")
            doc = row.get("primaryDocument")
            if not acc or not doc:
                continue
            sz_raw = row.get("size")
            size_b: int | None
            try:
                size_b = int(sz_raw) if sz_raw is not None else None
            except (TypeError, ValueError):
                size_b = None
            yield TenKFiling(
                cik10=cik10,
                name=company_name,
                form=str(form),
                filing_date=str(row.get("filingDate", "")),
                report_date=row.get("reportDate"),
                accession_number=str(acc),
                primary_document=str(doc),
                primary_doc_description=row.get("primaryDocDescription"),
                filing_index_url=_filing_index_url(cik10, str(acc)),
                primary_document_url=archives_document_url(
                    cik10=cik10,
                    accession_number=str(acc),
                    primary_document=str(doc),
                ),
                is_xbrl=row.get("isXBRL"),
                is_inline_xbrl=row.get("isInlineXBRL"),
                file_number=row.get("fileNumber"),
                film_number=row.get("filmNumber"),
                size_bytes=size_b,
                acceptance_datetime=row.get("acceptanceDateTime"),
                act=row.get("act"),
                items=row.get("items"),
                core_type=row.get("core_type"),
                is_xbrl_numeric=row.get("isXBRLNumeric"),
            )


def first_10k_size_bytes(submissions_data: dict[str, Any]) -> int | None:
    """
    Byte size of the most recently filed 10-K / 10-K/A in ``filings.recent`` only
    (the primary SEC submissions columnar block, newest filings first).
    """
    block = (submissions_data.get("filings") or {}).get("recent") or {}
    forms = block.get("form") or []
    sizes = block.get("size") or []
    for i, f in enumerate(forms):
        if f not in _TENK_FORMS:
            continue
        if i < len(sizes):
            try:
                return int(sizes[i])
            except (TypeError, ValueError):
                return None
    return None


def list_10k_filings(
    client: EdgarClient,
    cik: str | int,
    *,
    limit: int | None = None,
    include_additional_files: bool = True,
    base_submissions: dict[str, Any] | None = None,
) -> list[TenKFiling]:
    rows = iter_10k_filings(
        client,
        cik,
        include_additional_files=include_additional_files,
        base_submissions=base_submissions,
    )
    out: list[TenKFiling] = []
    for f in rows:
        out.append(f)
        if limit is not None and len(out) >= limit:
            break
    return out
