#!/usr/bin/env python3
"""
Silver ``SEC_filings_opportunities`` -> silver ``SEC_filings_opportunities_insights`` via Gemini.

Joins each opportunity back to bronze for verbatim source text, then upserts one insight row per
``opportunity_id`` (aligned with gold ``opportunity_insights``-style fields).

  python pipeline_silver_sec_opportunities_insights.py
  python pipeline_silver_sec_opportunities_insights.py --run-folder 2026-04-16T125501Z --only-missing
  python pipeline_silver_sec_opportunities_insights.py --dry-run --limit 1 --workers 8

``--recreate-silver-tables`` / ``--recreate-silver-table`` drops and recreates **only** the
``SEC_filings_opportunities_insights`` table (opportunities rows are kept). It does **not** run or
alter bronze.

Requires ``DATABASE_URL``, ``GEMINI_API_KEY``. Optional docs under ``datapilot_docs/`` (same as
``pipeline_silver_sec_opportunities.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from tqdm.auto import tqdm

_ROOT = Path(__file__).resolve().parent
_ds = _ROOT / "data_sources"
if _ds.is_dir() and str(_ds) not in sys.path:
    sys.path.insert(0, str(_ds))

load_dotenv(_ROOT / ".env")

from sec_filings_pipeline.datapilot_context import load_datapilot_bundle  # noqa: E402
from sec_filings_pipeline.gemini_extract import configure_gemini  # noqa: E402
from sec_filings_pipeline.gemini_sec_scoring import (  # noqa: E402
    INSIGHTS_PROMPT_VERSION,
    format_bronze_block,
    format_silver_opportunity_block,
    generate_insights_with_gemini,
    truncate_block,
)
from sec_filings_pipeline.silver_sec_db import (  # noqa: E402
    connect,
    ensure_silver_sec_tables,
    fetch_bronze_row,
    fetch_silver_opportunities_for_insights,
    upsert_sec_insights_batch,
)


@dataclass(frozen=True)
class _InsightWorkerCtx:
    ctx_text: str
    meta_note: str
    model_name: str
    write: bool
    max_bronze_chars: int


def _process_one_insight_row(o: dict[str, object], ictx: _InsightWorkerCtx) -> dict[str, object]:
    """One opportunity row; private DB connection per call."""
    rf = str(o.get("bronze_run_folder") or "")
    tk = str(o.get("bronze_ticker") or "")
    out: dict[str, object] = {"status": "fail", "ticker": tk}

    conn = connect()
    try:
        bron = fetch_bronze_row(conn, run_folder=rf, ticker=tk)
        if not bron:
            out["reason"] = "bronze_row_missing"
            return out

        bronze_block = truncate_block(format_bronze_block(bron), ictx.max_bronze_chars)
        opp_block = format_silver_opportunity_block(o)
        ins, note = generate_insights_with_gemini(
            datapilot_context=ictx.ctx_text,
            bronze_block=bronze_block,
            opportunity_block=opp_block,
            context_meta_note=ictx.meta_note,
        )
        if not ins or ins.get("_parse_note"):
            out["status"] = "fail"
            out["gemini_note"] = note
            out["parse_note"] = ins.get("_parse_note")
            return out

        opp_id = str(o.get("opportunity_id") or "")
        if not opp_id:
            out["reason"] = "missing_opportunity_id"
            return out

        payload = {
            "opportunity_id": opp_id,
            "gemini_note": note,
            "insight": ins,
        }

        if not ictx.write:
            out["status"] = "dry_ok"
            out["payload"] = payload
            return out

        out["insight_db_row"] = {
            "opportunity_id": opp_id,
            "business_problem": ins.get("business_problem"),
            "why_opportunity": ins.get("why_opportunity"),
            "why_now_signals": ins.get("why_now_signals"),
            "solution_summary": ins.get("solution_summary"),
            "outreach_draft": ins.get("outreach_draft"),
            "problem_category": ins.get("problem_category"),
            "severity_score": ins.get("severity_score"),
            "impact_summary": ins.get("impact_summary"),
            "evidence_quotes": ins.get("evidence_quotes"),
            "gemini_model": ictx.model_name,
            "prompt_version": INSIGHTS_PROMPT_VERSION,
        }
        out["status"] = "ok"
        return out
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Generate SEC silver opportunity insights with Gemini.")
    p.add_argument("--run-folder", default="", help="Filter bronze_run_folder on silver opportunities.")
    p.add_argument("--tickers", default="", help="Comma-separated tickers (optional).")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument(
        "--only-missing",
        action="store_true",
        help="Only opportunities that do not yet have an insights row.",
    )
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
        help="Print JSON only; do not upsert insights.",
    )
    p.add_argument(
        "--recreate-silver-tables",
        "--recreate-silver-table",
        action="store_true",
        dest="recreate_silver_tables",
        help="Drop and recreate **only** SEC_filings_opportunities_insights (destructive). "
        "Does not drop opportunities or bronze.",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel worker threads (default 4). Insight upserts use one connection on the main thread.",
    )
    p.add_argument(
        "--db-batch-size",
        type=int,
        default=100,
        metavar="N",
        help="Upsert insights in transactions of N rows (default 100). Use 1 for per-row commits.",
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

    write = not args.dry_run

    conn = connect()
    if write:
        ensure_silver_sec_tables(
            conn,
            recreate=False,
            recreate_insights_only=args.recreate_silver_tables,
        )
    elif args.recreate_silver_tables:
        tqdm.write(
            "Note: --recreate-silver-tables/--recreate-silver-table ignored when --dry-run is set.",
            file=sys.stderr,
        )

    opps = fetch_silver_opportunities_for_insights(
        conn,
        run_folder=args.run_folder or None,
        tickers=tickers,
        only_missing_insight=args.only_missing,
        offset=args.offset,
        limit=args.limit,
    )
    conn.close()

    if not opps:
        print("No silver opportunities matched filters.")
        return 0

    workers = max(1, int(args.workers))
    db_batch = max(1, int(args.db_batch_size))
    ictx = _InsightWorkerCtx(
        ctx_text=ctx_text,
        meta_note=meta_note,
        model_name=model_name,
        write=write,
        max_bronze_chars=args.max_bronze_chars,
    )

    ok = 0
    failed = 0
    dry_payloads: list[dict[str, object]] = []
    pending_insights: list[dict[str, object]] = []
    write_conn = None
    if write:
        write_conn = connect()

    def _maybe_flush_insights(*, force: bool = False) -> None:
        if write_conn is None:
            return
        if force:
            if pending_insights:
                upsert_sec_insights_batch(write_conn, pending_insights)
                pending_insights.clear()
            return
        if len(pending_insights) >= db_batch:
            upsert_sec_insights_batch(write_conn, pending_insights)
            pending_insights.clear()

    def _handle_insight_result(r: dict[str, object]) -> None:
        nonlocal ok, failed
        st = str(r.get("status") or "")
        if st == "ok":
            ok += 1
            if write and r.get("insight_db_row") is not None:
                pending_insights.append(r["insight_db_row"])  # type: ignore[arg-type]
                _maybe_flush_insights()
        elif st == "dry_ok":
            dry_payloads.append(r["payload"])  # type: ignore[arg-type]
            ok += 1
        else:
            failed += 1
            tqdm.write(
                f"[skip {r.get('ticker')}] {r.get('reason') or ''} "
                f"gemini={r.get('gemini_note')} parse={r.get('parse_note')}",
                file=sys.stderr,
            )

    try:
        if workers == 1:
            it = opps
            if args.no_progress:
                for o in it:
                    r = _process_one_insight_row(o, ictx)
                    _handle_insight_result(r)
            else:
                for o in tqdm(it, total=len(opps), desc="silver_insights"):
                    r = _process_one_insight_row(o, ictx)
                    _handle_insight_result(r)
        else:
            pbar = None if args.no_progress else tqdm(total=len(opps), desc="silver_insights", unit="row")
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(_process_one_insight_row, o, ictx): o for o in opps}
                for fut in as_completed(futs):
                    r = fut.result()
                    if pbar is not None:
                        pbar.update(1)
                    _handle_insight_result(r)
            if pbar is not None:
                pbar.close()
        _maybe_flush_insights(force=True)
    finally:
        if write_conn is not None:
            write_conn.close()

    if not write:
        for pl in sorted(dry_payloads, key=lambda x: str((x or {}).get("opportunity_id", ""))):
            print(json.dumps(pl, ensure_ascii=False, indent=2))

    print(f"Done. processed={ok} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
