from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from .bronze_db import qualified_bronze_table_sql

_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validated_ident(raw: str, *, env_name: str) -> str:
    s = (raw or "").strip()
    if not _IDENT.fullmatch(s):
        raise RuntimeError(
            f"Invalid {env_name}={raw!r}; use a single SQL identifier "
            "(letters, digits, underscore; must not start with a digit)."
        )
    return s


def silver_schema_sql() -> str:
    s = _validated_ident(os.environ.get("SILVER_SCHEMA") or "silver", env_name="SILVER_SCHEMA")
    return f'"{s}"'


def silver_sec_opportunities_table_sql() -> str:
    t = _validated_ident(
        os.environ.get("SILVER_SEC_OPPORTUNITIES_TABLE") or "SEC_filings_opportunities",
        env_name="SILVER_SEC_OPPORTUNITIES_TABLE",
    )
    return f'"{t}"'


def silver_sec_insights_table_sql() -> str:
    t = _validated_ident(
        os.environ.get("SILVER_SEC_INSIGHTS_TABLE") or "SEC_filings_opportunities_insights",
        env_name="SILVER_SEC_INSIGHTS_TABLE",
    )
    return f'"{t}"'


def qualified_silver_opportunities_sql() -> str:
    return f"{silver_schema_sql()}.{silver_sec_opportunities_table_sql()}"


def qualified_silver_insights_sql() -> str:
    return f"{silver_schema_sql()}.{silver_sec_insights_table_sql()}"


def connect():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg2.connect(url)


