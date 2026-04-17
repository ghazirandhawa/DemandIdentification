#!/usr/bin/env python3
"""
Bronze ``sec_filings`` -> silver ``SEC_filings_opportunities`` using Gemini scoring.

Reads DataPilot context from ``datapilot_docs/``: ``project_scope.txt``, ``DataPilot_ICP_v3.docx``,
and ``Data Pilot Sales Deck Complete.pptx`` (plus table text from the docx and grouped shapes in
the pptx when ``python-docx`` / ``python-pptx`` are installed). Also reads each company’s
``submissions.json`` under ``--store-root`` (country, addresses, website), ``companyfacts_10k.json``
for latest FY USD revenue (non-AI), and formats bronze ``officers_contacts`` as ``Position : Name`` lines. Assigns stable
UUIDs for ``silver_signal_id``, ``silver_company_id``, and ``opportunity_id`` (uuid5) so
re-runs upsert the same rows.

  python pipeline_silver_sec_opportunities.py
  python pipeline_silver_sec_opportunities.py --run-folder 2026-04-16T125501Z --limit 10 --workers 8
  python pipeline_silver_sec_opportunities.py --dry-run

``--recreate-silver-tables`` / ``--recreate-silver-table`` drops and recreates **both** silver
``SEC_filings_opportunities`` and ``SEC_filings_opportunities_insights`` (see ``SILVER_*`` env vars).
It does **not** run or alter the bronze pipeline or ``bronze.sec_filings``.

Requires ``DATABASE_URL`` to read bronze. ``--dry-run`` calls Gemini and prints JSON but does not
write silver. See ``.env.example`` for ``SILVER_SCHEMA``, ``SILVER_SEC_OPPORTUNITIES_TABLE``,
``GEMINI_API_KEY``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from tqdm.auto import tqdm

_ROOT = Path(__file__).resolve().parent
_ds = _ROOT / "data_sources"
if _ds.is_dir() and str(_ds) not in sys.path:
    sys.path.insert(0, str(_ds))

load_dotenv(_ROOT / ".env")

from SEC_DATA.paths import DEFAULT_STORE_ROOT  # noqa: E402

from sec_filings_pipeline.datapilot_context import load_datapilot_bundle  # noqa: E402
from sec_filings_pipeline.gemini_extract import configure_gemini  # noqa: E402
from sec_filings_pipeline.gemini_sec_scoring import (  # noqa: E402
    OPPORTUNITIES_PROMPT_VERSION,
    format_bronze_block,
    score_opportunity_with_gemini,
    truncate_block,
)
from sec_filings_pipeline.companyfacts_revenue import (  # noqa: E402
    load_latest_revenue_usd_from_disk,
)
from sec_filings_pipeline.submissions_registry import (  # noqa: E402
    format_officers_position_name,
    load_registry_fields_from_disk,
)
from sec_filings_pipeline.silver_sec_db import (  # noqa: E402
    connect,
    ensure_silver_sec_tables,
    fetch_bronze_sec_rows,
    fetch_existing_silver_bronze_keys,
    upsert_sec_opportunities_batch,
)


def _stable_ids(*, run_folder: str, ticker: str, cik10: str | None) -> tuple[str, str, str]:
    base = f"datapilot|sec|v1|{run_folder}|{ticker}"
    signal = uuid.uuid5(uuid.NAMESPACE_URL, base + "|signal")
    company = uuid.uuid5(uuid.NAMESPACE_URL, base + "|company|" + (cik10 or ""))
    opp = uuid.uuid5(uuid.NAMESPACE_URL, base + "|opportunity")
    return str(signal), str(company), str(opp)


def _edgar_company_url(cik10: str | None) -> str | None:
    if not cik10:
        return None
    digits = "".join(ch for ch in str(cik10) if ch.isdigit())
    if not digits:
        return None
    padded = digits.zfill(10)
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK="
        f"{padded}&type=10-K"
    )


def _parse_signal_date(v: object) -> object:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class _OppWorkerCtx:
    ctx_text: str
    meta_note: str
    model_name: str
    store_root: Path
    write_silver: bool
    skip_existing: bool
    max_bronze_chars: int
    existing_silver_keys: frozenset[tuple[str, str]]


def _process_one_opportunity_row(row: dict[str, object], wctx: _OppWorkerCtx) -> dict[str, object]:
    """
    Process a single bronze row (no DB writes here when ``write_silver``; main thread batches upserts).

    Returns a result dict with ``status`` in ``ok`` | ``skip`` | ``fail`` | ``dry_ok``.
    """
    rf = str(row.get("run_folder") or "")
    tk = str(row.get("ticker") or "")
    out: dict[str, object] = {"status": "fail", "ticker": tk}

    if wctx.skip_existing and (rf, tk) in wctx.existing_silver_keys:
        out["status"] = "skip"
        return out

    bronze_raw = format_bronze_block(row)
    bronze_block = truncate_block(bronze_raw, wctx.max_bronze_chars)
    scored, note = score_opportunity_with_gemini(
        datapilot_context=wctx.ctx_text,
        bronze_block=bronze_block,
        context_meta_note=wctx.meta_note,
    )
    if not scored or scored.get("_parse_note"):
        out["status"] = "fail"
        out["gemini_note"] = note
        out["parse_note"] = scored.get("_parse_note")
        return out

    cik = row.get("cik10")
    cik10 = str(cik) if cik is not None else None
    signal_id, company_id, opp_id = _stable_ids(run_folder=rf, ticker=tk, cik10=cik10)
    company_name = (row.get("company_name") or tk or "Unknown").strip()

    su = scored.get("source_url")
    source_url = str(su).strip() if su else None
    if not source_url:
        source_url = _edgar_company_url(cik10)
    signal_date = _parse_signal_date(scored.get("signal_date"))

    reg = load_registry_fields_from_disk(
        wctx.store_root, run_folder=rf, ticker=tk, cik10=cik10
    )
    contacts_summary = format_officers_position_name(row.get("officers_contacts"))
    latest_revenue_usd = load_latest_revenue_usd_from_disk(
        wctx.store_root, run_folder=rf, ticker=tk, cik10=cik10
    )

    payload_preview = {
        "opportunity_id": opp_id,
        "silver_signal_id": signal_id,
        "silver_company_id": company_id,
        "ticker": tk,
        "gemini_note": note,
        "registry_submissions": reg,
        "contacts_summary": contacts_summary,
        "latest_revenue_usd": latest_revenue_usd,
        "scored": {k: scored[k] for k in scored if k != "raw_response_excerpt"},
    }

    if not wctx.write_silver:
        out["status"] = "dry_ok"
        out["payload"] = payload_preview
        return out

    out["db_row"] = {
        "opportunity_id": opp_id,
        "silver_signal_id": signal_id,
        "silver_company_id": company_id,
        "bronze_run_folder": rf,
        "bronze_ticker": tk,
        "cik10": cik10,
        "company_name": company_name,
        "company_industry": scored.get("company_industry"),
        "geography": scored.get("geography"),
        "signal_display": scored.get("signal_display") or "Strategic Signal",
        "signal_source": scored.get("signal_source") or "Strategic",
        "problem_type": scored.get("problem_type") or "Data Engineering",
        "icp_score": int(scored.get("icp_score") or 0),
        "priority": scored.get("priority") or "Medium",
        "stage": scored.get("stage") or "Signals Identified",
        "opportunity_kind": scored.get("opportunity_kind"),
        "signal_title": scored.get("signal_title") or f"{tk} SEC signal",
        "signal_description": scored.get("signal_description") or "",
        "source_url": source_url,
        "signal_date": signal_date,
        "scoring_notes": scored.get("scoring_notes"),
        "evidence_quotes": scored.get("evidence_quotes"),
        "gemini_model": wctx.model_name,
        "prompt_version": OPPORTUNITIES_PROMPT_VERSION,
        "country": reg.get("country"),
        "mailing_address": reg.get("mailing_address"),
        "website": reg.get("website"),
        "business_address": reg.get("business_address"),
        "contacts_summary": contacts_summary,
        "latest_revenue_usd": latest_revenue_usd,
    }
    out["status"] = "ok"
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Score bronze SEC rows into silver SEC opportunities.")
    p.add_argument("--run-folder", default="", help="Filter bronze.run_folder.")
    p.add_argument(
        "--tickers",
        default="",
        help="Comma-separated tickers to include (optional).",
    )
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--skip-existing", action="store_true", help="Skip rows already in silver.")
    p.add_argument("--max-bronze-chars", type=int, default=72_000)
    p.add_argument(
        "--max-context-chars",
        type=int,
        default=None,
        help="Legacy: same character cap for scope, ICP docx, and sales deck each.",
    )
    p.add_argument(
        "--max-datapilot-total",
        type=int,
        default=120_000,
        help="Max characters for the combined DataPilot context block sent to Gemini.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Call Gemini and print scored JSON; do not write silver.",
    )
    p.add_argument(
        "--recreate-silver-tables",
        "--recreate-silver-table",
        action="store_true",
        dest="recreate_silver_tables",
        help="Drop and recreate **both** silver SEC_filings_opportunities and "
        "SEC_filings_opportunities_insights (destructive). Does not run or modify bronze.",
    )
    p.add_argument(
        "--store-root",
        type=Path,
        default=None,
        help="EDGAR sync root (default: data_sources/edgar_disclosures). Used to find "
        "<run>/<ticker>/submissions.json.",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel worker threads (default 4). DB writes use a single connection on the main thread.",
    )
    p.add_argument(
        "--db-batch-size",
        type=int,
        default=100,
        metavar="N",
        help="Upsert silver opportunities in transactions of N rows (default 100). Use 1 for per-row commits.",
    )
    p.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable tqdm progress bar.",
    )
    args = p.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] or None

    bundle = load_datapilot_bundle(
        repo_root=_ROOT,
        max_chars_per_source=args.max_context_chars,
        max_total_chars=args.max_datapilot_total,
    )
    ctx_text = bundle.get("text") or ""
    meta = bundle.get("meta") or {}
    meta_note = json.dumps(meta, ensure_ascii=False)

    configure_gemini()
    model_name = (os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash").strip()

    store_root = (args.store_root or DEFAULT_STORE_ROOT).resolve()

    conn = connect()
    rows = fetch_bronze_sec_rows(
        conn,
        run_folder=args.run_folder or None,
        tickers=tickers,
        offset=args.offset,
        limit=args.limit,
    )
    write_silver = not args.dry_run
    if write_silver:
        ensure_silver_sec_tables(
            conn,
            recreate=args.recreate_silver_tables,
            recreate_insights_only=False,
        )
    elif args.recreate_silver_tables:
        tqdm.write(
            "Note: --recreate-silver-tables/--recreate-silver-table ignored when --dry-run is set.",
            file=sys.stderr,
        )

    existing_keys: frozenset[tuple[str, str]] = frozenset()
    if write_silver and args.skip_existing and rows:
        run_folders = sorted(
            {str(r.get("run_folder") or "").strip() for r in rows if str(r.get("run_folder") or "").strip()}
        )
        existing_keys = frozenset(fetch_existing_silver_bronze_keys(conn, run_folders=run_folders))
    conn.close()

    if not rows:
        print("No bronze rows matched filters.")
        return 0

    workers = max(1, int(args.workers))
    db_batch = max(1, int(args.db_batch_size))
    wctx = _OppWorkerCtx(
        ctx_text=ctx_text,
        meta_note=meta_note,
        model_name=model_name,
        store_root=store_root,
        write_silver=write_silver,
        skip_existing=bool(args.skip_existing),
        max_bronze_chars=args.max_bronze_chars,
        existing_silver_keys=existing_keys,
    )

    ok = 0
    failed = 0
    dry_payloads: list[dict[str, object]] = []
    pending_upserts: list[dict[str, object]] = []
    write_conn = None
    if write_silver:
        write_conn = connect()

    def _maybe_flush_opportunities(*, force: bool = False) -> None:
        if write_conn is None:
            return
        if force:
            if pending_upserts:
                upsert_sec_opportunities_batch(write_conn, pending_upserts)
                pending_upserts.clear()
            return
        if len(pending_upserts) >= db_batch:
            upsert_sec_opportunities_batch(write_conn, pending_upserts)
            pending_upserts.clear()

    def _handle_opp_result(r: dict[str, object]) -> None:
        nonlocal ok, failed
        st = str(r.get("status") or "")
        if st == "ok" or st == "skip":
            ok += 1
            if st == "ok" and write_silver and r.get("db_row") is not None:
                pending_upserts.append(r["db_row"])  # type: ignore[arg-type]
                _maybe_flush_opportunities()
        elif st == "dry_ok":
            dry_payloads.append(r["payload"])  # type: ignore[arg-type]
            ok += 1
        else:
            failed += 1
            tqdm.write(
                f"[skip {r.get('ticker')}] gemini: {r.get('gemini_note')} parse={r.get('parse_note')}",
                file=sys.stderr,
            )

    try:
        if workers == 1:
            it = rows
            if args.no_progress:
                for row in it:
                    r = _process_one_opportunity_row(row, wctx)
                    _handle_opp_result(r)
            else:
                for row in tqdm(it, total=len(rows), desc="silver_opportunities"):
                    r = _process_one_opportunity_row(row, wctx)
                    _handle_opp_result(r)
        else:
            pbar = (
                None
                if args.no_progress
                else tqdm(total=len(rows), desc="silver_opportunities", unit="row")
            )
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(_process_one_opportunity_row, row, wctx): row for row in rows}
                for fut in as_completed(futs):
                    r = fut.result()
                    if pbar is not None:
                        pbar.update(1)
                    _handle_opp_result(r)
            if pbar is not None:
                pbar.close()
        _maybe_flush_opportunities(force=True)
    finally:
        if write_conn is not None:
            write_conn.close()

    if not write_silver:
        for pl in sorted(dry_payloads, key=lambda x: str(x.get("ticker", ""))):
            print(json.dumps(pl, ensure_ascii=False, indent=2))

    print(f"Done. upserted_or_skipped={ok} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
