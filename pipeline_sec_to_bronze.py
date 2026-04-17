#!/usr/bin/env python3
"""
Process the latest EDGAR sync run: public float from ``companyfacts_10k.json``, selected
10-K / 10-K/A primary document (latest annual by filing date), keyword and
signal-tag hits, LLM bundle (OpenAI or Gemini via ``BRONZE_LLM_PROVIDER``), CSV, and Postgres
``bronze.sec_filings`` (schema/table configurable via ``BRONZE_SCHEMA`` / ``BRONZE_TABLE``).

  python pipeline_sec_to_bronze.py
  python pipeline_sec_to_bronze.py --run 2026-04-16T125501Z --limit 5 --no-db --workers 8
  python pipeline_sec_to_bronze.py --offset 100 --limit 100
  python pipeline_sec_to_bronze.py --recreate-bronze-table

Requires LLM credentials: set ``GEMINI_API_KEY`` and/or ``OPENAI_API_KEY``. If
``BRONZE_LLM_PROVIDER`` is unset, only-Gemini keys use Gemini; only-OpenAI keys use OpenAI; if
both are set, OpenAI is used unless you set ``BRONZE_LLM_PROVIDER=gemini``. Also needs
``DATABASE_URL`` unless ``--no-db``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from tqdm.auto import tqdm

_ROOT = Path(__file__).resolve().parent
_ds = _ROOT / "data_sources"
if _ds.is_dir() and str(_ds) not in sys.path:
    sys.path.insert(0, str(_ds))

load_dotenv(_ROOT / ".env")

from SEC_DATA.paths import DEFAULT_STORE_ROOT  # noqa: E402
from sec_filings_pipeline.bronze_db import (  # noqa: E402
    bronze_table_fqn,
    connect,
    ensure_table,
    qualified_bronze_table_sql,
    upsert_rows_batch,
)
from sec_filings_pipeline.bronze_llm import (  # noqa: E402
    configure_llm,
    extract_bronze_bundle,
    extract_pdf_text,
    llm_provider,
)
from sec_filings_pipeline.company_meta import entity_name_from_companyfacts  # noqa: E402
from sec_filings_pipeline.disclosure_select import (  # noqa: E402
    narrative_blob_for_selected_filings,
    select_bronze_disclosures,
)
from sec_filings_pipeline.keyword_hits import find_keyword_hits  # noqa: E402
from sec_filings_pipeline.local_text import load_filing_path_as_text  # noqa: E402
from sec_filings_pipeline.signal_keywords import evaluate_signal_hits, signal_hits_as_csv  # noqa: E402
from sec_filings_pipeline.bronze_batch import LlmApiAbortController, llm_transport_failure_in_notes  # noqa: E402
from sec_filings_pipeline.market_cap_facts import extract_public_float_usd  # noqa: E402
from sec_filings_pipeline.run_discovery import iter_company_dirs, latest_run_dir  # noqa: E402


def _resolve_run_dir(store_root: Path, run: str | None) -> Path:
    if run:
        p = store_root / run
        if not p.is_dir():
            raise SystemExit(f"Run directory not found: {p}")
        return p
    latest = latest_run_dir(store_root)
    if latest is None:
        raise SystemExit(f"No timestamped run folders under {store_root}")
    return latest


def process_company(run_dir: Path, company_dir: Path) -> dict[str, object]:
    run_folder = run_dir.name
    ticker = company_dir.name
    meta_path = company_dir / "sync_meta.json"
    meta: dict[str, object] = {}
    cik10 = ""
    sync_title = ""
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            cik10 = str(meta.get("cik10") or "").strip()
            sync_title = str(meta.get("company_title") or "").strip()
        except (OSError, json.JSONDecodeError):
            pass

    facts_path = company_dir / "companyfacts_10k.json"
    entity_name = entity_name_from_companyfacts(facts_path)
    cap_val, cap_src = extract_public_float_usd(facts_path)

    paths, targets, sel_note = select_bronze_disclosures(company_dir)
    html_sections: list[str] = []
    pdf_notes: list[str] = []
    pdf_sections: list[str] = []
    for p in paths:
        if p.suffix.lower() == ".pdf":
            try:
                txt, note = extract_pdf_text(p)
            except RuntimeError as exc:
                pdf_notes.append(f"{p.name}:{exc}")
                continue
            pdf_notes.append(f"{p.name}:{note}")
            if txt:
                pdf_sections.append(f"=== {p.name} ===\n{txt}")
        else:
            ht = load_filing_path_as_text(p)
            if ht:
                html_sections.append(f"=== {p.name} ===\n{ht}")

    narr_blob = narrative_blob_for_selected_filings(company_dir, targets)
    htm_blob = "\n\n".join(html_sections)
    pdf_blob = "\n\n".join(pdf_sections)

    combined = "\n\n".join([narr_blob, htm_blob, pdf_blob]).strip()

    hits = find_keyword_hits(combined)
    hits_csv = "|".join(hits)
    sig_eval = evaluate_signal_hits(combined)

    bundle, bundle_note = extract_bronze_bundle(combined)
    pdf_notes.append(f"bronze_bundle_llm:{bundle_note};disclosure_select:{sel_note}")

    llm_name = bundle.get("company_name")
    if isinstance(llm_name, str) and llm_name.strip():
        company_name = llm_name.strip()
    else:
        company_name = (entity_name or sync_title or ticker or "").strip() or None

    officers_contacts: dict[str, object] = {
        "officers": bundle.get("officers") or [],
        "other_contacts": bundle.get("other_contacts") or [],
    }

    return {
        "run_folder": run_folder,
        "ticker": ticker,
        "cik10": cik10 or None,
        "company_name": company_name,
        "public_float_usd": cap_val,
        "public_float_source": cap_src,
        "relevance_keywords": hits_csv,
        "signal_hits_pain": signal_hits_as_csv(sig_eval, "signal_hits_pain"),
        "signal_hits_strategic": signal_hits_as_csv(sig_eval, "signal_hits_strategic"),
        "signal_hits_vertical": signal_hits_as_csv(sig_eval, "signal_hits_vertical"),
        "tags_business": signal_hits_as_csv(sig_eval, "tags_business"),
        "tags_products": signal_hits_as_csv(sig_eval, "tags_products"),
        "tags_strategy": signal_hits_as_csv(sig_eval, "tags_strategy"),
        "tags_risk": signal_hits_as_csv(sig_eval, "tags_risk"),
        "description_business_operations": str(bundle.get("description_business_operations") or ""),
        "description_products_services": str(bundle.get("description_products_services") or ""),
        "description_strategy_outlook": str(bundle.get("description_strategy_outlook") or ""),
        "description_risk_summary": str(bundle.get("description_risk_summary") or ""),
        "officers_contacts": officers_contacts,
        "pdf_gemini_notes": ";".join(pdf_notes)[:8000],
        "combined_text_chars": len(combined),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Bronze pipeline: latest SEC run → CSV + Postgres.")
    p.add_argument(
        "--store-root",
        type=Path,
        default=DEFAULT_STORE_ROOT,
        help="Root that contains timestamped run folders (default: data_sources/edgar_disclosures).",
    )
    p.add_argument(
        "--run",
        default="",
        help="Explicit run folder name (e.g. 2026-04-16T125501Z). Default: latest under store root.",
    )
    p.add_argument("--limit", type=int, default=0, help="Max companies to process (0 = all).")
    p.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many company folders before --limit (for batching, e.g. --offset 100 --limit 100).",
    )
    p.add_argument(
        "--llm-max-consecutive-failures",
        type=int,
        default=10,
        metavar="N",
        help="After N consecutive LLM transport failures (bundle/PDF API), stop the batch and "
        "still write CSV + DB for rows collected so far (default: 10, aborts on the 11th).",
    )
    p.add_argument("--no-db", action="store_true", help="Skip Postgres upload.")
    p.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="CSV output path (default: <run_dir>/bronze_sec_filings.csv).",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel worker threads for company processing (default: 4).",
    )
    p.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the tqdm status bar (stderr).",
    )
    p.add_argument(
        "--recreate-bronze-table",
        action="store_true",
        help="Drop the bronze filings table if it exists (see BRONZE_SCHEMA / BRONZE_TABLE), "
        "then create it before upserts (wipes all rows in that table). "
        "Default: keep the table and only insert/update rows.",
    )
    p.add_argument(
        "--db-batch-size",
        type=int,
        default=100,
        metavar="N",
        help="Postgres: upsert rows in transactions of N (default 100). Use 1 for per-row commits.",
    )
    args = p.parse_args()

    if args.recreate_bronze_table and args.no_db:
        print(
            "--recreate-bronze-table is ignored with --no-db (no Postgres connection).",
            file=sys.stderr,
        )

    if not args.no_db and not (os.environ.get("DATABASE_URL") or "").strip():
        print(
            "DATABASE_URL is not set (add it to .env or the environment). "
            "Without it, the pipeline only writes the CSV and never connects to Postgres. "
            "Use --no-db to silence this check when you intend CSV-only.",
            file=sys.stderr,
        )
        return 1

    try:
        configure_llm()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    store_root: Path = args.store_root
    run_dir = _resolve_run_dir(store_root, args.run.strip() or None)
    csv_path = args.csv or (run_dir / "bronze_sec_filings.csv")

    companies = list(iter_company_dirs(run_dir))
    dirs_seen = len(companies)
    off = max(0, int(args.offset))
    if off:
        companies = companies[off:]
    if args.limit > 0:
        companies = companies[: args.limit]
    company_jobs = len(companies)

    fieldnames = [
        "run_folder",
        "ticker",
        "cik10",
        "company_name",
        "public_float_usd",
        "public_float_source",
        "relevance_keywords",
        "signal_hits_pain",
        "signal_hits_strategic",
        "signal_hits_vertical",
        "tags_business",
        "tags_products",
        "tags_strategy",
        "tags_risk",
        "description_business_operations",
        "description_products_services",
        "description_strategy_outlook",
        "description_risk_summary",
        "officers_contacts_json",
        "pdf_gemini_notes",
        "combined_text_chars",
    ]

    rows: list[dict[str, object]] = []
    workers = max(1, int(args.workers))
    mon = LlmApiAbortController(max_consecutive_failures=args.llm_max_consecutive_failures)
    prov = llm_provider()
    db_batch = max(1, int(args.db_batch_size))

    db_conn = None
    pending_db: list[dict[str, object]] = []
    if not args.no_db:
        db_conn = connect()
        ensure_table(db_conn, recreate=args.recreate_bronze_table)

    def _flush_db_batch(*, force: bool = False) -> None:
        if db_conn is None:
            return
        if force:
            if pending_db:
                upsert_rows_batch(db_conn, pending_db)
                pending_db.clear()
            return
        if len(pending_db) >= db_batch:
            upsert_rows_batch(db_conn, pending_db)
            pending_db.clear()

    def _guarded(cdir: Path) -> dict[str, object] | None:
        if mon.should_abort():
            return None
        try:
            r = process_company(run_dir, cdir)
        except Exception as exc:
            tqdm.write(f"[pipeline] skip {cdir.name}: {exc!s}", file=sys.stderr)
            return None
        notes = str(r.get("pdf_gemini_notes") or "")
        if llm_transport_failure_in_notes(notes, provider=prov):
            tripped = mon.record_llm_transport_failure(notes)
            tqdm.write(
                f"[pipeline] LLM transport failure {cdir.name} (streak={mon.streak()}): {notes[:240]!s}",
                file=sys.stderr,
            )
            if tripped:
                tqdm.write(
                    "[pipeline] Stopping batch after repeated LLM API errors; "
                    "flushing CSV and DB for rows collected so far.",
                    file=sys.stderr,
                )
            return None
        mon.record_success()
        return r

    try:
        if not companies:
            pass
        elif workers == 1:
            it = companies
            if args.no_progress:
                for cdir in it:
                    if mon.should_abort():
                        break
                    r = _guarded(cdir)
                    if r is not None:
                        rows.append(r)
                        if db_conn is not None:
                            pending_db.append(r)
                            _flush_db_batch()
            else:
                for cdir in tqdm(it, total=len(companies), desc="Bronze", unit="co", file=sys.stderr):
                    if mon.should_abort():
                        break
                    r = _guarded(cdir)
                    if r is not None:
                        rows.append(r)
                        if db_conn is not None:
                            pending_db.append(r)
                            _flush_db_batch()
        else:
            pbar = (
                None
                if args.no_progress
                else tqdm(
                    total=len(companies),
                    desc="Bronze",
                    unit="co",
                    file=sys.stderr,
                )
            )
            i = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                while i < len(companies) and not mon.should_abort():
                    batch = companies[i : i + workers]
                    futs = {pool.submit(_guarded, cdir): cdir for cdir in batch}
                    for fut in as_completed(futs):
                        r = fut.result()
                        if pbar is not None:
                            pbar.update(1)
                        if r is not None:
                            rows.append(r)
                            if db_conn is not None:
                                pending_db.append(r)
                                _flush_db_batch()
                    i += len(batch)
            if pbar is not None:
                pbar.close()
    finally:
        if db_conn is not None:
            _flush_db_batch(force=True)

    rows.sort(key=lambda r: str(r["ticker"]).upper())

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "run_folder": r["run_folder"],
                    "ticker": r["ticker"],
                    "cik10": r["cik10"] or "",
                    "company_name": r.get("company_name") or "",
                    "public_float_usd": r["public_float_usd"] if r["public_float_usd"] is not None else "",
                    "public_float_source": r["public_float_source"],
                    "relevance_keywords": r["relevance_keywords"],
                    "signal_hits_pain": r.get("signal_hits_pain") or "",
                    "signal_hits_strategic": r.get("signal_hits_strategic") or "",
                    "signal_hits_vertical": r.get("signal_hits_vertical") or "",
                    "tags_business": r.get("tags_business") or "",
                    "tags_products": r.get("tags_products") or "",
                    "tags_strategy": r.get("tags_strategy") or "",
                    "tags_risk": r.get("tags_risk") or "",
                    "description_business_operations": r.get("description_business_operations") or "",
                    "description_products_services": r.get("description_products_services") or "",
                    "description_strategy_outlook": r.get("description_strategy_outlook") or "",
                    "description_risk_summary": r.get("description_risk_summary") or "",
                    "officers_contacts_json": json.dumps(r["officers_contacts"], ensure_ascii=False),
                    "pdf_gemini_notes": r["pdf_gemini_notes"],
                    "combined_text_chars": r["combined_text_chars"],
                }
            )

    _db_qt = qualified_bronze_table_sql()
    _db_fqn = bronze_table_fqn()
    summary: dict[str, object] = {
        "run_dir": str(run_dir),
        "run_folder": run_dir.name,
        "csv": str(csv_path),
        "company_dirs_seen_under_run": dirs_seen,
        "offset": off,
        "llm_max_consecutive_failures": args.llm_max_consecutive_failures,
        "companies_scheduled": company_jobs,
        "rows_extracted": len(rows),
        "db_upload": not args.no_db,
        "bronze_table_recreated": bool(args.recreate_bronze_table) and not args.no_db,
        "bronze_llm_provider": llm_provider(),
        "db_table": _db_fqn,
        "db_batch_size": db_batch,
        "db_query_this_run": f"SELECT * FROM {_db_qt} WHERE run_folder = '{run_dir.name}' ORDER BY ticker;",
    }
    if mon.abort:
        summary["aborted_due_to_llm_transport_failures"] = True
        summary["llm_last_error_excerpt"] = mon.last_error_excerpt
        summary["note"] = (
            "Batch stopped after repeated LLM API transport failures; partial CSV and Postgres upsert completed."
        )
    elif len(rows) < company_jobs:
        summary["note"] = (
            "Some companies were skipped (see stderr). rows_extracted < companies_scheduled "
            "means nothing was inserted for those tickers."
        )

    if not args.no_db:
        try:
            with db_conn.cursor() as cur:  # type: ignore[union-attr]
                cur.execute(f"SELECT COUNT(*) FROM {_db_qt} WHERE run_folder = %s", (run_dir.name,))
                summary["db_rows_for_this_run_folder"] = int(cur.fetchone()[0])
                cur.execute(f"SELECT COUNT(*) FROM {_db_qt}")
                summary["db_total_rows_in_table"] = int(cur.fetchone()[0])
        finally:
            db_conn.close()  # type: ignore[union-attr]
    else:
        summary["db_rows_for_this_run_folder"] = None
        summary["db_total_rows_in_table"] = None

    print(json.dumps(summary, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
