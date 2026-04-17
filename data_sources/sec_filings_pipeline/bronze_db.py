from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import Any

import psycopg2
from psycopg2.extras import Json

_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validated_ident(raw: str, *, env_name: str) -> str:
    s = (raw or "").strip()
    if not _IDENT.fullmatch(s):
        raise RuntimeError(
            f"Invalid {env_name}={raw!r}; use a single SQL identifier "
            "(letters, digits, underscore; must not start with a digit)."
        )
    return s


def bronze_table_ids() -> tuple[str, str]:
    """Return ``(schema, table)`` unquoted names: default ``bronze``, ``sec_filings``."""
    schema = _validated_ident(os.environ.get("BRONZE_SCHEMA") or "bronze", env_name="BRONZE_SCHEMA")
    table = _validated_ident(os.environ.get("BRONZE_TABLE") or "sec_filings", env_name="BRONZE_TABLE")
    return schema, table


def bronze_schema_sql() -> str:
    """Quoted schema name for DDL/DML (default: ``bronze``)."""
    s, _ = bronze_table_ids()
    return f'"{s}"'


def bronze_table_sql() -> str:
    """Quoted table name only (default: ``sec_filings`` — no ``BRONZE_`` prefix)."""
    _, t = bronze_table_ids()
    return f'"{t}"'


def qualified_bronze_table_sql() -> str:
    """``"bronze"."sec_filings"`` style name for SQL statements."""
    s, t = bronze_table_ids()
    return f'"{s}"."{t}"'


def bronze_table_fqn() -> str:
    """Human-readable ``bronze.sec_filings`` for logs and JSON."""
    s, t = bronze_table_ids()
    return f"{s}.{t}"


def _ddl_qualified_table() -> str:
    sc = bronze_schema_sql()
    tb = bronze_table_sql()
    return f"""CREATE TABLE IF NOT EXISTS {sc}.{tb} (
  run_folder TEXT NOT NULL,
  ticker TEXT NOT NULL,
  cik10 TEXT,
  company_name TEXT,
  public_float_usd DOUBLE PRECISION,
  public_float_source TEXT,
  relevance_keywords TEXT,
  signal_hits_pain TEXT,
  signal_hits_strategic TEXT,
  signal_hits_vertical TEXT,
  tags_business TEXT,
  tags_products TEXT,
  tags_strategy TEXT,
  tags_risk TEXT,
  description_business_operations TEXT,
  description_products_services TEXT,
  description_strategy_outlook TEXT,
  description_risk_summary TEXT,
  officers_contacts JSONB,
  pdf_gemini_notes TEXT,
  combined_text_chars BIGINT,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_folder, ticker)
);
"""


def _alter_statements() -> list[str]:
    q = qualified_bronze_table_sql()
    return [
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS company_name TEXT;",
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS signal_hits_pain TEXT;",
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS signal_hits_strategic TEXT;",
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS signal_hits_vertical TEXT;",
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS tags_business TEXT;",
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS tags_products TEXT;",
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS tags_strategy TEXT;",
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS tags_risk TEXT;",
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS description_business_operations TEXT;",
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS description_products_services TEXT;",
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS description_strategy_outlook TEXT;",
        f"ALTER TABLE {q} ADD COLUMN IF NOT EXISTS description_risk_summary TEXT;",
    ]


def connect():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg2.connect(url)


def ensure_table(conn, *, recreate: bool = False) -> None:
    """
    Ensure schema ``bronze`` (or ``BRONZE_SCHEMA``) exists, create ``sec_filings``
    (or ``BRONZE_TABLE``) if missing, and apply additive migrations.

    If ``recreate`` is true, drop the qualified table first (all rows removed), then create fresh.
    Default is false: keep existing data and upsert as usual.
    """
    sc = bronze_schema_sql()
    qt = qualified_bronze_table_sql()
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {sc};")
        if recreate:
            cur.execute(f"DROP TABLE IF EXISTS {qt};")
        cur.execute(_ddl_qualified_table())
        for stmt in _alter_statements():
            cur.execute(stmt)
    conn.commit()


