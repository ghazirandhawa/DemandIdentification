"""
Silver: build FE-facing opportunities (rule-based ICP fit) and optional Gemini narratives.

This is the same logic as ingest_gold.py, but writes into silver.opportunities and
silver.opportunity_insights. (User has manually migrated these tables from gold → silver.)

Usage:
  python ingest_opportunities.py
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg2.extras import execute_batch

import gold_llm
from ingest_utils import get_connection

PROMPT_VERSION = "silver_insights_v1"

DEFAULT_CHECKPOINT_PATH = Path(__file__).with_name(".ingest_opportunities_checkpoint.json")
DEFAULT_FALLBACK_CSV_PATH = Path(__file__).with_name("opportunity_insights_fallback.csv")


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_EMP_IDEAL_LO = 200
_EMP_IDEAL_HI = 5000
_REV_IDEAL_LO = 50_000_000.0
_REV_IDEAL_HI = 500_000_000.0


def _text_blob(title: str | None, desc: str | None) -> str:
    return f"{title or ''} {desc or ''}".lower()


def classify_problem_type(title: str | None, description: str | None) -> str:
    t = _text_blob(title, description)
    if re.search(r"\b(genai|generative ai|llm|gpt|langchain|rag|prompt)\b", t):
        return "GenAI / Automation"
    if re.search(
        r"\b(machine learning|mlops|deep learning|\bml engineer\b|data scientist|"
        r"computer vision|nlp|neural)\b",
        t,
    ):
        return "AI / ML"
    if re.search(
        r"\b(bi |business intelligence|tableau|looker|power bi|qlik|dashboard|analytics manager)\b",
        t,
    ):
        return "BI / Analytics"
    return "Data Engineering"


def signal_display_row(signal_type: str, source: str) -> str:
    if signal_type == "formal_rfp":
        return "Formal RFP"
    if source == "govcon":
        return "Formal RFP"
    return "Hiring Signal"


def compute_icp_score(
    signal_type: str,
    industry: str | None,
    employees: int | None,
    revenue: float | None,
    country: str | None,
    state: str | None,
    title: str | None,
    description: str | None,
) -> tuple[int, str]:
    notes: list[str] = []
    score = 45

    blob = _text_blob(title, description)
    if signal_type == "hiring" and re.search(
        r"\b(data|analytics|ai|ml|warehouse|snowflake|databricks|bi |governance|automation)\b",
        blob,
    ):
        score += 12
        notes.append("role_relevance")

    if country and re.search(r"united states|usa|\bus\b", country.lower()):
        score += 8
        notes.append("geo_us")
    elif state and len(state or "") > 1:
        score += 4
        notes.append("geo_subnational")

    if employees is not None:
        if _EMP_IDEAL_LO <= employees <= _EMP_IDEAL_HI:
            score += 18
            notes.append("emp_mid_market")
        elif employees < _EMP_IDEAL_LO:
            score += 4
            notes.append("emp_small")
        elif employees > 20_000:
            score -= 12
            notes.append("emp_enterprise_skew")

    if revenue is not None and revenue > 0:
        if _REV_IDEAL_LO <= revenue <= _REV_IDEAL_HI:
            score += 15
            notes.append("rev_mid_market")
        elif revenue > 1_000_000_000_000:
            score -= 15
            notes.append("rev_mega_corp")

    if industry:
        ind = industry.lower()
        if any(
            x in ind
            for x in (
                "financial",
                "banking",
                "insurance",
                "health",
                "retail",
                "manufacturing",
                "logistics",
                "information technology",
            )
        ):
            score += 5
            notes.append("industry_vertical")

    if signal_type == "formal_rfp":
        score -= 8
        notes.append("gov_signal_discount")

    score = max(0, min(100, int(round(score))))
    return score, ",".join(notes)


def priority_from_score(s: int) -> str:
    if s >= 85:
        return "Critical"
    if s >= 70:
        return "High"
    return "Medium"


def opportunity_kind(signal_type: str, title: str | None, desc: str | None) -> str:
    blob = _text_blob(title, desc)
    if signal_type == "formal_rfp":
        return "project"
    if re.search(r"\b(chief|vp |director|head of)\b", blob):
        return "transformation"
    if re.search(r"\b(program|initiative|platform|migration|modernization)\b", blob):
        return "project"
    return "individual_hire"


def geography_display(country: str | None, state: str | None, city: str | None, company_name: str) -> str:
    parts = [p for p in (city, state, country) if p and str(p).strip()]
    if parts:
        return ", ".join(parts[:3])
    if company_name:
        return "—"
    return "—"


def fetch_signal_rows(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            ds.signal_id,
            ds.signal_type,
            ds.signal_subtype,
            ds.title,
            ds.description,
            ds.company_name,
            ds.company_id,
            ds.location_city,
            ds.location_state,
            ds.location_country,
            ds.signal_date,
            ds.source,
            ds.source_url,
            ds.is_jooble,
            ds.is_adzuna,
            ds.is_apollo,
            ds.is_govcon,
            c.industry,
            c.estimated_employees,
            c.annual_revenue,
            c.country AS company_country,
            c.state AS company_state,
            c.city AS company_city,
            c.name AS resolved_company_name
        FROM silver.demand_signals ds
        LEFT JOIN silver.companies c ON c.company_id = ds.company_id
        ORDER BY ds.signal_date DESC NULLS LAST
        """
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_opportunity_rows(rows: list[dict[str, Any]]) -> list[tuple]:
    """
    Build opportunities, but dedupe to one row per company_name.
    Winner: higher icp_score, then newer signal_date, then stable tie-break by signal_id.
    """
    best_by_company: dict[str, tuple[tuple[int, int, str], tuple]] = {}
    for r in rows:
        company_name = (r.get("resolved_company_name") or r.get("company_name") or "Unknown").strip()
        key = re.sub(r"\s+", " ", company_name).strip().lower() or "unknown"

        industry = r.get("industry")
        geo = geography_display(
            r.get("location_country") or r.get("company_country"),
            r.get("location_state") or r.get("company_state"),
            r.get("location_city") or r.get("company_city"),
            company_name,
        )
        st = r.get("signal_type") or "hiring"
        src = r.get("source") or ""
        prob = classify_problem_type(r.get("title"), r.get("description"))
        display = signal_display_row(st, src)
        icp, notes = compute_icp_score(
            st,
            industry,
            r.get("estimated_employees"),
            _to_float(r.get("annual_revenue")),
            r.get("location_country") or r.get("company_country"),
            r.get("location_state"),
            r.get("title"),
            r.get("description"),
        )
        pr = priority_from_score(icp)
        kind = opportunity_kind(st, r.get("title"), r.get("description"))

        row_tuple = (
            r["signal_id"],
            r.get("company_id"),
            company_name[:512],
            str(industry)[:512] if industry else None,
            geo[:512],
            display[:128],
            (src or "")[:64],
            prob[:128],
            icp,
            pr[:32],
            "Signals Identified",
            kind[:32],
            (r.get("title") or "")[:1024],
            r.get("description"),
            r.get("source_url"),
            r.get("signal_date"),
            bool(r.get("is_jooble")),
            bool(r.get("is_adzuna")),
            bool(r.get("is_apollo")),
            bool(r.get("is_govcon")),
            notes[:2000],
        )

        sd = r.get("signal_date")
        ts = int(sd.timestamp()) if sd is not None else -1
        sid = str(r.get("signal_id") or "")
        score = (icp, ts, sid)

        cur_best = best_by_company.get(key)
        if cur_best is None or score > cur_best[0]:
            best_by_company[key] = (score, row_tuple)

    items = list(best_by_company.values())
    items.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in items]


