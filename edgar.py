#!/usr/bin/env python3
"""
EDGAR helpers: list 10-K metadata, pull disclosure JSON/HTML to disk, or bulk sync.

From the repository root (uses ``data_sources/edgar_disclosures/`` by default):

  python edgar.py list AAPL 5
  python edgar.py pull AAPL --quarters 8
  python edgar.py bulk --max 200 --max-latest-10k-mb 12 --quarters 8

``SEC_USER_AGENT`` is read from ``.env`` (see ``.env.example``).

By default, ``pull`` and ``bulk`` skip filers whose latest fiscal-year USD revenue
(SEC ``companyfacts``) is above ``$1B``; use ``--no-revenue-filter`` to disable
or ``--max-revenue-usd`` to change the cutoff.

Progress (ticker / 10-K index / save counts) is written to stderr by default so
stdout stays JSON-only; use ``--no-progress`` to disable.

Each ``pull`` or ``bulk`` run writes under ``{store}/{UTC_TIMESTAMP}/{TICKER}/``:
``ALL_NARRATIVE_INFO.json`` (unless ``--no-ix-narrative``), ``tenk_documents/``
(primary 10-K filing plus any PDFs from ``index.json``), submissions, facts, and
metadata. Use ``--years N`` instead of ``--quarters`` for a year-based window.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from requests import HTTPError

_ROOT = Path(__file__).resolve().parent
_ds = _ROOT / "data_sources"
if _ds.is_dir() and str(_ds) not in sys.path:
    sys.path.insert(0, str(_ds))

from SEC_DATA import (  # noqa: E402
    DEFAULT_MAX_REVENUE_USD,
    DEFAULT_STORE_ROOT,
    EdgarClient,
    bulk_sync_from_tickers,
    combined_exclusions,
    fetch_company_facts,
    list_10k_filings,
    load_extra_cik10,
    revenue_exceeds_threshold,
    sync_company,
    ticker_to_cik_str,
)


def _require_ua(p: argparse.ArgumentParser) -> None:
    if not os.environ.get("SEC_USER_AGENT", "").strip():
        p.error(
            "Set SEC_USER_AGENT in a .env file (see .env.example) or in the environment."
        )


def cmd_list(args: argparse.Namespace) -> int:
    client = EdgarClient()
    ident = args.identifier.strip()
    cik = ident if ident.isdigit() else ticker_to_cik_str(client, ident)
    rows = list_10k_filings(client, cik, limit=args.limit)
    json.dump([r.to_dict() for r in rows], sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    client = EdgarClient()
    ident = args.identifier.strip()
    cik = ident if ident.isdigit() else ticker_to_cik_str(client, ident)
    root = Path(args.store) if args.store else DEFAULT_STORE_ROOT
    facts_base = None
    if not args.no_revenue_filter:
        cap = float(args.max_revenue_usd) if args.max_revenue_usd is not None else DEFAULT_MAX_REVENUE_USD
        try:
            facts_base = fetch_company_facts(client, cik)
        except HTTPError as exc:
            print(f"Could not load companyfacts for revenue check: {exc}", file=sys.stderr)
            return 1
        excl, rev = revenue_exceeds_threshold(facts_base, cap)
        if excl:
            print(
                json.dumps(
                    {
                        "skipped": True,
                        "reason": "revenue_above_threshold",
                        "revenue_usd": rev,
                        "max_revenue_usd": cap,
                        "cik": cik,
                    },
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 2
    prog = None if args.no_progress else sys.stderr
    info = sync_company(
        client,
        cik,
        root,
        include_full_companyfacts=args.full_facts,
        download_tenk_primary_documents=not args.no_primary_docs,
        download_index_pdfs=not args.no_index_pdfs,
        tenk_index_limit=args.tenk_limit,
        ticker=None if ident.isdigit() else ident.upper(),
        company_title=None,
        years=args.years,
        quarters=args.quarters,
        export_narrative_ixbrl=not args.no_ix_narrative,
        include_supplemental_submissions=args.include_supplemental_submissions,
        companyfacts_base=facts_base,
        progress_stream=prog,
    )
    json.dump(info, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_bulk(args: argparse.Namespace) -> int:
    client = EdgarClient()
    root = Path(args.store) if args.store else DEFAULT_STORE_ROOT
    extra_path = Path(args.extra_exclude_file) if args.extra_exclude_file else None
    extra = load_extra_cik10(extra_path)
    if args.skip_default_exclusions:
        exclude = extra
    else:
        exclude = combined_exclusions(extra)
    max_bytes = None
    if args.max_latest_10k_mb is not None:
        max_bytes = int(args.max_latest_10k_mb * 1024 * 1024)
    prog = None if args.no_progress else sys.stderr
    report = bulk_sync_from_tickers(
        client,
        root,
        exclude_cik10=exclude,
        max_companies=args.max,
        max_latest_10k_bytes=max_bytes,
        include_full_companyfacts=args.full_facts,
        download_tenk_primary_documents=not args.no_primary_docs,
        download_index_pdfs=not args.no_index_pdfs,
        tenk_index_limit=args.tenk_limit,
        progress_every=args.progress_every,
        max_workers=args.workers,
        years=args.years,
        quarters=args.quarters,
        export_narrative_ixbrl=not args.no_ix_narrative,
        include_supplemental_submissions=args.include_supplemental_submissions,
        skip_revenue_size_filter=args.no_revenue_filter,
        max_revenue_usd=float(args.max_revenue_usd)
        if args.max_revenue_usd is not None
        else DEFAULT_MAX_REVENUE_USD,
        progress_stream=prog,
    )
    json.dump(
        {
            "store_root": report["store_root"],
            "run_folder": report.get("run_folder"),
            "saved_count": report["saved_count"],
            "skipped_count": report["skipped_count"],
            "max_workers": report.get("max_workers"),
            "max_revenue_usd": report.get("max_revenue_usd"),
            "skip_revenue_size_filter": report.get("skip_revenue_size_filter"),
            "report_path": str(
                Path(report["store_root"]) / report["run_folder"] / "last_bulk_sync_report.json"
            )
            if report.get("run_folder")
            else str(Path(report["store_root"]) / "last_bulk_sync_report.json"),
        },
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(description="EDGAR 10-K listing and disclosure download.")
    sub = p.add_subparsers(dest="command", required=True)

    pl = sub.add_parser("list", help="Print 10-K / 10-K/A metadata as JSON.")
    pl.add_argument("identifier", help="Ticker or numeric CIK.")
    pl.add_argument("limit", nargs="?", type=int, default=10, help="Max rows (default: 10).")
    pl.set_defaults(func=cmd_list)

    pp = sub.add_parser("pull", help="Save submissions, 10-K index, and XBRL facts under data_sources/.")
    pp.add_argument("identifier", help="Ticker or numeric CIK.")
    pp.add_argument(
        "--store",
        default="",
        help="Output root (default: data_sources/edgar_disclosures).",
    )
    pp.add_argument(
        "--full-facts",
        action="store_true",
        help="Also store companyfacts_full.json (large).",
    )
    pp.add_argument(
        "--no-primary-docs",
        action="store_true",
        help="Do not save primary 10-K documents under tenk_documents/ (default: save all in window).",
    )
    pp.add_argument(
        "--no-index-pdfs",
        action="store_true",
        help="Do not download extra PDFs listed in each filing index.json (default: on).",
    )
    pp.add_argument(
        "--tenk-limit",
        type=int,
        default=50,
        help="Max 10-K rows to write in tenk_filings.json (default: 50).",
    )
    pp.add_argument(
        "--years",
        type=int,
        default=None,
        metavar="N",
        help="Rolling window in years (overrides --quarters when set).",
    )
    pp.add_argument(
        "--quarters",
        type=int,
        default=8,
        help="Rolling window in calendar quarters when --years is not set (default: 8).",
    )
    pp.add_argument(
        "--no-ix-narrative",
        action="store_true",
        help="Skip downloading 10-K HTML and ix:nonNumeric narrative extraction.",
    )
    pp.add_argument(
        "--include-supplemental-submissions",
        action="store_true",
        help="Also read older CIK*-submissions-*.json chains (slower; rarely needed for 2y).",
    )
    pp.add_argument(
        "--no-revenue-filter",
        action="store_true",
        help="Allow pull even when latest FY USD revenue exceeds the cutoff (default: exclude above $1B).",
    )
    pp.add_argument(
        "--max-revenue-usd",
        type=float,
        default=None,
        metavar="USD",
        help="Override revenue cutoff in USD (default: $1B from companyfacts).",
    )
    pp.add_argument(
        "--no-progress",
        action="store_true",
        help="Do not print filing progress to stderr (default: on).",
    )
    pp.set_defaults(func=cmd_pull)

    pb = sub.add_parser(
        "bulk",
        help="Sync many companies from company_tickers.json (not all SEC filers).",
    )
    pb.add_argument("--max", type=int, required=True, help="How many companies to successfully save.")
    pb.add_argument(
        "--store",
        default="",
        help="Output root (default: data_sources/edgar_disclosures).",
    )
    pb.add_argument(
        "--skip-default-exclusions",
        action="store_true",
        help="Do not apply the built-in megacap-tech CIK blocklist.",
    )
    pb.add_argument(
        "--extra-exclude-file",
        default="",
        help="Optional text file: one decimal CIK per line (# comments allowed).",
    )
    pb.add_argument(
        "--max-latest-10k-mb",
        type=float,
        default=None,
        help="Skip filers whose latest filings.recent 10-K primary doc is larger than this many MB.",
    )
    pb.add_argument("--full-facts", action="store_true", help="Also store companyfacts_full.json per company.")
    pb.add_argument(
        "--no-primary-docs",
        action="store_true",
        help="Do not save primary 10-K documents under tenk_documents/ (default: save all in window).",
    )
    pb.add_argument(
        "--no-index-pdfs",
        action="store_true",
        help="Do not download extra PDFs from each filing index.json (default: on).",
    )
    pb.add_argument("--tenk-limit", type=int, default=50, help="Max 10-K rows per company in JSON.")
    pb.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="With --no-progress only: print every N saves (default: 50; 0=off).",
    )
    pb.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Thread pool size for parallel SEC requests (default: 8).",
    )
    pb.add_argument(
        "--years",
        type=int,
        default=None,
        metavar="N",
        help="Rolling window in years (overrides --quarters when set).",
    )
    pb.add_argument(
        "--quarters",
        type=int,
        default=8,
        help="Rolling window in calendar quarters when --years is not set (default: 8).",
    )
    pb.add_argument(
        "--no-ix-narrative",
        action="store_true",
        help="Skip ix:nonNumeric narrative extraction for each 10-K in the window.",
    )
    pb.add_argument(
        "--include-supplemental-submissions",
        action="store_true",
        help="Also merge supplemental submissions JSON files when listing 10-Ks.",
    )
    pb.add_argument(
        "--no-revenue-filter",
        action="store_true",
        help="Do not exclude filers by latest FY USD revenue (default: exclude above $1B).",
    )
    pb.add_argument(
        "--max-revenue-usd",
        type=float,
        default=None,
        metavar="USD",
        help="Override revenue cutoff in USD (default: $1B from companyfacts).",
    )
    pb.add_argument(
        "--no-progress",
        action="store_true",
        help="Do not print bulk progress to stderr (default: on).",
    )
    pb.set_defaults(func=cmd_bulk)

    args = p.parse_args(argv)
    _require_ua(p)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
