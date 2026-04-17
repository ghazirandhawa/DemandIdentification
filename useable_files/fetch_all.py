"""
Run all data fetch scripts in sequence (writes JSON under this repo per each script).

Usage:
  python fetch_all.py

Order:
  1. fetch_apollo_data
  2. fetch_apollo_enrichment
  3. fetch_gdelt_signals
  4. fetch_govcon_rfps
  5. fetch_jooble_jobs
  6. fetch_adzuna_jobs

Each step uses that script’s own argparse on sys.argv (same as running it directly).
"""

from __future__ import annotations

import sys


def main() -> None:
    import fetch_adzuna_jobs
    import fetch_apollo_data
    import fetch_apollo_enrichment
    import fetch_gdelt_signals
    import fetch_govcon_rfps
    import fetch_jooble_jobs

    fetch_apollo_data.main()
    fetch_apollo_enrichment.main()
    fetch_gdelt_signals.main()
    fetch_govcon_rfps.main()
    fetch_jooble_jobs.main()
    fetch_adzuna_jobs.main()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"fetch_all failed: {e}", file=sys.stderr)
        sys.exit(1)
