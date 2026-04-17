from __future__ import annotations

import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from requests import HTTPError
from requests.exceptions import RequestException

from .archives_index import archives_file_url, ranked_index_pdf_names
from .client import EdgarClient
from .facts_filter import (
    TENK_FORMS,
    filter_companyfacts_by_forms_and_filed_on_or_after,
)
from .filing_window import (
    filing_date_on_or_after,
    narrative_concept_filter,
    resolve_rolling_earliest,
    trim_filings_recent_in_place,
)
from .narrative_ixbrl import extract_ix_nonnumeric_narratives, filter_narrative_blocks
from .paths import (
    DEFAULT_STORE_ROOT,
    company_folder_slug,
    ensure_company_output_dir,
    utc_run_folder_name,
)
from .progress import progress_finish_line, write_bulk_save_progress, write_pull_filing_progress
from .revenue_filter import DEFAULT_MAX_REVENUE_USD, revenue_exceeds_threshold
from .submissions import (
    fetch_submissions,
    first_10k_size_bytes,
    format_cik10,
    list_10k_filings,
    primary_document_saved_basename,
)
from .tickers import iter_ticker_rows
from .xbrl import fetch_company_facts

_tls = threading.local()


def _thread_local_client(user_agent: str) -> EdgarClient:
    if getattr(_tls, "client", None) is None or getattr(_tls, "ua", None) != user_agent:
        _tls.ua = user_agent
        _tls.client = EdgarClient(user_agent=user_agent)
    return _tls.client


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_filename_component(name: str, max_len: int = 140) -> str:
    s = re.sub(r"[^\w.\-]+", "_", (name or "").strip())
    return (s or "file")[:max_len]


def _log_fetch_skip(message: str, *, ticker: str, cik10: str, extra: str = "") -> None:
    tail = f" {extra}" if extra else ""
    print(f"[SEC_DATA] {message} (ticker={ticker} cik={cik10}){tail}", file=sys.stderr, flush=True)


