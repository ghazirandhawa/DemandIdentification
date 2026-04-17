"""
GovCon API — Federal Contract Opportunity Extraction
for DataPilot Demand Identification Engine

GovCon API provides cleaner, better-structured federal contract data
than SAM.gov's native API, with full-text search, 59 fields per notice,
full contract descriptions, and award data.

HOW TO GET API KEY (free, instant):
  Option A (terminal):
    curl -X POST "https://govconapi.com/api/v1/keys?email=YOUR_EMAIL&plan=free"

  Option B (browser):
    Go to https://govconapi.com → enter your email → key emailed instantly

  Free trial: 14 days, 25 requests/day, 50 results/page, basic filters
  No credit card required.

BASIC FILTERS (free tier):
  keywords, naics, psc, state, notice_type, solicitation_number

This script uses keyword + NAICS searches to maximize coverage within
25 requests/day. Run once per day for incremental data collection.
"""

import json
import os
import time
import requests
from datetime import datetime

API_KEY = "gca_7NBOmblsPXekQOL7fl9VsOwZFh2wguIRY26zTRSkwXk"

BASE_URL = "https://govconapi.com/api/v1"
OUTPUT_PATH = "govcon_rfps_raw.json"
PROGRESS_PATH = ".govcon_progress.json"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
}

KEYWORD_QUERIES = [
    {"label": "KW_DataAnalytics", "keywords": "data analytics", "limit": 50},
    {"label": "KW_BusinessIntelligence", "keywords": "business intelligence", "limit": 50},
    {"label": "KW_MachineLearning", "keywords": "machine learning", "limit": 50},
    {"label": "KW_ArtificialIntelligence", "keywords": "artificial intelligence", "limit": 50},
    {"label": "KW_DataEngineering", "keywords": "data engineering", "limit": 50},
    {"label": "KW_CloudMigration", "keywords": "cloud migration", "limit": 50},
    {"label": "KW_DataModernization", "keywords": "data modernization", "limit": 50},
    {"label": "KW_DataWarehouse", "keywords": "data warehouse", "limit": 50},
    {"label": "KW_DigitalTransformation", "keywords": "digital transformation", "limit": 50},
    {"label": "KW_Automation", "keywords": "automation analytics", "limit": 50},
    {"label": "KW_Dashboard", "keywords": "dashboard reporting", "limit": 50},
    {"label": "KW_DataGovernance", "keywords": "data governance", "limit": 50},
]

NAICS_QUERIES = [
    {"label": "NAICS_541511", "naics": "541511", "limit": 50},  # Custom Computer Programming
    {"label": "NAICS_541512", "naics": "541512", "limit": 50},  # Computer Systems Design
    {"label": "NAICS_541519", "naics": "541519", "limit": 50},  # Other Computer Related Services
    {"label": "NAICS_518210", "naics": "518210", "limit": 50},  # Data Processing & Hosting
    {"label": "NAICS_541611", "naics": "541611", "limit": 50},  # Management Consulting
    {"label": "NAICS_541690", "naics": "541690", "limit": 50},  # Other Scientific/Technical Consulting
]

ALL_QUERIES = KEYWORD_QUERIES + NAICS_QUERIES


def load_progress():
    """Load progress from previous runs."""
    if os.path.exists(PROGRESS_PATH):
        try:
            with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            pass
    return {"completed_labels": [], "requests_today": 0, "last_run_date": None}


def save_progress(progress):
    """Save progress for resume across days."""
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)


def load_existing_ids():
    """Load notice IDs already saved to avoid duplicates."""
    seen = set()
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
            for opp in existing.get("opportunities", []):
                nid = opp.get("notice_id", "")
                if nid:
                    seen.add(nid)
        except (json.JSONDecodeError, KeyError):
            pass
    return seen