def upsert_row(
    conn,
    *,
    run_folder: str,
    ticker: str,
    cik10: str | None,
    company_name: str | None,
    public_float_usd: float | None,
    public_float_source: str | None,
    relevance_keywords: str,
    signal_hits_pain: str,
    signal_hits_strategic: str,
    signal_hits_vertical: str,
    tags_business: str,
    tags_products: str,
    tags_strategy: str,
    tags_risk: str,
    description_business_operations: str,
    description_products_services: str,
    description_strategy_outlook: str,
    description_risk_summary: str,
    officers_contacts: dict[str, Any] | list[Any] | None,
    pdf_gemini_notes: str,
    combined_text_chars: int,
    commit: bool = True,
) -> None:
    qt = qualified_bronze_table_sql()
    sql = f"""
    INSERT INTO {qt} (
      run_folder, ticker, cik10, company_name,
      public_float_usd, public_float_source,
      relevance_keywords,
      signal_hits_pain, signal_hits_strategic, signal_hits_vertical,
      tags_business, tags_products, tags_strategy, tags_risk,
      description_business_operations, description_products_services,
      description_strategy_outlook, description_risk_summary,
      officers_contacts, pdf_gemini_notes, combined_text_chars
    ) VALUES (
      %(run_folder)s, %(ticker)s, %(cik10)s, %(company_name)s,
      %(public_float_usd)s, %(public_float_source)s,
      %(relevance_keywords)s,
      %(signal_hits_pain)s, %(signal_hits_strategic)s, %(signal_hits_vertical)s,
      %(tags_business)s, %(tags_products)s, %(tags_strategy)s, %(tags_risk)s,
      %(description_business_operations)s, %(description_products_services)s,
      %(description_strategy_outlook)s, %(description_risk_summary)s,
      %(officers_contacts)s, %(pdf_gemini_notes)s, %(combined_text_chars)s
    )
    ON CONFLICT (run_folder, ticker) DO UPDATE SET
      cik10 = EXCLUDED.cik10,
      company_name = EXCLUDED.company_name,
      public_float_usd = EXCLUDED.public_float_usd,
      public_float_source = EXCLUDED.public_float_source,
      relevance_keywords = EXCLUDED.relevance_keywords,
      signal_hits_pain = EXCLUDED.signal_hits_pain,
      signal_hits_strategic = EXCLUDED.signal_hits_strategic,
      signal_hits_vertical = EXCLUDED.signal_hits_vertical,
      tags_business = EXCLUDED.tags_business,
      tags_products = EXCLUDED.tags_products,
      tags_strategy = EXCLUDED.tags_strategy,
      tags_risk = EXCLUDED.tags_risk,
      description_business_operations = EXCLUDED.description_business_operations,
      description_products_services = EXCLUDED.description_products_services,
      description_strategy_outlook = EXCLUDED.description_strategy_outlook,
      description_risk_summary = EXCLUDED.description_risk_summary,
      officers_contacts = EXCLUDED.officers_contacts,
      pdf_gemini_notes = EXCLUDED.pdf_gemini_notes,
      combined_text_chars = EXCLUDED.combined_text_chars,
      processed_at = now();
    """
    payload = {
        "run_folder": run_folder,
        "ticker": ticker,
        "cik10": cik10,
        "company_name": company_name,
        "public_float_usd": public_float_usd,
        "public_float_source": public_float_source,
        "relevance_keywords": relevance_keywords,
        "signal_hits_pain": signal_hits_pain,
        "signal_hits_strategic": signal_hits_strategic,
        "signal_hits_vertical": signal_hits_vertical,
        "tags_business": tags_business,
        "tags_products": tags_products,
        "tags_strategy": tags_strategy,
        "tags_risk": tags_risk,
        "description_business_operations": description_business_operations,
        "description_products_services": description_products_services,
        "description_strategy_outlook": description_strategy_outlook,
        "description_risk_summary": description_risk_summary,
        "officers_contacts": Json(officers_contacts if officers_contacts is not None else {}),
        "pdf_gemini_notes": pdf_gemini_notes,
        "combined_text_chars": combined_text_chars,
    }
    with conn.cursor() as cur:
        cur.execute(sql, payload)
    if commit:
        conn.commit()


def upsert_rows_batch(conn, rows: Sequence[dict[str, Any]]) -> None:
    """Upsert many bronze rows in **one** transaction (one commit at the end)."""
    if not rows:
        return
    for r in rows:
        upsert_row(
            conn,
            commit=False,
            run_folder=str(r["run_folder"]),
            ticker=str(r["ticker"]),
            cik10=r["cik10"] if r["cik10"] else None,
            company_name=r.get("company_name") if r.get("company_name") else None,
            public_float_usd=r["public_float_usd"] if isinstance(r["public_float_usd"], (int, float)) else None,
            public_float_source=str(r["public_float_source"]),
            relevance_keywords=str(r["relevance_keywords"]),
            signal_hits_pain=str(r.get("signal_hits_pain") or ""),
            signal_hits_strategic=str(r.get("signal_hits_strategic") or ""),
            signal_hits_vertical=str(r.get("signal_hits_vertical") or ""),
            tags_business=str(r.get("tags_business") or ""),
            tags_products=str(r.get("tags_products") or ""),
            tags_strategy=str(r.get("tags_strategy") or ""),
            tags_risk=str(r.get("tags_risk") or ""),
            description_business_operations=str(r.get("description_business_operations") or ""),
            description_products_services=str(r.get("description_products_services") or ""),
            description_strategy_outlook=str(r.get("description_strategy_outlook") or ""),
            description_risk_summary=str(r.get("description_risk_summary") or ""),
            officers_contacts=r["officers_contacts"] if isinstance(r["officers_contacts"], dict) else {},
            pdf_gemini_notes=str(r["pdf_gemini_notes"]),
            combined_text_chars=int(r["combined_text_chars"]),
        )
    conn.commit()