def sync_company(
    client: EdgarClient,
    cik: str | int,
    store_root: Path | None = None,
    *,
    run_folder: str | None = None,
    submissions_base: dict[str, Any] | None = None,
    ticker: str | None = None,
    company_title: str | None = None,
    years: int | None = None,
    quarters: int | None = 8,
    include_full_companyfacts: bool = False,
    download_tenk_primary_documents: bool = True,
    download_index_pdfs: bool = True,
    tenk_index_limit: int = 50,
    include_supplemental_submissions: bool = False,
    export_narrative_ixbrl: bool = True,
    companyfacts_base: dict[str, Any] | None = None,
    progress_stream: TextIO | None = None,
) -> dict[str, Any]:
    """
    Store submissions (trimmed), 10-K index, companyfacts (10-K + date window),
    consolidated narrative JSON, and primary 10-K documents under a timestamped run folder.

    Layout: ``{store_root}/{UTC_RUN}/{TICKER}/`` with ``ALL_NARRATIVE_INFO.json``
    (when narrative export is on) and ``tenk_documents/`` for primary filings
    and any PDFs listed in each filing's ``index.json``.
    """
    root = store_root or DEFAULT_STORE_ROOT
    cik10 = format_cik10(cik)
    run = run_folder if run_folder is not None else utc_run_folder_name()
    earliest, window_label = resolve_rolling_earliest(years=years, quarters=quarters)
    folder = company_folder_slug(ticker, cik10)
    d = ensure_company_output_dir(root, run, folder)

    base_raw = submissions_base if submissions_base is not None else fetch_submissions(client, cik10)
    base_trim = trim_filings_recent_in_place(base_raw, earliest)
    write_json(d / "submissions.json", base_trim)

    tenks_all = list_10k_filings(
        client,
        cik10,
        limit=tenk_index_limit,
        include_additional_files=include_supplemental_submissions,
        base_submissions=base_trim,
    )
    tenks = [t for t in tenks_all if filing_date_on_or_after(t.filing_date, earliest)]
    write_json(d / "tenk_filings.json", [t.to_dict() for t in tenks])

    plabel = (ticker or "").strip().upper() or cik10
    if progress_stream is not None:
        if not tenks:
            progress_stream.write(f"\rpull {plabel} — no 10-K filings in {window_label} window\n")
            progress_stream.flush()

    facts: dict[str, Any] = {}
    if companyfacts_base is not None:
        facts = companyfacts_base
    else:
        try:
            facts = fetch_company_facts(client, cik10)
        except HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
    if include_full_companyfacts and facts:
        full_trim = filter_companyfacts_by_forms_and_filed_on_or_after(
            facts, TENK_FORMS, earliest
        )
        write_json(d / "companyfacts_full.json", full_trim)
    slim = (
        filter_companyfacts_by_forms_and_filed_on_or_after(facts, TENK_FORMS, earliest)
        if facts
        else {"cik": cik10, "entityName": None, "facts": {}}
    )
    write_json(d / "companyfacts_10k.json", slim)

    doc_dir = d / "tenk_documents"
    doc_dir.mkdir(parents=True, exist_ok=True)
    narrative_filings: list[dict[str, Any]] = []
    primary_saved = 0
    pdf_saved = 0
    all_narrative_filings: list[dict[str, Any]] = []
    fetch_warnings: list[dict[str, Any]] = []
    n_tenk = len(tenks)
    for i, t in enumerate(tenks, start=1):
        if progress_stream is not None and n_tenk:
            write_pull_filing_progress(
                progress_stream,
                label=plabel,
                filing_index=i,
                filing_total=n_tenk,
                accession=t.accession_number,
            )
        acc_nd = t.accession_number.replace("-", "")
        need_body = download_tenk_primary_documents or export_narrative_ixbrl
        raw: bytes = b""
        if need_body:
            try:
                raw = client.get_bytes(t.primary_document_url)
            except RequestException as exc:
                w = {
                    "stage": "primary_document",
                    "accession_number": t.accession_number,
                    "primary_document": t.primary_document,
                    "url": t.primary_document_url,
                    "error": repr(exc),
                }
                fetch_warnings.append(w)
                _log_fetch_skip(
                    "skipped filing after primary document download failed",
                    ticker=plabel,
                    cik10=cik10,
                    extra=f"accession={t.accession_number} {exc!s}",
                )
                continue
        saved_name: str | None = None
        if download_tenk_primary_documents and raw:
            saved_name = primary_document_saved_basename(t)
            primary_path = doc_dir / saved_name
            if raw[:4] == b"%PDF" or t.primary_document.lower().endswith(".pdf"):
                primary_path.write_bytes(raw)
            else:
                primary_path.write_text(
                    raw.decode("utf-8", errors="replace"), encoding="utf-8", errors="replace"
                )
            primary_saved += 1

        text_for_ix = raw.decode("utf-8", errors="replace") if raw else ""
        if export_narrative_ixbrl and raw:
            blocks = extract_ix_nonnumeric_narratives(text_for_ix)
            slim_blocks = filter_narrative_blocks(blocks, concept_filter=narrative_concept_filter)
            filing_entry = {
                "form": t.form,
                "accession_number": t.accession_number,
                "filing_date": t.filing_date,
                "report_date": t.report_date,
                "primary_document": t.primary_document,
                "primary_document_saved": saved_name,
                "concepts": [
                    {"concept": b["concept"], "text": b["text"], "char_count": b["char_count"]}
                    for b in slim_blocks
                ],
            }
            all_narrative_filings.append(filing_entry)
            narrative_filings.append(
                {
                    "form": t.form,
                    "accession_number": t.accession_number,
                    "filing_date": t.filing_date,
                    "concept_count": len(slim_blocks),
                }
            )

        if download_index_pdfs:
            try:
                pdf_names = ranked_index_pdf_names(
                    client,
                    cik10=cik10,
                    accession_number=t.accession_number,
                    primary_document=t.primary_document,
                )
            except RequestException as exc:
                w = {
                    "stage": "filing_index_json",
                    "accession_number": t.accession_number,
                    "error": repr(exc),
                }
                fetch_warnings.append(w)
                _log_fetch_skip(
                    "skipped index.json for filing (no extra PDFs this pass)",
                    ticker=plabel,
                    cik10=cik10,
                    extra=f"accession={t.accession_number} {exc!s}",
                )
                pdf_names = []
            for pdf_name in pdf_names:
                if pdf_name == t.primary_document:
                    continue
                try:
                    purl = archives_file_url(
                        cik10=cik10, accession_number=t.accession_number, filename=pdf_name
                    )
                    pdf_bytes = client.get_bytes(purl)
                except RequestException as exc:
                    w = {
                        "stage": "index_pdf",
                        "accession_number": t.accession_number,
                        "pdf_name": pdf_name,
                        "url": purl,
                        "error": repr(exc),
                    }
                    fetch_warnings.append(w)
                    _log_fetch_skip(
                        "skipped index PDF download",
                        ticker=plabel,
                        cik10=cik10,
                        extra=f"{pdf_name} accession={t.accession_number} {exc!s}",
                    )
                    continue
                pdf_base = f"{t.filing_date}_{acc_nd}_{_safe_filename_component(pdf_name)}"
                (doc_dir / pdf_base).write_bytes(pdf_bytes)
                pdf_saved += 1

    if export_narrative_ixbrl and all_narrative_filings:
        write_json(
            d / "ALL_NARRATIVE_INFO.json",
            {
                "schema": "all_narrative_info_v1",
                "cik10": cik10,
                "ticker": (ticker or "").strip().upper(),
                "window_label": window_label,
                "earliest_date_inclusive": earliest.isoformat(),
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "filings": all_narrative_filings,
            },
        )

    meta = {
        "cik10": cik10,
        "run_folder": run,
        "store_folder": folder,
        "ticker": ticker,
        "company_title": company_title,
        "rolling_window_label": window_label,
        "rolling_window_years": years,
        "rolling_window_quarters": quarters if years is None else None,
        "earliest_date_inclusive": earliest.isoformat(),
        "synced_at_utc": datetime.now(timezone.utc).isoformat(),
        "sec_api_note": (
            "data.sec.gov companyfacts are XBRL fact tables; Item 1 / 1A / 7 narrative "
            "is not available as a separate JSON resource. Narrative excerpts are parsed "
            "from the primary 10-K HTML (ix:nonNumeric)."
        ),
        "paths": {
            "submissions": "submissions.json",
            "tenk_filings": "tenk_filings.json",
            "companyfacts_10k": "companyfacts_10k.json",
            "companyfacts_full": "companyfacts_full.json" if include_full_companyfacts and facts else None,
            "tenk_documents_dir": "tenk_documents/",
            "all_narrative_info": "ALL_NARRATIVE_INFO.json" if export_narrative_ixbrl and all_narrative_filings else None,
            "primary_documents_saved": primary_saved,
            "index_pdf_saved": pdf_saved,
        },
        "fetch_warnings": fetch_warnings,
        "narrative_filings": narrative_filings,
    }
    write_json(d / "sync_meta.json", meta)

    if progress_stream is not None and n_tenk:
        progress_finish_line(progress_stream)

    return {
        "cik10": cik10,
        "ticker": (ticker or "").strip().upper(),
        "run_folder": run,
        "store_root": str(root),
        "store_folder": folder,
        "dir": str(d),
        "tenk_rows": len(tenks),
        "facts_tags": sum(len(v) for v in (slim.get("facts") or {}).values()),
        "tenk_primary_saved": primary_saved,
        "tenk_index_pdfs_saved": pdf_saved,
        "narrative_filing_count": len(narrative_filings),
        "fetch_warnings": fetch_warnings,
    }


