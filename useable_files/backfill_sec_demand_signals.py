"""
Backfill silver.demand_signals for SEC filing opportunities.

For each row in silver."SEC_filings_opportunities", ensures a silver.demand_signals row exists
with signal_id = that row's silver_signal_id (so gold.opportunities FK and merge u_f work).

- INSERT only; never DELETE, TRUNCATE, or UPDATE existing demand_signals rows.
- Uses ON CONFLICT (signal_id) DO NOTHING if the UUID already exists (idempotent).
- company_id is set only when silver_company_id exists in silver.companies; otherwise NULL (FK-safe).

Usage:
  python backfill_sec_demand_signals.py
  python backfill_sec_demand_signals.py --dry-run
  python backfill_sec_demand_signals.py --limit 50
"""

from __future__ import annotations

import argparse
import sys

from ingest_utils import get_connection


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill silver.demand_signals for SEC (insert-only).")
    p.add_argument("--dry-run", action="store_true", help="Count rows that would be inserted; no writes.")
    p.add_argument("--limit", type=int, default=None, help="Max SEC opportunities to process (testing).")
    args = p.parse_args()

    conn = get_connection()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        lim_clause = ""
        lim_outer = ""
        params: list = []
        if args.limit is not None and args.limit > 0:
            lim_clause = "LIMIT %s"
            lim_outer = "LIMIT %s"
            params.append(args.limit)

        # Distinct SEC signal ids not yet present in demand_signals (one backfill row per id).
        count_sql = f"""
        SELECT count(*) FROM (
            SELECT DISTINCT s.silver_signal_id
            FROM silver."SEC_filings_opportunities" AS s
            WHERE s.silver_signal_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM silver.demand_signals AS ds
                  WHERE ds.signal_id = s.silver_signal_id
              )
            {lim_clause}
        ) AS missing_ids
        """
        cur.execute(count_sql, params if params else None)
        (n_missing,) = cur.fetchone()
        lim_note = f" (capped by --limit {args.limit})" if args.limit else ""
        print(f"Distinct SEC silver_signal_id values missing from demand_signals{lim_note}: {n_missing}")

        if args.dry_run:
            conn.rollback()
            print("Dry run — no inserts.")
            return

        insert_sql = f"""
        INSERT INTO silver.demand_signals (
            signal_id,
            signal_type,
            signal_subtype,
            title,
            description,
            company_name,
            company_id,
            location_city,
            location_state,
            location_country,
            signal_date,
            source,
            source_url,
            is_jooble,
            is_adzuna,
            is_apollo,
            is_govcon,
            relevance_keywords
        )
        SELECT
            x.silver_signal_id,
            'sec_filing',
            left(coalesce(nullif(trim(x.opportunity_kind), ''), 'sec_filing'), 512),
            left(
                coalesce(
                    nullif(trim(x.signal_title), ''),
                    nullif(trim(x.company_name), ''),
                    'SEC filing'
                ),
                2000
            ),
            left(coalesce(x.signal_description, ''), 2000),
            x.company_name,
            CASE
                WHEN x.silver_company_id IS NOT NULL
                     AND EXISTS (
                         SELECT 1 FROM silver.companies AS c
                         WHERE c.company_id = x.silver_company_id
                     )
                THEN x.silver_company_id
                ELSE NULL::uuid
            END,
            NULL::text,
            NULL::text,
            NULL::text,
            x.signal_date,
            left(coalesce(nullif(trim(x.signal_source), ''), 'sec'), 64),
            x.source_url,
            false,
            false,
            false,
            false,
            NULL::jsonb
        FROM (
            SELECT DISTINCT ON (s.silver_signal_id)
                s.silver_signal_id,
                s.opportunity_kind,
                s.signal_title,
                s.company_name,
                s.signal_description,
                s.silver_company_id,
                s.signal_date,
                s.signal_source,
                s.source_url
            FROM silver."SEC_filings_opportunities" AS s
            WHERE s.silver_signal_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM silver.demand_signals AS ds
                  WHERE ds.signal_id = s.silver_signal_id
              )
            ORDER BY
                s.silver_signal_id,
                s.signal_date DESC NULLS LAST,
                s.opportunity_id
        ) AS x
        {lim_outer}
        ON CONFLICT (signal_id) DO NOTHING
        """
        ins_params = list(params) if lim_outer else []
        cur.execute(insert_sql, ins_params if ins_params else None)
        inserted = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        conn.commit()
        print(f"Insert finished (rowcount from driver): {inserted}")
        print("Committed. Existing demand_signals rows were not modified or removed.")

        cur.execute(
            """
            SELECT count(*)
            FROM silver."SEC_filings_opportunities" s
            WHERE s.silver_signal_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM silver.demand_signals ds WHERE ds.signal_id = s.silver_signal_id
              )
            """
        )
        (still_missing,) = cur.fetchone()
        print(f"SEC rows still missing demand_signals after run: {still_missing}")
    except Exception:
        conn.rollback()
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