def ensure_silver_sec_tables(
    conn,
    *,
    recreate: bool = False,
    recreate_insights_only: bool = False,
) -> None:
    """
    Create or migrate silver SEC tables.

    - ``recreate=True``: drop **both** insights and opportunities (FK order), then recreate.
    - ``recreate_insights_only=True``: drop **only** the insights table (opportunities kept).
      Ignored if ``recreate`` is True.
    """
    sc = silver_schema_sql()
    qo = qualified_silver_opportunities_sql()
    qi = qualified_silver_insights_sql()
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {sc};")
        if recreate:
            cur.execute(f"DROP TABLE IF EXISTS {qi};")
            cur.execute(f"DROP TABLE IF EXISTS {qo};")
        elif recreate_insights_only:
            cur.execute(f"DROP TABLE IF EXISTS {qi};")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {qo} (
              opportunity_id UUID NOT NULL PRIMARY KEY,
              silver_signal_id UUID NOT NULL,
              silver_company_id UUID NOT NULL,
              bronze_run_folder TEXT NOT NULL,
              bronze_ticker TEXT NOT NULL,
              cik10 TEXT,
              company_name TEXT NOT NULL,
              company_industry TEXT,
              geography TEXT,
              signal_display TEXT NOT NULL,
              signal_source TEXT NOT NULL,
              problem_type TEXT NOT NULL,
              icp_score INT4 NOT NULL,
              priority TEXT NOT NULL,
              stage TEXT NOT NULL DEFAULT 'Signals Identified',
              opportunity_kind TEXT,
              signal_title TEXT NOT NULL,
              signal_description TEXT NOT NULL,
              source_url TEXT,
              signal_date TIMESTAMPTZ,
              is_jooble BOOL NOT NULL DEFAULT false,
              is_adzuna BOOL NOT NULL DEFAULT false,
              is_apollo BOOL NOT NULL DEFAULT false,
              is_govcon BOOL NOT NULL DEFAULT false,
              is_sec_filing BOOL NOT NULL DEFAULT true,
              country TEXT,
              mailing_address TEXT,
              website TEXT,
              business_address TEXT,
              contacts_summary TEXT,
              latest_revenue_usd DOUBLE PRECISION,
              scoring_notes TEXT,
              evidence_quotes JSONB,
              gemini_model TEXT,
              prompt_version TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE (bronze_run_folder, bronze_ticker)
            );
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {qi} (
              insight_id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
              opportunity_id UUID NOT NULL REFERENCES {qo} (opportunity_id) ON DELETE CASCADE,
              business_problem TEXT,
              why_opportunity TEXT,
              why_now_signals TEXT,
              solution_summary TEXT,
              outreach_draft TEXT,
              problem_category TEXT,
              severity_score INT4,
              impact_summary TEXT,
              evidence_quotes JSONB,
              gemini_model TEXT,
              prompt_version TEXT,
              generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE (opportunity_id)
            );
            """
        )
        cur.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_sec_opp_bronze
            ON {qo} (bronze_run_folder, bronze_ticker);
            """
        )
        cur.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_sec_ins_opp
            ON {qi} (opportunity_id);
            """
        )
        for stmt in (
            f"ALTER TABLE {qo} ADD COLUMN IF NOT EXISTS evidence_quotes JSONB;",
            f"ALTER TABLE {qo} ADD COLUMN IF NOT EXISTS is_sec_filing BOOL;",
            f"ALTER TABLE {qo} ADD COLUMN IF NOT EXISTS country TEXT;",
            f"ALTER TABLE {qo} ADD COLUMN IF NOT EXISTS mailing_address TEXT;",
            f"ALTER TABLE {qo} ADD COLUMN IF NOT EXISTS website TEXT;",
            f"ALTER TABLE {qo} ADD COLUMN IF NOT EXISTS business_address TEXT;",
            f"ALTER TABLE {qo} ADD COLUMN IF NOT EXISTS contacts_summary TEXT;",
            f"ALTER TABLE {qo} ADD COLUMN IF NOT EXISTS latest_revenue_usd DOUBLE PRECISION;",
            f"ALTER TABLE {qi} ADD COLUMN IF NOT EXISTS evidence_quotes JSONB;",
            f"ALTER TABLE {qi} ADD COLUMN IF NOT EXISTS why_now_signals TEXT;",
        ):
            cur.execute(stmt)
        cur.execute(
            f"UPDATE {qo} SET is_sec_filing = true WHERE is_sec_filing IS NULL;"
        )
    conn.commit()


def upsert_sec_opportunity(
    conn,
    *,
    opportunity_id: str,
    silver_signal_id: str,
    silver_company_id: str,
    bronze_run_folder: str,
    bronze_ticker: str,
    cik10: str | None,
    company_name: str,
    company_industry: str | None,
    geography: str | None,
    signal_display: str,
    signal_source: str,
    problem_type: str,
    icp_score: int,
    priority: str,
    stage: str,
    opportunity_kind: str | None,
    signal_title: str,
    signal_description: str,
    source_url: str | None,
    signal_date: Any,
    scoring_notes: str | None,
    evidence_quotes: dict[str, Any] | list[Any] | None,
    gemini_model: str | None,
    prompt_version: str | None,
    country: str | None,
    mailing_address: str | None,
    website: str | None,
    business_address: str | None,
    contacts_summary: str | None,
    latest_revenue_usd: float | None,
    commit: bool = True,
) -> None:
    qo = qualified_silver_opportunities_sql()
    sql = f"""
    INSERT INTO {qo} (
      opportunity_id, silver_signal_id, silver_company_id,
      bronze_run_folder, bronze_ticker, cik10, company_name,
      company_industry, geography,
      signal_display, signal_source, problem_type, icp_score, priority, stage,
      opportunity_kind, signal_title, signal_description, source_url, signal_date,
      is_jooble, is_adzuna, is_apollo, is_govcon, is_sec_filing,
      country, mailing_address, website, business_address, contacts_summary,
      latest_revenue_usd,
      scoring_notes, evidence_quotes, gemini_model, prompt_version
    ) VALUES (
      %(opportunity_id)s::uuid, %(silver_signal_id)s::uuid, %(silver_company_id)s::uuid,
      %(bronze_run_folder)s, %(bronze_ticker)s, %(cik10)s, %(company_name)s,
      %(company_industry)s, %(geography)s,
      %(signal_display)s, %(signal_source)s, %(problem_type)s, %(icp_score)s, %(priority)s, %(stage)s,
      %(opportunity_kind)s, %(signal_title)s, %(signal_description)s, %(source_url)s, %(signal_date)s,
      false, false, false, false, true,
      %(country)s, %(mailing_address)s, %(website)s, %(business_address)s, %(contacts_summary)s,
      %(latest_revenue_usd)s,
      %(scoring_notes)s, %(evidence_quotes)s, %(gemini_model)s, %(prompt_version)s
    )
    ON CONFLICT (bronze_run_folder, bronze_ticker) DO UPDATE SET
      silver_signal_id = EXCLUDED.silver_signal_id,
      silver_company_id = EXCLUDED.silver_company_id,
      cik10 = EXCLUDED.cik10,
      company_name = EXCLUDED.company_name,
      company_industry = EXCLUDED.company_industry,
      geography = EXCLUDED.geography,
      signal_display = EXCLUDED.signal_display,
      signal_source = EXCLUDED.signal_source,
      problem_type = EXCLUDED.problem_type,
      icp_score = EXCLUDED.icp_score,
      priority = EXCLUDED.priority,
      stage = EXCLUDED.stage,
      opportunity_kind = EXCLUDED.opportunity_kind,
      signal_title = EXCLUDED.signal_title,
      signal_description = EXCLUDED.signal_description,
      source_url = EXCLUDED.source_url,
      signal_date = EXCLUDED.signal_date,
      country = EXCLUDED.country,
      mailing_address = EXCLUDED.mailing_address,
      website = EXCLUDED.website,
      business_address = EXCLUDED.business_address,
      contacts_summary = EXCLUDED.contacts_summary,
      latest_revenue_usd = EXCLUDED.latest_revenue_usd,
      scoring_notes = EXCLUDED.scoring_notes,
      evidence_quotes = EXCLUDED.evidence_quotes,
      gemini_model = EXCLUDED.gemini_model,
      prompt_version = EXCLUDED.prompt_version;
    """
    payload = {
        "opportunity_id": opportunity_id,
        "silver_signal_id": silver_signal_id,
        "silver_company_id": silver_company_id,
        "bronze_run_folder": bronze_run_folder,
        "bronze_ticker": bronze_ticker,
        "cik10": cik10,
        "company_name": company_name,
        "company_industry": company_industry,
        "geography": geography,
        "signal_display": signal_display,
        "signal_source": signal_source,
        "problem_type": problem_type,
        "icp_score": icp_score,
        "priority": priority,
        "stage": stage,
        "opportunity_kind": opportunity_kind,
        "signal_title": signal_title,
        "signal_description": signal_description,
        "source_url": source_url,
        "signal_date": signal_date,
        "country": country,
        "mailing_address": mailing_address,
        "website": website,
        "business_address": business_address,
        "contacts_summary": contacts_summary,
        "latest_revenue_usd": latest_revenue_usd,
        "scoring_notes": scoring_notes,
        "evidence_quotes": Json(evidence_quotes if evidence_quotes is not None else []),
        "gemini_model": gemini_model,
        "prompt_version": prompt_version,
    }
    with conn.cursor() as cur:
        cur.execute(sql, payload)
    if commit:
        conn.commit()


def upsert_sec_opportunities_batch(conn, items: Sequence[dict[str, Any]]) -> None:
    """Upsert many silver opportunity rows in **one** transaction."""
    if not items:
        return
    for it in items:
        upsert_sec_opportunity(conn, commit=False, **it)
    conn.commit()


def fetch_existing_silver_bronze_keys(
    conn,
    *,
    run_folders: Sequence[str],
) -> set[tuple[str, str]]:
    """``(bronze_run_folder, bronze_ticker)`` pairs already stored in silver opportunities."""
    rf = [str(x).strip() for x in run_folders if str(x).strip()]
    if not rf:
        return set()
    qo = qualified_silver_opportunities_sql()
    sql = f"SELECT bronze_run_folder, bronze_ticker FROM {qo} WHERE bronze_run_folder = ANY(%s)"
    with conn.cursor() as cur:
        cur.execute(sql, (rf,))
        raw = cur.fetchall()
    return {(str(a), str(b)) for a, b in raw}


def upsert_sec_insight(
    conn,
    *,
    opportunity_id: str,
    business_problem: str | None,
    why_opportunity: str | None,
    why_now_signals: str | None,
    solution_summary: str | None,
    outreach_draft: str | None,
    problem_category: str | None,
    severity_score: int | None,
    impact_summary: str | None,
    evidence_quotes: dict[str, Any] | list[Any] | None,
    gemini_model: str | None,
    prompt_version: str | None,
    commit: bool = True,
) -> None:
    qi = qualified_silver_insights_sql()
    sql = f"""
    INSERT INTO {qi} (
      opportunity_id,
      business_problem, why_opportunity, why_now_signals, solution_summary, outreach_draft,
      problem_category, severity_score, impact_summary, evidence_quotes,
      gemini_model, prompt_version
    ) VALUES (
      %(opportunity_id)s::uuid,
      %(business_problem)s, %(why_opportunity)s, %(why_now_signals)s, %(solution_summary)s, %(outreach_draft)s,
      %(problem_category)s, %(severity_score)s, %(impact_summary)s, %(evidence_quotes)s,
      %(gemini_model)s, %(prompt_version)s
    )
    ON CONFLICT (opportunity_id) DO UPDATE SET
      business_problem = EXCLUDED.business_problem,
      why_opportunity = EXCLUDED.why_opportunity,
      why_now_signals = EXCLUDED.why_now_signals,
      solution_summary = EXCLUDED.solution_summary,
      outreach_draft = EXCLUDED.outreach_draft,
      problem_category = EXCLUDED.problem_category,
      severity_score = EXCLUDED.severity_score,
      impact_summary = EXCLUDED.impact_summary,
      evidence_quotes = EXCLUDED.evidence_quotes,
      gemini_model = EXCLUDED.gemini_model,
      prompt_version = EXCLUDED.prompt_version,
      generated_at = now();
    """
    payload = {
        "opportunity_id": opportunity_id,
        "business_problem": business_problem,
        "why_opportunity": why_opportunity,
        "why_now_signals": why_now_signals,
        "solution_summary": solution_summary,
        "outreach_draft": outreach_draft,
        "problem_category": problem_category,
        "severity_score": severity_score,
        "impact_summary": impact_summary,
        "evidence_quotes": Json(evidence_quotes if evidence_quotes is not None else []),
        "gemini_model": gemini_model,
        "prompt_version": prompt_version,
    }
    with conn.cursor() as cur:
        cur.execute(sql, payload)
    if commit:
        conn.commit()


def upsert_sec_insights_batch(conn, items: Sequence[dict[str, Any]]) -> None:
    """Upsert many silver insight rows in **one** transaction."""
    if not items:
        return
    for it in items:
        upsert_sec_insight(conn, commit=False, **it)
    conn.commit()


def silver_opportunity_exists(conn, *, bronze_run_folder: str, bronze_ticker: str) -> bool:
    qo = qualified_silver_opportunities_sql()
    sql = f"SELECT 1 FROM {qo} WHERE bronze_run_folder = %s AND bronze_ticker = %s LIMIT 1;"
    with conn.cursor() as cur:
        cur.execute(sql, (bronze_run_folder, bronze_ticker))
        return cur.fetchone() is not None


def silver_insight_exists(conn, *, opportunity_id: str) -> bool:
    qi = qualified_silver_insights_sql()
    sql = f"SELECT 1 FROM {qi} WHERE opportunity_id = %s::uuid LIMIT 1;"
    with conn.cursor() as cur:
        cur.execute(sql, (opportunity_id,))
        return cur.fetchone() is not None


def fetch_bronze_sec_rows(
    conn,
    *,
    run_folder: str | None = None,
    tickers: list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    qb = qualified_bronze_table_sql()
    cols = (
        "run_folder, ticker, cik10, company_name, relevance_keywords, "
        "signal_hits_pain, signal_hits_strategic, signal_hits_vertical, "
        "tags_business, tags_products, tags_strategy, tags_risk, "
        "description_business_operations, description_products_services, "
        "description_strategy_outlook, description_risk_summary, "
        "officers_contacts, pdf_gemini_notes, combined_text_chars"
    )
    where: list[str] = ["1=1"]
    params: list[Any] = []
    if run_folder:
        where.append("run_folder = %s")
        params.append(run_folder)
    if tickers:
        where.append("ticker = ANY(%s)")
        params.append(tickers)
    sql = f"SELECT {cols} FROM {qb} WHERE {' AND '.join(where)} ORDER BY run_folder, ticker"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))
    sql += " OFFSET %s"
    params.append(int(offset))
    out: list[dict[str, Any]] = []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, tuple(params))
        for r in cur.fetchall():
            out.append(dict(r))
    return out


def fetch_silver_opportunities_for_insights(
    conn,
    *,
    run_folder: str | None = None,
    tickers: list[str] | None = None,
    only_missing_insight: bool = False,
    offset: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    qo = qualified_silver_opportunities_sql()
    qi = qualified_silver_insights_sql()
    where = ["1=1"]
    params: list[Any] = []
    if run_folder:
        where.append("o.bronze_run_folder = %s")
        params.append(run_folder)
    if tickers:
        where.append("o.bronze_ticker = ANY(%s)")
        params.append(tickers)
    join_missing = ""
    if only_missing_insight:
        join_missing = f"LEFT JOIN {qi} i ON i.opportunity_id = o.opportunity_id"
        where.append("i.opportunity_id IS NULL")
    sql = (
        f"SELECT o.* FROM {qo} o "
        f"{join_missing} "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY o.bronze_run_folder, o.bronze_ticker"
    )
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))
    sql += " OFFSET %s"
    params.append(int(offset))
    out: list[dict[str, Any]] = []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, tuple(params))
        for r in cur.fetchall():
            out.append(dict(r))
    return out


def fetch_bronze_row(
    conn, *, run_folder: str, ticker: str
) -> dict[str, Any] | None:
    rows = fetch_bronze_sec_rows(conn, run_folder=run_folder, tickers=[ticker], limit=1)
    return rows[0] if rows else None