def truncate_opportunities(cur) -> None:
    # CASCADE clears opportunity_insights (FK → opportunities) if FK exists in your migrated schema.
    cur.execute("TRUNCATE TABLE silver.opportunities CASCADE")


def insert_opportunities(cur, batch: list[tuple]) -> None:
    sql = """
    INSERT INTO silver.opportunities (
        silver_signal_id, silver_company_id, company_name, company_industry, geography,
        signal_display, signal_source, problem_type, icp_score, priority, stage,
        opportunity_kind, signal_title, signal_description, source_url, signal_date,
        is_jooble, is_adzuna, is_apollo, is_govcon, scoring_notes
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    """
    execute_batch(cur, sql, batch, page_size=400)


def insert_insights(cur, items: list[tuple]) -> None:
    if not items:
        return
    sql = """
    INSERT INTO silver.opportunity_insights (
        opportunity_id, business_problem, why_opportunity, solution_summary, outreach_draft,
        problem_category, severity_score, impact_summary, gemini_model, prompt_version
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (opportunity_id) DO UPDATE SET
        business_problem = excluded.business_problem,
        why_opportunity = excluded.why_opportunity,
        solution_summary = excluded.solution_summary,
        outreach_draft = excluded.outreach_draft,
        problem_category = excluded.problem_category,
        severity_score = excluded.severity_score,
        impact_summary = excluded.impact_summary,
        gemini_model = excluded.gemini_model,
        prompt_version = excluded.prompt_version,
        generated_at = now()
    """
    execute_batch(cur, sql, items, page_size=50)