def search_opportunities(params):
    """Search GovCon API with error handling."""
    url = f"{BASE_URL}/opportunities/search"

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=30)

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 3600))
                print(f"\n  [RATE LIMITED] Daily quota (25 req) exhausted.")
                print(f"  Retry after {retry_after}s. Saving partial results.")
                return "RATE_LIMITED"

            if resp.status_code == 403:
                detail = resp.json().get("detail", "")
                print(f"\n  [PLAN RESTRICTION] {detail}")
                print(f"  Skipping this query (needs paid plan).")
                return "PLAN_RESTRICTED"

            if resp.status_code == 400:
                detail = resp.json().get("detail", "Bad request")
                print(f"\n  [BAD REQUEST] {detail}")
                return None

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.ConnectionError as e:
            print(f"\n  [CONNECTION ERROR] attempt {attempt+1}/3: {e}")
            if attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            return None

        except requests.exceptions.Timeout:
            print(f"\n  [TIMEOUT] attempt {attempt+1}/3")
            if attempt < 2:
                time.sleep(10)
                continue
            return None

        except requests.exceptions.RequestException as e:
            print(f"\n  [ERROR] {e}")
            return None

    return None


def search_awards(params):
    """Search GovCon awards endpoint."""
    url = f"{BASE_URL}/awards/search"

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=30)

            if resp.status_code == 429:
                return "RATE_LIMITED"

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.RequestException as e:
            print(f"\n  [AWARDS ERROR] attempt {attempt+1}/3: {e}")
            if attempt < 2:
                time.sleep(10)
                continue
            return None

    return None


def save_results(all_opportunities, all_awards, queries_completed, total_queries):
    """Save results to JSON, merging with any existing data."""
    output = {
        "metadata": {
            "source": "govcon_api",
            "api_endpoint": BASE_URL,
            "extraction_timestamp": datetime.now().isoformat(),
            "total_opportunities": len(all_opportunities),
            "total_awards": len(all_awards),
            "queries_completed": queries_completed,
            "total_queries_planned": total_queries,
            "purpose": "DataPilot Demand Identification Engine — Federal Contract Signals via GovCon API",
            "cost": "Free (14-day trial, 25 req/day)",
            "note": (
                "All queries completed."
                if queries_completed >= total_queries
                else f"Partial: {queries_completed}/{total_queries} done. Re-run tomorrow to continue."
            ),
        },
        "opportunities": all_opportunities,
        "awards": all_awards,
    }

    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)

            existing_opps = existing.get("opportunities", [])
            existing_awards = existing.get("awards", [])
            existing_opp_ids = {o.get("notice_id", "") for o in existing_opps}
            existing_award_ids = {a.get("award_number", "") for a in existing_awards}

            for opp in all_opportunities:
                if opp.get("notice_id", "") not in existing_opp_ids:
                    existing_opps.append(opp)
                    existing_opp_ids.add(opp["notice_id"])

            for award in all_awards:
                if award.get("award_number", "") not in existing_award_ids:
                    existing_awards.append(award)
                    existing_award_ids.add(award["award_number"])

            output["opportunities"] = existing_opps
            output["awards"] = existing_awards
            output["metadata"]["total_opportunities"] = len(existing_opps)
            output["metadata"]["total_awards"] = len(existing_awards)
            output["metadata"]["note"] = (
                f"Merged with previous run. "
                f"Opportunities: {len(existing_opps)}, Awards: {len(existing_awards)}"
            )
            print(f"\n  Merged: {len(existing_opps)} opportunities, {len(existing_awards)} awards total")
        except (json.JSONDecodeError, KeyError):
            pass

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Saved to {OUTPUT_PATH}")


