"""
Merge SEC + non-SEC silver opportunities into gold (dedupe by company_key, best by score).

Sources (read-only on silver; never DROP/TRUNCATE silver):
  - silver."SEC_filings_opportunities"
  - silver.opportunities

Insights (reuse-first; no Gemini):
  - silver."SEC_filings_opportunities_insights" when the winning row is from SEC
  - silver.opportunities_insights_fixed when the winning row is from silver.opportunities

Gold writes (each run, by default):
  - DROP + CREATE gold.opportunity_insights and gold.opportunities (then indexes), then reload data.
    (Avoids TRUNCATE, which can be problematic on some CockroachDB setups.)
  - Skips any candidate whose silver_signal_id is missing from silver.demand_signals (SEC filings
    often use ids not present in demand_signals).
  - INSERT deduped rows into gold.opportunities (common columns only; preserves winner opportunity_id).
    silver_company_id is NULLed when absent from silver.companies (FK-safe for SEC rows).
  - INSERT INTO gold.opportunity_insights ... ON CONFLICT (opportunity_id) DO UPDATE
  - UPDATE gold.opportunities SET is_sec_filing = true only when the joined insight row has
    prompt_version = 'sec_ins_v2'; otherwise false (including opportunities with no insight).

After a successful commit, prints post-commit validation on a fresh connection (per-table counts,
distinct id checks, and insight coverage) so numbers align with what you see in the DB console.

Usage:
  python merge_sec_silver_to_gold.py
  python merge_sec_silver_to_gold.py --dry-run
  python merge_sec_silver_to_gold.py --preserve-gold   # skip DROP/CREATE; append/upsert (advanced)
  python merge_sec_silver_to_gold.py --no-truncate     # same as --preserve-gold
"""

from __future__ import annotations

import argparse
import sys

from ingest_utils import get_connection

# Shared CTE chain: sec UNION ALL silver -> company_key -> window dedupe -> winners
_MERGE_WINNERS_CTE = """
WITH sec AS (
    SELECT
        'sec'::text AS merge_source,
        s.opportunity_id,
        s.silver_signal_id,
        s.silver_company_id,
        s.company_name,
        s.company_industry,
        s.geography,
        s.signal_display,
        s.signal_source,
        s.problem_type,
        s.icp_score,
        s.priority,
        s.stage,
        s.opportunity_kind,
        s.signal_title,
        s.signal_description,
        s.source_url,
        s.signal_date,
        false AS is_jooble,
        false AS is_adzuna,
        false AS is_apollo,
        false AS is_govcon,
        NULL::text AS scoring_notes,
        lower(trim(regexp_replace(coalesce(s.company_name, ''), '[[:space:]]+', ' ', 'g'))) AS company_key
    FROM silver."SEC_filings_opportunities" AS s
),
main AS (
    SELECT
        'silver'::text AS merge_source,
        o.opportunity_id,
        o.silver_signal_id,
        o.silver_company_id,
        o.company_name,
        o.company_industry,
        o.geography,
        o.signal_display,
        o.signal_source,
        o.problem_type,
        o.icp_score,
        o.priority,
        o.stage,
        o.opportunity_kind,
        o.signal_title,
        o.signal_description,
        o.source_url,
        o.signal_date,
        coalesce(o.is_jooble, false) AS is_jooble,
        coalesce(o.is_adzuna, false) AS is_adzuna,
        coalesce(o.is_apollo, false) AS is_apollo,
        coalesce(o.is_govcon, false) AS is_govcon,
        o.scoring_notes,
        lower(trim(regexp_replace(coalesce(o.company_name, ''), '[[:space:]]+', ' ', 'g'))) AS company_key
    FROM silver.opportunities AS o
),
u AS (
    SELECT * FROM sec
    UNION ALL
    SELECT * FROM main
),
u_f AS (
    SELECT u.*
    FROM u
    WHERE EXISTS (
        SELECT 1 FROM silver.demand_signals AS ds WHERE ds.signal_id = u.silver_signal_id
    )
),
winners AS (
    SELECT *
    FROM (
        SELECT
            f.*,
            row_number() OVER (
                PARTITION BY f.company_key
                ORDER BY
                    f.icp_score DESC NULLS LAST,
                    f.signal_date DESC NULLS LAST,
                    f.opportunity_id
            ) AS rn
        FROM u_f AS f
    ) ranked
    WHERE ranked.rn = 1
)
"""