def _is_cr_retry_error(e: Exception) -> bool:
    msg = str(e)
    return (
        "TransactionRetryWithProtoRefreshError" in msg
        or "restart transaction" in msg.lower()
        or "ABORT_REASON_CLIENT_REJECT" in msg
        or "TransactionAbortedError" in msg
    )


def _append_insights_to_csv(path: Path, rows: list[tuple]) -> int:
    """
    Append insight rows to a local CSV spill file.
    If file exists, de-dupe on opportunity_id so reruns don't bloat the file.
    Returns number of newly written rows.
    """
    if not rows:
        return 0

    fieldnames = [
        "opportunity_id",
        "business_problem",
        "why_opportunity",
        "solution_summary",
        "outreach_draft",
        "problem_category",
        "severity_score",
        "impact_summary",
        "gemini_model",
        "prompt_version",
    ]

    existing_ids: set[str] = set()
    if path.exists():
        try:
            with path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    oid = (r.get("opportunity_id") or "").strip()
                    if oid:
                        existing_ids.add(oid)
        except Exception:
            # If existing CSV is unreadable, fall back to simple append without de-dupe.
            existing_ids.clear()

    wrote = 0
    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for t in rows:
            oid = str(t[0])
            if existing_ids and oid in existing_ids:
                continue
            writer.writerow(
                {
                    "opportunity_id": oid,
                    "business_problem": t[1],
                    "why_opportunity": t[2],
                    "solution_summary": t[3],
                    "outreach_draft": t[4],
                    "problem_category": t[5],
                    "severity_score": t[6],
                    "impact_summary": t[7],
                    "gemini_model": t[8],
                    "prompt_version": t[9],
                }
            )
            wrote += 1
    return wrote


def _flush_insights_with_retry_or_csv(
    *,
    conn,
    cur,
    pending_inserts: list[tuple],
    fallback_csv_path: Path,
) -> dict[str, int]:
    """
    Try DB upsert once; on Cockroach retry error, retry one more time.
    If it still fails, append to local CSV and continue.
    Returns stats dict.
    """
    stats = {"db_upserted": 0, "csv_appended": 0}
    if not pending_inserts:
        return stats

    try:
        insert_insights(cur, pending_inserts)
        conn.commit()
        stats["db_upserted"] = len(pending_inserts)
        pending_inserts.clear()
        return stats
    except Exception as e:
        conn.rollback()
        if not _is_cr_retry_error(e):
            # Non-retry error: spill to CSV and continue.
            stats["csv_appended"] = _append_insights_to_csv(fallback_csv_path, pending_inserts)
            pending_inserts.clear()
            return stats

        # Retry once for Cockroach serialization/retry errors.
        time.sleep(0.35)
        try:
            insert_insights(cur, pending_inserts)
            conn.commit()
            stats["db_upserted"] = len(pending_inserts)
            pending_inserts.clear()
            return stats
        except Exception:
            conn.rollback()
            stats["csv_appended"] = _append_insights_to_csv(fallback_csv_path, pending_inserts)
            pending_inserts.clear()
            return stats