def _fetch_companyfacts_job(user_agent: str, cik10: str) -> dict[str, Any]:
    c = _thread_local_client(user_agent)
    try:
        return fetch_company_facts(c, cik10)
    except HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return {}
        raise


def _fetch_submissions_job(user_agent: str, row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("cik_str")
    if raw is None:
        return {"kind": "skip", "skip": {"reason": "bad_row"}}
    cik10 = str(int(raw)).zfill(10)
    ticker = str(row.get("ticker", "")).strip().upper()
    title = str(row.get("title", "")).strip()
    try:
        c = _thread_local_client(user_agent)
        sub = fetch_submissions(c, cik10)
    except HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        return {
            "kind": "skip",
            "skip": {"cik10": cik10, "ticker": ticker, "reason": "http_error", "status": code},
        }
    return {
        "kind": "ok",
        "cik10": cik10,
        "ticker": ticker,
        "title": title,
        "row": row,
        "sub": sub,
    }


def _sync_company_job(
    user_agent: str,
    store_root: Path,
    meta: dict[str, Any],
    sub: dict[str, Any],
    *,
    run_folder: str,
    include_full_companyfacts: bool,
    download_tenk_primary_documents: bool,
    download_index_pdfs: bool,
    tenk_index_limit: int,
    years: int | None,
    quarters: int | None,
    export_narrative_ixbrl: bool,
    include_supplemental_submissions: bool,
    companyfacts_base: dict[str, Any] | None,
) -> dict[str, Any]:
    c = _thread_local_client(user_agent)
    return sync_company(
        c,
        meta["cik10"],
        store_root,
        run_folder=run_folder,
        submissions_base=sub,
        ticker=meta.get("ticker"),
        company_title=meta.get("title"),
        years=years,
        quarters=quarters,
        include_full_companyfacts=include_full_companyfacts,
        download_tenk_primary_documents=download_tenk_primary_documents,
        download_index_pdfs=download_index_pdfs,
        tenk_index_limit=tenk_index_limit,
        include_supplemental_submissions=include_supplemental_submissions,
        export_narrative_ixbrl=export_narrative_ixbrl,
        companyfacts_base=companyfacts_base,
    )


def bulk_sync_from_tickers(
    client: EdgarClient,
    store_root: Path | None = None,
    *,
    exclude_cik10: frozenset[str],
    max_companies: int,
    max_latest_10k_bytes: int | None = None,
    include_full_companyfacts: bool = False,
    download_tenk_primary_documents: bool = True,
    download_index_pdfs: bool = True,
    tenk_index_limit: int = 50,
    progress_every: int = 50,
    max_workers: int = 8,
    years: int | None = None,
    quarters: int | None = 8,
    export_narrative_ixbrl: bool = True,
    include_supplemental_submissions: bool = False,
    skip_revenue_size_filter: bool = False,
    max_revenue_usd: float = DEFAULT_MAX_REVENUE_USD,
    progress_stream: TextIO | None = None,
) -> dict[str, Any]:
    root = store_root or DEFAULT_STORE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    shared_run = utc_run_folder_name()
    (root / shared_run).mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    n_seen = 0
    ua = client.user_agent
    rows_iter = iter(iter_ticker_rows(client))
    saved_lock = threading.Lock()
    workers = max(1, min(32, max_workers))
    batch_limit = max(workers * 4, workers + 8)
    effective_revenue_cap = None if skip_revenue_size_filter else float(max_revenue_usd)
    progress_emitted = False

    while len(saved) < max_companies:
        batch_rows: list[dict[str, Any]] = []
        while len(batch_rows) < batch_limit:
            try:
                row = next(rows_iter)
            except StopIteration:
                break
            n_seen += 1
            raw = row.get("cik_str")
            if raw is None:
                continue
            cik10 = str(int(raw)).zfill(10)
            ticker = str(row.get("ticker", "")).strip().upper()
            if cik10 in exclude_cik10:
                skipped.append({"cik10": cik10, "ticker": ticker, "reason": "excluded_cik"})
                continue
            batch_rows.append(row)

        if not batch_rows:
            break

        fetch_results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_fetch_submissions_job, ua, r) for r in batch_rows]
            for fut in futures:
                fetch_results.append(fut.result())

        sync_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for fr in fetch_results:
            if fr["kind"] == "skip":
                skipped.append(fr["skip"])
                continue
            cik10 = fr["cik10"]
            ticker = fr["ticker"]
            sub = fr["sub"]
            sz = first_10k_size_bytes(sub)
            if sz is None:
                skipped.append({"cik10": cik10, "ticker": ticker, "reason": "no_10k_in_recent"})
                continue
            if max_latest_10k_bytes is not None and sz > max_latest_10k_bytes:
                skipped.append(
                    {
                        "cik10": cik10,
                        "ticker": ticker,
                        "reason": "latest_10k_too_large",
                        "size_bytes": sz,
                    }
                )
                continue
            meta = {"cik10": cik10, "ticker": ticker, "title": fr["title"]}
            sync_candidates.append((meta, sub))

        need = max_companies - len(saved)
        to_sync = sync_candidates[:need]
        if not to_sync:
            continue

        to_run: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]] = []
        if effective_revenue_cap is not None:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                fut_map = {
                    ex.submit(_fetch_companyfacts_job, ua, meta["cik10"]): (meta, sub)
                    for meta, sub in to_sync
                }
                for fut, (meta, sub) in fut_map.items():
                    facts = fut.result()
                    excl, rev = revenue_exceeds_threshold(facts, effective_revenue_cap)
                    if excl:
                        skipped.append(
                            {
                                "cik10": meta["cik10"],
                                "ticker": meta.get("ticker", ""),
                                "reason": "revenue_above_threshold",
                                "revenue_usd": rev,
                                "max_revenue_usd": effective_revenue_cap,
                            }
                        )
                        continue
                    to_run.append((meta, sub, facts))
        else:
            to_run = [(meta, sub, None) for meta, sub in to_sync]

        if not to_run:
            continue

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [
                ex.submit(
                    _sync_company_job,
                    ua,
                    root,
                    meta,
                    sub,
                    run_folder=shared_run,
                    include_full_companyfacts=include_full_companyfacts,
                    download_tenk_primary_documents=download_tenk_primary_documents,
                    download_index_pdfs=download_index_pdfs,
                    tenk_index_limit=tenk_index_limit,
                    years=years,
                    quarters=quarters,
                    export_narrative_ixbrl=export_narrative_ixbrl,
                    include_supplemental_submissions=include_supplemental_submissions,
                    companyfacts_base=facts,
                )
                for meta, sub, facts in to_run
            ]
            for fut in as_completed(futures):
                info = fut.result()
                with saved_lock:
                    if len(saved) < max_companies:
                        saved.append(info)
                        if progress_stream is not None:
                            write_bulk_save_progress(
                                progress_stream,
                                saved=len(saved),
                                saved_goal=max_companies,
                                rows_seen=n_seen,
                                ticker=str(info.get("ticker") or ""),
                                cik10=str(info.get("cik10", "")),
                            )
                            progress_emitted = True
                        elif progress_every > 0 and len(saved) % progress_every == 0:
                            print(f"synced {len(saved)} companies…", flush=True)

    if progress_stream is not None and progress_emitted:
        progress_finish_line(progress_stream)

    report = {
        "store_root": str(root),
        "run_folder": shared_run,
        "max_companies": max_companies,
        "saved_count": len(saved),
        "skipped_count": len(skipped),
        "tickers_rows_seen": n_seen,
        "max_workers": workers,
        "rolling_window_years": years,
        "rolling_window_quarters": quarters if years is None else None,
        "max_revenue_usd": effective_revenue_cap,
        "skip_revenue_size_filter": skip_revenue_size_filter,
        "saved": saved,
        "skipped": skipped,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(root / shared_run / "last_bulk_sync_report.json", report)
    return report