def main():
    if API_KEY == "YOUR_GOVCON_API_KEY_HERE":
        print("=" * 70)
        print("ERROR: Please set your GovCon API key first!")
        print()
        print("HOW TO GET YOUR FREE API KEY (instant, no credit card):")
        print()
        print("  Option A — Terminal:")
        print('    curl -X POST "https://govconapi.com/api/v1/keys?email=YOUR_EMAIL&plan=free"')
        print()
        print("  Option B — Browser:")
        print("    Go to https://govconapi.com → enter email → key emailed instantly")
        print()
        print("  Free trial: 14 days, 25 requests/day, 50 results/request")
        print("=" * 70)
        return

    progress = load_progress()
    today = datetime.now().strftime("%Y-%m-%d")

    if progress["last_run_date"] != today:
        progress["requests_today"] = 0
        progress["last_run_date"] = today

    completed_labels = set(progress["completed_labels"])
    pending_queries = [q for q in ALL_QUERIES if q["label"] not in completed_labels]

    if not pending_queries:
        print("All queries already completed! Delete .govcon_progress.json to re-run.")
        return

    seen_ids = load_existing_ids()
    all_opportunities = []
    all_awards = []
    queries_completed_this_run = 0
    total_planned = len(ALL_QUERIES)
    already_done = len(completed_labels)

    print("=" * 70)
    print("GovCon API — Federal Contract Opportunity Extraction")
    print(f"Endpoint: {BASE_URL}")
    print(f"Free tier: 25 requests/day, 50 results/page")
    print(f"Queries: {len(pending_queries)} pending ({already_done} already done)")
    print(f"Requests used today: {progress['requests_today']}/25")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 70)

    # --- Phase 1: Opportunity searches ---
    print("\n--- Phase 1: Searching Opportunities ---")

    for qi, query_cfg in enumerate(pending_queries, 1):
        if progress["requests_today"] >= 24:
            print(f"\n  [BUDGET] Approaching daily limit ({progress['requests_today']}/25).")
            print(f"  Saving results. Re-run tomorrow for remaining queries.")
            break

        label = query_cfg["label"]
        limit = query_cfg["limit"]

        params = {"limit": limit}
        if "keywords" in query_cfg:
            params["keywords"] = query_cfg["keywords"]
            display = f"keywords='{query_cfg['keywords']}'"
        else:
            params["naics"] = query_cfg["naics"]
            display = f"naics={query_cfg['naics']}"

        remaining = len(pending_queries) - qi + 1
        print(f"\n[{already_done + qi}/{total_planned}] {label}: {display} ({remaining} remaining)")

        data = search_opportunities(params)
        progress["requests_today"] += 1

        if data == "RATE_LIMITED":
            save_results(all_opportunities, all_awards, already_done + queries_completed_this_run, total_planned)
            progress["completed_labels"] = list(completed_labels)
            save_progress(progress)
            print(f"\n  Re-run this script tomorrow to continue.")
            return

        if data == "PLAN_RESTRICTED" or data is None:
            time.sleep(2)
            continue

        opps = data.get("data", [])
        pagination = data.get("pagination", {})
        total_matches = pagination.get("total", 0)
        new_count = 0

        for opp in opps:
            nid = opp.get("notice_id", "")
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                opp["_query_label"] = label
                opp["_signal_type"] = "formal_rfp"
                opp["_extraction_source"] = "govcon_api"
                opp["_extracted_at"] = datetime.now().isoformat()
                all_opportunities.append(opp)
                new_count += 1

        completed_labels.add(label)
        queries_completed_this_run += 1
        print(f"  Total matches: {total_matches}, returned: {len(opps)}, new: {new_count}")

        time.sleep(3)

    # --- Phase 2: Award searches (use remaining daily budget) ---
    award_keywords = ["data analytics", "artificial intelligence", "machine learning"]
    awards_done = progress.get("awards_done", False)

    if not awards_done and progress["requests_today"] < 23:
        print("\n--- Phase 2: Searching Awards (who won data/AI contracts) ---")

        for kw in award_keywords:
            if progress["requests_today"] >= 24:
                break

            print(f"\n  Awards search: '{kw}'")
            data = search_awards({"awardee_name": None, "naics": "541512", "limit": 50})
            progress["requests_today"] += 1

            if data == "RATE_LIMITED":
                break

            if data and "data" in data:
                for award in data["data"]:
                    aid = award.get("award_number", "")
                    if aid:
                        award["_query_keyword"] = kw
                        award["_extraction_source"] = "govcon_api"
                        award["_extracted_at"] = datetime.now().isoformat()
                        all_awards.append(award)
                print(f"  Found {len(data['data'])} awards")

            time.sleep(3)

        progress["awards_done"] = True

    # --- Save everything ---
    total_done = already_done + queries_completed_this_run
    print(f"\n{'=' * 70}")
    print(f"GovCon API Extraction — {queries_completed_this_run} queries this run")
    print(f"New opportunities: {len(all_opportunities)}")
    print(f"New awards: {len(all_awards)}")
    print(f"Requests used today: {progress['requests_today']}/25")

    if total_done < total_planned:
        print(f"Progress: {total_done}/{total_planned} — re-run tomorrow for the rest")
    else:
        print(f"All {total_planned} queries completed!")

    save_results(all_opportunities, all_awards, total_done, total_planned)
    progress["completed_labels"] = list(completed_labels)
    save_progress(progress)


if __name__ == "__main__":
    main()