def _load_checkpoint(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = dict(payload)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def _get_tqdm():
    try:
        from tqdm import tqdm  # type: ignore

        return tqdm
    except Exception:
        return None


def _insight_row_from_llm(opportunity_id: str, data: dict[str, Any], model_name: str) -> tuple:
    return (
        opportunity_id,
        (data.get("business_problem") or "")[:8000],
        (data.get("why_opportunity") or "")[:8000],
        (data.get("solution_summary") or "")[:8000],
        (data.get("outreach_draft") or "")[:8000],
        (data.get("problem_category") or "")[:512],
        gold_llm.safe_int(data.get("severity_score")),
        (data.get("impact_summary") or "")[:4000],
        model_name,
        PROMPT_VERSION,
    )


def _generate_one_insight(row: dict[str, Any], api_key: str, model_name: str) -> tuple[str, tuple | None]:
    """
    Return (opportunity_id, insight_row_tuple_or_None).
    Never raises: failures map to None so the pipeline keeps going.
    """
    oid = str(row.get("opportunity_id"))
    try:
        ctx = gold_llm.build_context_block(row)
        data = gold_llm.generate_insight_json(ctx, api_key)
        if not data:
            return oid, None
        return oid, _insight_row_from_llm(oid, data, model_name)
    except Exception:
        return oid, None


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Build silver.opportunities + generate insights with checkpointing.")
    p.add_argument("--offset", type=int, default=None, help="Start generating insights from this 0-based offset.")
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Number of opportunities to process after offset (default: all remaining).",
    )
    p.add_argument(
        "--checkpoint",
        type=str,
        default=str(DEFAULT_CHECKPOINT_PATH),
        help=f"Checkpoint JSON path (default: {DEFAULT_CHECKPOINT_PATH.name})",
    )
    p.add_argument("--workers", type=int, default=12, help="Parallel workers for Gemini calls (default: 12).")
    p.add_argument("--batch-size", type=int, default=8, help="Gemini batch size (default: 8).")
    p.add_argument("--commit-every", type=int, default=100, help="Commit insights every N rows (default: 100).")
    p.add_argument(
        "--fallback-csv",
        type=str,
        default=str(DEFAULT_FALLBACK_CSV_PATH),
        help=f"CSV spill path if DB insert fails twice (default: {DEFAULT_FALLBACK_CSV_PATH.name})",
    )
    p.add_argument(
        "--no-insights",
        action="store_true",
        help="Only build silver.opportunities; skip Gemini insights.",
    )
    p.add_argument(
        "--insights-only",
        action="store_true",
        help="Skip building/inserting silver.opportunities; only upsert silver.opportunity_insights.",
    )
    args = p.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent / ".env")
    except ImportError:
        pass

    api_key = os.environ.get("GEMINI_API_KEY") or ""
    checkpoint_path = Path(args.checkpoint)
    fallback_csv_path = Path(args.fallback_csv)
    cp = _load_checkpoint(checkpoint_path) or {}
    cp_offset = cp.get("next_offset")
    start_offset = args.offset if args.offset is not None else (int(cp_offset) if isinstance(cp_offset, int) else 0)

    print("Opportunities load: connecting …")
    conn = get_connection()
    conn.autocommit = False
    cur = conn.cursor()
    processed = 0
    inserted_insights_total = 0
    db_upserted_total = 0
    csv_appended_total = 0
    try:
        if args.no_insights and args.insights_only:
            raise ValueError("Use only one of --no-insights or --insights-only.")

        if not args.insights_only:
            # truncate_opportunities(cur)
            print("Reading silver.demand_signals (+ companies) …")
            rows = fetch_signal_rows(cur)
            print(f"  -> {len(rows)} signal rows")
            print("Scoring + inserting silver.opportunities …")
            batch = build_opportunity_rows(rows)
            insert_opportunities(cur, batch)
            print(f"  -> {len(batch)} opportunities (deduped by company_name)")
            conn.commit()
        else:
            print("Insights-only mode: using existing silver.opportunities …")

        if args.no_insights:
            print("  --no-insights set — skipping silver.opportunity_insights.")
            return

        if not api_key:
            print("  GEMINI_API_KEY not set — skipping silver.opportunity_insights.")
            return

        cur.execute(
            """
            SELECT opportunity_id, company_name, company_industry, geography, signal_display,
                   signal_source, problem_type, icp_score, priority, signal_title, signal_description,
                   source_url, signal_date
            FROM silver.opportunities
            ORDER BY icp_score DESC, signal_date DESC NULLS LAST
            """,
        )
        cols = [d[0] for d in cur.description]
        opps = [dict(zip(cols, row)) for row in cur.fetchall()]
        total = len(opps)
        if start_offset < 0:
            start_offset = 0
        if start_offset > total:
            start_offset = total
        end_offset = total
        if args.limit is not None:
            lim = int(args.limit)
            if lim < 0:
                lim = 0
            end_offset = min(total, start_offset + lim)

        _save_checkpoint(
            checkpoint_path,
            {
                "script": "ingest_opportunities.py",
                "phase": "insights",
                "total": total,
                "next_offset": start_offset,
                "end_offset": end_offset,
                "workers": args.workers,
                "batch_size": args.batch_size,
                "commit_every": args.commit_every,
            },
        )

        print(
            f"  Generating Gemini insights for {end_offset - start_offset}/{total} opportunities "
            f"(offset={start_offset}, limit={args.limit}) …"
        )
        model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        sleep_sec = float(os.environ.get("GEMINI_SLEEP_SEC", "0.35"))
        workers = max(1, int(args.workers))
        batch_size = max(1, int(args.batch_size))
        commit_every = max(1, int(args.commit_every))

        tqdm = _get_tqdm()
        pbar = tqdm(total=end_offset - start_offset, desc="Gemini insights", unit="opp") if tqdm else None

        pending_inserts: list[tuple] = []
        next_offset_to_save = start_offset

        with ThreadPoolExecutor(max_workers=workers) as ex:
            i = start_offset
            while i < end_offset:
                chunk = opps[i : min(end_offset, i + batch_size)]
                futures = [ex.submit(_generate_one_insight, r, api_key, model_name) for r in chunk]
                for fut in as_completed(futures):
                    oid, insight_row = fut.result()
                    processed += 1
                    if insight_row is not None:
                        pending_inserts.append(insight_row)
                        inserted_insights_total += 1
                    if pbar:
                        pbar.update(1)

                i += batch_size
                next_offset_to_save = i

                # Commit + checkpoint every commit_every processed rows.
                if processed % commit_every == 0:
                    flush_stats = _flush_insights_with_retry_or_csv(
                        conn=conn,
                        cur=cur,
                        pending_inserts=pending_inserts,
                        fallback_csv_path=fallback_csv_path,
                    )
                    db_upserted_total += flush_stats["db_upserted"]
                    csv_appended_total += flush_stats["csv_appended"]
                    _save_checkpoint(
                        checkpoint_path,
                        {
                            "script": "ingest_opportunities.py",
                            "phase": "insights",
                            "total": total,
                            "next_offset": next_offset_to_save,
                            "processed_since_start": processed,
                            "inserted_insights_since_start": inserted_insights_total,
                            "db_upserted_since_start": db_upserted_total,
                            "csv_appended_since_start": csv_appended_total,
                            "fallback_csv_path": str(fallback_csv_path),
                        },
                    )

                if sleep_sec > 0:
                    time.sleep(sleep_sec)

            # Final flush
            flush_stats = _flush_insights_with_retry_or_csv(
                conn=conn,
                cur=cur,
                pending_inserts=pending_inserts,
                fallback_csv_path=fallback_csv_path,
            )
            db_upserted_total += flush_stats["db_upserted"]
            csv_appended_total += flush_stats["csv_appended"]
            _save_checkpoint(
                checkpoint_path,
                {
                    "script": "ingest_opportunities.py",
                    "phase": "done",
                    "total": total,
                    "next_offset": end_offset,
                    "end_offset": end_offset,
                    "processed_since_start": processed,
                    "inserted_insights_since_start": inserted_insights_total,
                    "db_upserted_since_start": db_upserted_total,
                    "csv_appended_since_start": csv_appended_total,
                    "fallback_csv_path": str(fallback_csv_path),
                },
            )

        if pbar:
            pbar.close()

        print(
            f"  -> insights generated: {inserted_insights_total} | db upserted: {db_upserted_total} | "
            f"csv spilled: {csv_appended_total}"
        )
        print("Opportunities commit OK.")
    except Exception:
        conn.rollback()
        # Persist best-effort checkpoint even on failure
        try:
            _save_checkpoint(
                checkpoint_path,
                {
                    "script": "ingest_opportunities.py",
                    "phase": "failed",
                    "next_offset": start_offset + processed,
                    "processed_since_start": processed,
                    "inserted_insights_since_start": inserted_insights_total,
                    "db_upserted_since_start": db_upserted_total,
                    "csv_appended_since_start": csv_appended_total,
                    "fallback_csv_path": str(fallback_csv_path),
                },
            )
        except Exception:
            pass
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