def _recreate_gold_tables(cur) -> None:
    """Drop and recreate gold opportunity tables (matches create_schema.py). No silver writes."""
    cur.execute('DROP TABLE IF EXISTS gold.opportunity_insights CASCADE')
    cur.execute('DROP TABLE IF EXISTS gold.opportunities CASCADE')
    cur.execute(
        """
        CREATE TABLE gold.opportunities (
            opportunity_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            silver_signal_id    UUID NOT NULL REFERENCES silver.demand_signals(signal_id) ON DELETE CASCADE,
            silver_company_id   UUID REFERENCES silver.companies(company_id) ON DELETE SET NULL,
            company_name        TEXT NOT NULL,
            company_industry    TEXT,
            geography           TEXT,
            signal_display        TEXT NOT NULL,
            signal_source         TEXT,
            problem_type          TEXT,
            icp_score             INT4 NOT NULL,
            priority              TEXT NOT NULL,
            stage                 TEXT NOT NULL DEFAULT 'Signals Identified',
            opportunity_kind      TEXT,
            signal_title          TEXT NOT NULL,
            signal_description    TEXT,
            source_url            TEXT,
            signal_date           TIMESTAMPTZ,
            is_jooble             BOOL DEFAULT false,
            is_adzuna             BOOL DEFAULT false,
            is_apollo             BOOL DEFAULT false,
            is_govcon             BOOL DEFAULT false,
            is_sec_filing         BOOL NOT NULL DEFAULT false,
            scoring_notes         TEXT,
            created_at            TIMESTAMPTZ DEFAULT now(),
            updated_at            TIMESTAMPTZ DEFAULT now(),
            UNIQUE (silver_signal_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE gold.opportunity_insights (
            insight_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            opportunity_id       UUID NOT NULL REFERENCES gold.opportunities(opportunity_id) ON DELETE CASCADE,
            business_problem      TEXT,
            why_opportunity         TEXT,
            solution_summary        TEXT,
            outreach_draft          TEXT,
            problem_category        TEXT,
            severity_score          INT4,
            impact_summary          TEXT,
            gemini_model            TEXT,
            prompt_version          TEXT,
            generated_at            TIMESTAMPTZ DEFAULT now(),
            UNIQUE (opportunity_id)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS gold_opps_company_idx ON gold.opportunities (silver_company_id)"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS gold_opps_priority_idx ON gold.opportunities (priority)")
    cur.execute("CREATE INDEX IF NOT EXISTS gold_opps_icp_idx ON gold.opportunities (icp_score DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS gold_opps_stage_idx ON gold.opportunities (stage)")
    cur.execute("CREATE INDEX IF NOT EXISTS gold_opps_problem_idx ON gold.opportunities (problem_type)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS gold_opps_signal_date_idx ON gold.opportunities (signal_date DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS gold_insights_opp_idx ON gold.opportunity_insights (opportunity_id)"
    )


def _ensure_is_sec_filing_column(cur) -> None:
    """If gold was preserved from an older schema, add is_sec_filing (no-op when column exists)."""
    cur.execute(
        """
        ALTER TABLE gold.opportunities
        ADD COLUMN IF NOT EXISTS is_sec_filing BOOL NOT NULL DEFAULT false
        """
    )


def _backfill_is_sec_filing_from_insights(cur) -> None:
    """
    Set gold.opportunities.is_sec_filing from gold.opportunity_insights.prompt_version:
    true only when prompt_version = 'sec_ins_v2'; otherwise false (including no insight row).
    """
    cur.execute(
        """
        UPDATE gold.opportunities AS o
        SET is_sec_filing = COALESCE(
            (
                SELECT CASE WHEN i.prompt_version = 'sec_ins_v2' THEN true ELSE false END
                FROM gold.opportunity_insights AS i
                WHERE i.opportunity_id = o.opportunity_id
            ),
            false
        )
        """
    )


def _dry_run(cur) -> None:
    cur.execute(
        f"""
        {_MERGE_WINNERS_CTE}
        SELECT
            (SELECT count(*) FROM u) AS all_candidates,
            (SELECT count(*) FROM u_f) AS fk_ok_candidates,
            (SELECT count(DISTINCT company_key) FROM u_f) AS distinct_company_keys,
            (SELECT count(*) FROM winners) AS winner_rows
        """
    )
    row = cur.fetchone()
    all_c, fk_ok, dck, win_n = row[0], row[1], row[2], row[3]
    skipped = int(all_c) - int(fk_ok)
    print("Dry run (no gold writes):")
    print(f"  All candidate rows (SEC + silver): {all_c}")
    print(f"  Skipped (silver_signal_id not in silver.demand_signals): {skipped}")
    print(f"  FK-ok candidates: {fk_ok}")
    print(f"  Distinct company_key (FK-ok): {dck}")
    print(f"  Winner rows (after dedupe): {win_n}")


def _merge_to_gold(cur, *, recreate_gold: bool) -> None:
    if recreate_gold:
        print("Gold: DROP + CREATE gold.opportunities / gold.opportunity_insights (default full reload) …")
        _recreate_gold_tables(cur)
    else:
        print("Gold: preserving existing tables (--preserve-gold / --no-truncate); DROP/CREATE skipped.")
        _ensure_is_sec_filing_column(cur)

    cur.execute(
        f"""
        {_MERGE_WINNERS_CTE}
        INSERT INTO gold.opportunities (
            opportunity_id,
            silver_signal_id,
            silver_company_id,
            company_name,
            company_industry,
            geography,
            signal_display,
            signal_source,
            problem_type,
            icp_score,
            priority,
            stage,
            opportunity_kind,
            signal_title,
            signal_description,
            source_url,
            signal_date,
            is_jooble,
            is_adzuna,
            is_apollo,
            is_govcon,
            scoring_notes
        )
        SELECT
            w.opportunity_id,
            w.silver_signal_id,
            CASE
                WHEN w.silver_company_id IS NOT NULL
                     AND EXISTS (
                         SELECT 1 FROM silver.companies AS c
                         WHERE c.company_id = w.silver_company_id
                     )
                THEN w.silver_company_id
                ELSE NULL::uuid
            END,
            w.company_name,
            w.company_industry,
            w.geography,
            w.signal_display,
            w.signal_source,
            w.problem_type,
            w.icp_score,
            w.priority,
            w.stage,
            w.opportunity_kind,
            coalesce(nullif(trim(w.signal_title), ''), '(no title)') AS signal_title,
            w.signal_description,
            w.source_url,
            w.signal_date,
            w.is_jooble,
            w.is_adzuna,
            w.is_apollo,
            w.is_govcon,
            w.scoring_notes
        FROM winners AS w
        """
    )

    # SEC insights (common columns only)
    cur.execute(
        f"""
        {_MERGE_WINNERS_CTE}
        INSERT INTO gold.opportunity_insights (
            opportunity_id,
            business_problem,
            why_opportunity,
            solution_summary,
            outreach_draft,
            problem_category,
            severity_score,
            impact_summary,
            gemini_model,
            prompt_version
        )
        SELECT
            w.opportunity_id,
            i.business_problem,
            i.why_opportunity,
            i.solution_summary,
            i.outreach_draft,
            i.problem_category,
            i.severity_score,
            i.impact_summary,
            i.gemini_model,
            i.prompt_version
        FROM winners AS w
        INNER JOIN silver."SEC_filings_opportunities_insights" AS i
            ON i.opportunity_id = w.opportunity_id
        WHERE w.merge_source = 'sec'
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
    )

    # Non-SEC insights from silver.opportunities_insights_fixed
    cur.execute(
        f"""
        {_MERGE_WINNERS_CTE}
        INSERT INTO gold.opportunity_insights (
            opportunity_id,
            business_problem,
            why_opportunity,
            solution_summary,
            outreach_draft,
            problem_category,
            severity_score,
            impact_summary,
            gemini_model,
            prompt_version
        )
        SELECT
            w.opportunity_id,
            f.business_problem,
            f.why_opportunity,
            f.solution_summary,
            f.outreach_draft,
            f.problem_category,
            f.severity_score,
            f.impact_summary,
            f.gemini_model,
            f.prompt_version
        FROM winners AS w
        INNER JOIN silver.opportunities_insights_fixed AS f
            ON f.opportunity_id = w.opportunity_id
        WHERE w.merge_source = 'silver'
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
    )

    print("Gold: backfill opportunities.is_sec_filing from opportunity_insights.prompt_version …")
    _backfill_is_sec_filing_from_insights(cur)


def _validate_gold_detailed() -> None:
    """
    Re-query gold using a fresh connection after COMMIT so counts match other clients / DB UI.
    Validates each gold table separately with extra sanity checks.
    """
    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        print("\n=== Post-commit validation (fresh DB connection) ===")
        cur.execute("SELECT current_database(), current_schema()")
        db, schema = cur.fetchone()
        print(f"  current_database={db!r}  current_schema={schema!r}")

        # --- gold.opportunities ---
        cur.execute("SELECT count(*) FROM gold.opportunities")
        (n_opp_rows,) = cur.fetchone()
        cur.execute("SELECT count(DISTINCT opportunity_id) FROM gold.opportunities")
        (n_opp_distinct_id,) = cur.fetchone()
        cur.execute("SELECT count(DISTINCT silver_signal_id) FROM gold.opportunities")
        (n_opp_distinct_sig,) = cur.fetchone()
        cur.execute(
            "SELECT min(created_at), max(created_at) FROM gold.opportunities"
        )
        tmin, tmax = cur.fetchone()
        print("\n  [gold.opportunities]")
        print(f"    count(*)                = {n_opp_rows}")
        print(f"    count(distinct opp_id)  = {n_opp_distinct_id}")
        print(f"    count(distinct signal)  = {n_opp_distinct_sig}")
        print(f"    created_at min / max    = {tmin} / {tmax}")
        if int(n_opp_rows or 0) != int(n_opp_distinct_id or 0):
            print(
                "    WARNING: count(*) != count(distinct opportunity_id) — duplicate PK risk.",
                file=sys.stderr,
            )

        # --- gold.opportunity_insights ---
        cur.execute("SELECT count(*) FROM gold.opportunity_insights")
        (n_ins_rows,) = cur.fetchone()
        cur.execute("SELECT count(DISTINCT opportunity_id) FROM gold.opportunity_insights")
        (n_ins_distinct_opp,) = cur.fetchone()
        cur.execute("SELECT count(DISTINCT insight_id) FROM gold.opportunity_insights")
        (n_ins_distinct_id,) = cur.fetchone()
        cur.execute(
            "SELECT min(generated_at), max(generated_at) FROM gold.opportunity_insights"
        )
        gmin, gmax = cur.fetchone()
        print("\n  [gold.opportunity_insights]")
        print(f"    count(*)                = {n_ins_rows}")
        print(f"    count(distinct opp_id)  = {n_ins_distinct_opp}")
        print(f"    count(distinct insight) = {n_ins_distinct_id}")
        print(f"    generated_at min / max  = {gmin} / {gmax}")
        if int(n_ins_rows or 0) != int(n_ins_distinct_opp or 0):
            print(
                "    WARNING: count(*) != count(distinct opportunity_id) — UNIQUE(opp_id) violated?",
                file=sys.stderr,
            )

        # --- join coverage ---
        cur.execute(
            """
            SELECT count(*)
            FROM gold.opportunities o
            LEFT JOIN gold.opportunity_insights i ON i.opportunity_id = o.opportunity_id
            WHERE i.opportunity_id IS NULL
            """
        )
        (n_missing,) = cur.fetchone()
        print("\n  [coverage]")
        print(f"    opportunities with no insight row = {n_missing}")
        if n_opp_rows is not None and int(n_opp_rows) > 0:
            with_ins = int(n_opp_rows) - int(n_missing or 0)
            print(f"    opportunities with an insight   = {with_ins}")
    finally:
        cur.close()
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Merge SEC + silver opportunities into gold (dedupe by company).")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print candidate/winner counts; do not modify gold.",
    )
    p.add_argument(
        "--preserve-gold",
        action="store_true",
        help="Do not DROP/CREATE gold tables: keep existing gold data (may hit duplicate PK / UNIQUE(silver_signal_id)).",
    )
    p.add_argument(
        "--no-truncate",
        action="store_true",
        help="Alias of --preserve-gold (legacy name; does not use TRUNCATE anymore).",
    )
    args = p.parse_args()

    conn = get_connection()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        if args.dry_run:
            _dry_run(cur)
            conn.rollback()
            return

        preserve = bool(args.preserve_gold or args.no_truncate)
        _merge_to_gold(cur, recreate_gold=not preserve)
        conn.commit()
        print("\nMerge committed OK.")
        _validate_gold_detailed()
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
