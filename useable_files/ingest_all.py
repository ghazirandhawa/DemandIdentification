"""
Full pipeline: bronze → silver → opportunities → SEC signal backfill → gold merge.

Usage:
  python ingest_all.py

Prerequisites:
  - create_schema.py has been applied (schemas bronze/silver/gold and tables exist)
  - db_config.py with valid Cockroach credentials
  - Raw JSON files in this directory (see ingest_bronze.py)

Order:
  1. ingest_bronze — JSON → bronze.*
  2. ingest_silver — bronze.* → silver.*
  3. ingest_opportunities — silver.opportunities (+ insights)
  4. backfill_sec_demand_signals — ensure SEC rows’ signal_ids exist in silver.demand_signals
  5. merge_sec_silver_to_gold — dedupe SEC + silver into gold.opportunities / gold.opportunity_insights
"""

from __future__ import annotations

import sys

from ingest_utils import get_connection


def print_counts() -> None:
    conn = get_connection()
    cur = conn.cursor()
    tables = [
        "bronze.jooble_jobs",
        "bronze.adzuna_jobs",
        "bronze.apollo_organizations",
        "bronze.apollo_people",
        "bronze.apollo_job_postings",
        "bronze.govcon_opportunities",
        "bronze.govcon_awards",
        "silver.companies",
        "silver.job_postings",
        "silver.contacts",
        "silver.govcon_opportunities",
        "silver.govcon_awards",
        "silver.demand_signals",
        "silver.opportunities",
        "silver.opportunity_insights",
        "gold.opportunities",
        "gold.opportunity_insights",
    ]
    print("\nRow counts:")
    for t in tables:
        cur.execute(f"SELECT count(*) FROM {t}")
        (n,) = cur.fetchone()
        print(f"  {t}: {n}")
    cur.close()
    conn.close()


def main() -> None:
    import backfill_sec_demand_signals
    import ingest_bronze
    import ingest_opportunities
    import ingest_silver
    import merge_sec_silver_to_gold

    ingest_bronze.main()
    ingest_silver.main()
    ingest_opportunities.main()
    backfill_sec_demand_signals.main()
    merge_sec_silver_to_gold.main()
    print_counts()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ingest_all failed: {e}", file=sys.stderr)
        sys.exit(1)
