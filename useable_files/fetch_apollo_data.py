"""
Apollo.io Data Extraction for DataPilot Demand Identification Engine

Narrow scope: mid-tier AI vendors (firmographic band) plus IC data/ML roles
(data engineer, ML engineer, data scientist, etc.).

  1. Organization Search — AI-focused keywords and/or open roles for those titles
  2. People Search       — same IC titles at employers in the mid-tier band (FREE)
  3. Org Job Postings    — postings for discovered companies (credit-capped)

Rate limit: 600 calls/hour per endpoint. Script respects this with sleeps.
Saves intermediate + final results to JSON files.

Hackathon-scale volume caps live near APOLLO_* / ORG_SEARCH_* / PEOPLE_* /
JOB_POSTINGS_* constants; raise them for a full crawl.
"""

import json
import sys
import time
import traceback
import requests
from datetime import datetime

OUTPUT_ORGS = "apollo_organizations.json"
OUTPUT_PEOPLE = "apollo_people.json"
OUTPUT_JOB_POSTINGS = "apollo_job_postings.json"
OUTPUT_EXTRACTION_SUMMARY = "apollo_extraction_summary.json"


def save_json(data, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"  -> Saved to {filename}")

API_KEY = "zonlKKKsnOLJEeUF3_og0w"
BASE_URL = "https://api.apollo.io/api/v1"
HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "x-api-key": API_KEY,
}

RATE_LIMIT_SLEEP = 6.5  # ~550 calls/hour, safely under 600/hour limit

# ---------------------------------------------------------------------------
# ICP-ALIGNED SEARCH CONFIGURATIONS
# ---------------------------------------------------------------------------

# US target locations from ICP Section 1.2
TARGET_LOCATIONS = [
    "Texas", "California", "New York", "New Jersey",
    "Florida", "Ohio", "Illinois", "Pennsylvania",
    "Massachusetts", "Washington, District of Columbia",
]

# Mid-tier AI vendors: API-side band (Apollo ranges are inclusive "min,max";
# revenue is integer USD with no symbols per Apollo docs).
MID_TIER_AI_EMP_MIN = 30
MID_TIER_AI_EMP_MAX = 800
MID_TIER_AI_EMPLOYEE_RANGE = f"{MID_TIER_AI_EMP_MIN},{MID_TIER_AI_EMP_MAX}"
MID_TIER_AI_REVENUE_MIN = 8_000_000    # $8M (slightly below prior $10M floor for earlier-stage startups)
MID_TIER_AI_REVENUE_MAX = 80_000_000   # $80M (slightly below prior $100M cap)

# --- Hackathon caps (raise for production / full market crawl) ---
# Apollo hard max is 500 pages × 100 rows; lower page caps save time when sector cap is small.
APOLLO_ORG_SEARCH_MAX_PAGES = 120
APOLLO_PEOPLE_SEARCH_MAX_PAGES = 16

# Unique post-filtered orgs per ORG_SEARCH_QUERIES sector (~3 sectors → ≤1350 orgs total).
ORG_SEARCH_MAX_ORGS_PER_SECTOR = 450

# Stop people search once this many unique people are collected (across all sub-queries).
PEOPLE_SEARCH_MAX_TOTAL = 3000

# Max people to keep per employer (Apollo organization id); spreads coverage across ~1227 orgs.
PEOPLE_SEARCH_MAX_PER_ORG = 4

# Org job postings: credit-heavy; one GET per org (up to 50 postings each, single page).
JOB_POSTINGS_MAX_ORGS = 225
# Prefetch pool size before firmo filter (was 3× max_orgs; tripled with caps).
JOB_POSTINGS_CANDIDATE_MULTIPLIER = 9

# Title counts sent to Apollo (API may impose its own limits).
PEOPLE_QUERY_TITLE_LIMIT = 45
ORG_HIRING_QUERY_JOB_TITLE_LIMIT = 24

# IC + hands-on data / ML titles (org job postings filter + people search).
DATA_ML_IC_TITLES = [
    "data engineer",
    "senior data engineer",
    "staff data engineer",
    "analytics engineer",
    "data scientist",
    "senior data scientist",
    "machine learning engineer",
    "ML engineer",
    "MLOps engineer",
    "AI engineer",
    "applied scientist",
    "research scientist",
    "data architect",
    "machine learning scientist",
]

# Seniorities broad enough for IC through senior leadership in technical roles.
DATA_ML_SENIORITIES = [
    "intern", "entry", "senior", "manager", "director",
    "head", "vp", "c_suite", "owner", "founder", "partner",
]

# ---------------------------------------------------------------------------
# ORGANIZATION SEARCH — mid-tier AI only (keywords + open roles)
# ---------------------------------------------------------------------------

_ORG_BASE = {
    "organization_locations[]": TARGET_LOCATIONS,
    "organization_num_employees_ranges[]": [MID_TIER_AI_EMPLOYEE_RANGE],
    "revenue_range[min]": MID_TIER_AI_REVENUE_MIN,
    "revenue_range[max]": MID_TIER_AI_REVENUE_MAX,
}

ORG_SEARCH_QUERIES = [
    {
        "label": "MidTierAI_Vendor_Keywords_Core",
        "params": {
            **_ORG_BASE,
            "q_organization_keyword_tags[]": [
                "B2B SaaS",
                "artificial intelligence",
                "machine learning",
                "generative AI",
                "LLM",
                "MLOps",
            ],
        },
    },
    {
        "label": "MidTierAI_Vendor_Keywords_Infrastructure",
        "params": {
            **_ORG_BASE,
            "q_organization_keyword_tags[]": [
                "AI infrastructure",
                "ML platform",
                "model serving",
                "AI startup",
                "deep learning",
            ],
        },
    },
    {
        "label": "MidTierAI_Hiring_DataML_IC",
        "params": {
            **_ORG_BASE,
            "q_organization_keyword_tags[]": [
                "SaaS",
                "artificial intelligence",
                "machine learning",
            ],
            "q_organization_job_titles[]": [
                "data engineer",
                "data scientist",
                "machine learning engineer",
                "MLOps engineer",
                "ML engineer",
                "AI engineer",
                "analytics engineer",
            ],
        },
    },
]


def _num(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v):
    n = _num(v)
    if n is None:
        return None
    try:
        return int(n)
    except (TypeError, ValueError):
        return None


def org_passes_mid_tier_firmographics(org: dict) -> bool:
    """Enforce mid-tier band on Apollo's modeled fields.

    Safety net when search index and `organization_revenue` disagree, or when
    callers bypass query-string filters. Search payloads include `organization_revenue` for most
    accounts — use that as the primary gate. If revenue is missing or zero,
    fall back to `estimated_num_employees` when present. Otherwise reject
    (unverified firmographics).
    """
    rev = _num(org.get("organization_revenue"))
    if rev is not None and rev > 0:
        return MID_TIER_AI_REVENUE_MIN <= rev <= MID_TIER_AI_REVENUE_MAX

    emp = _int_or_none(org.get("estimated_num_employees"))
    if emp is not None and emp > 0:
        return MID_TIER_AI_EMP_MIN <= emp <= MID_TIER_AI_EMP_MAX

    return False


# ---------------------------------------------------------------------------
# API HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def apollo_flat_query_params(params: dict) -> list[tuple[str, str]]:
    """Flatten search dict for URL query string (Apollo OpenAPI uses in: query).

    Array params use repeated keys, e.g. organization_locations[]=Texas twice.
    """
    pairs: list[tuple[str, str]] = []
    for key, val in params.items():
        if val is None:
            continue
        if isinstance(val, (list, tuple)):
            for item in val:
                if item is None:
                    continue
                pairs.append((key, str(item)))
        elif isinstance(val, bool):
            pairs.append((key, "true" if val else "false"))
        else:
            pairs.append((key, str(val)))
    return pairs


def api_post(endpoint, params, retries=3, *, params_in_query=False):
    """POST to Apollo with retry and rate-limit handling.

    Organization / people search OpenAPI marks filters as query parameters.
    Sending them only as a JSON body can leave firmographics ignored; use
    params_in_query=True for mixed_companies/search and mixed_people/api_search.
    """
    url = f"{BASE_URL}/{endpoint}"
    for attempt in range(retries):
        try:
            if params_in_query:
                hdrs = {k: v for k, v in HEADERS.items() if k.lower() != "content-type"}
                resp = requests.post(
                    url,
                    headers=hdrs,
                    params=apollo_flat_query_params(params),
                    timeout=30,
                )
            else:
                resp = requests.post(url, headers=HEADERS, json=params, timeout=30)
            if resp.status_code == 429:
                wait = 60 * (attempt + 1)
                print(f"    [RATE LIMITED] Waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            print(f"    [ERROR] attempt {attempt+1}: {e}")
            if attempt < retries - 1:
                time.sleep(10)
    return None


def api_get(endpoint, params, retries=3):
    """GET request to Apollo API with retry and rate-limit handling."""
    url = f"{BASE_URL}/{endpoint}"
    headers_get = {k: v for k, v in HEADERS.items() if k != "Content-Type"}
    headers_get["x-api-key"] = API_KEY
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers_get, params=params, timeout=30)
            if resp.status_code == 429:
                wait = 60 * (attempt + 1)
                print(f"    [RATE LIMITED] Waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            print(f"    [ERROR] attempt {attempt+1}: {e}")
            if attempt < retries - 1:
                time.sleep(10)
    return None


# ---------------------------------------------------------------------------
# PART 1: ORGANIZATION SEARCH
# ---------------------------------------------------------------------------

def fetch_organizations():
    """Search for mid-tier AI companies (keyword + hiring filters)."""
    all_orgs = []
    seen_ids = set()
    total_queries = len(ORG_SEARCH_QUERIES)

    print("=" * 70)
    print("PART 1: ORGANIZATION SEARCH")
    print(f"Running {total_queries} mid-tier AI org queries")
    print("=" * 70)

    for qi, query in enumerate(ORG_SEARCH_QUERIES, 1):
        label = query["label"]
        params = dict(query["params"])
        params["per_page"] = 100
        params["page"] = 1

        print(f"\n[{qi}/{total_queries}] {label}")
        print(
            f"  (cap {ORG_SEARCH_MAX_ORGS_PER_SECTOR} orgs/sector; up to {APOLLO_ORG_SEARCH_MAX_PAGES} pages "
            f"× {params['per_page']} rows)"
        )

        try:
            added_this_query = 0
            pages_fetched = 0
            per_page = int(params.get("per_page") or 100)

            while pages_fetched < APOLLO_ORG_SEARCH_MAX_PAGES:
                if added_this_query >= ORG_SEARCH_MAX_ORGS_PER_SECTOR:
                    break

                pages_fetched += 1
                print(f"  Page {params['page']}...", end=" ")

                data = api_post("mixed_companies/search", params, params_in_query=True)
                if not data:
                    print("NO DATA")
                    break

                orgs = data.get("organizations", [])
                pagination = data.get("pagination", {})
                total_entries = pagination.get("total_entries", 0)
                total_pages = pagination.get("total_pages", 0)

                new_this_page = 0
                dropped_firmo = 0
                for org in orgs:
                    if added_this_query >= ORG_SEARCH_MAX_ORGS_PER_SECTOR:
                        break
                    if not org_passes_mid_tier_firmographics(org):
                        dropped_firmo += 1
                        continue
                    org_id = org.get("id", "")
                    if org_id and org_id not in seen_ids:
                        seen_ids.add(org_id)
                        org["_icp_query_label"] = label
                        org["_extraction_source"] = "apollo_org_search"
                        org["_extracted_at"] = datetime.now().isoformat()
                        all_orgs.append(org)
                        new_this_page += 1
                        added_this_query += 1

                print(
                    f"total={total_entries}, fetched={len(orgs)}, "
                    f"new_this_page={new_this_page}, query_total_new={added_this_query}, "
                    f"dropped_out_of_band={dropped_firmo}"
                )

                if added_this_query >= ORG_SEARCH_MAX_ORGS_PER_SECTOR:
                    break
                if len(orgs) < per_page:
                    break
                if total_pages and params["page"] >= total_pages:
                    break

                params["page"] += 1
                time.sleep(RATE_LIMIT_SLEEP)
        finally:
            save_json(
                {
                    "metadata": {
                        "source": "apollo_organization_search",
                        "extraction_timestamp": datetime.now().isoformat(),
                        "total_organizations": len(all_orgs),
                        "queries_run": [q["label"] for q in ORG_SEARCH_QUERIES],
                        "checkpoint": True,
                        "sectors_completed": qi,
                        "last_sector_label": label,
                        "purpose": "DataPilot — mid-tier AI orgs (checkpoint; safe if run interrupted)",
                        "hackathon_cap_orgs_per_sector": ORG_SEARCH_MAX_ORGS_PER_SECTOR,
                        "hackathon_cap_org_search_pages": APOLLO_ORG_SEARCH_MAX_PAGES,
                    },
                    "organizations": list(all_orgs),
                },
                OUTPUT_ORGS,
            )

        time.sleep(RATE_LIMIT_SLEEP)

    print(
        f"\n--- Org Search Complete: {len(all_orgs)} unique organizations "
        f"(post-filtered; ≤{ORG_SEARCH_MAX_ORGS_PER_SECTOR} per sector) ---"
    )
    return all_orgs


# ---------------------------------------------------------------------------
# PART 2: PEOPLE SEARCH (FREE — no credit consumption)
# ---------------------------------------------------------------------------

def fetch_decision_makers(organizations):
    """Find IC data/ML people (titles) at employers in the mid-tier AI band.

    Uses People API Search (FREE). Filtered by org HQ location, headcount,
    revenue, person titles, and seniority — not CMO/general buyer personas.
    """
    all_people = []
    seen_ids = set()
    per_org_people: dict[str, int] = {}

    print("\n" + "=" * 70)
    print("PART 2: PEOPLE SEARCH (Data / ML IC roles) — FREE, no credits")
    print("=" * 70)

    ic_titles = DATA_ML_IC_TITLES[:PEOPLE_QUERY_TITLE_LIMIT]

    broad_queries = []
    for location in TARGET_LOCATIONS:
        broad_queries.append({
            "label": f"DataML_IC_{location.replace(', ', '_').replace(' ', '_')}",
            "params": {
                "person_titles[]": ic_titles,
                "person_seniorities[]": DATA_ML_SENIORITIES,
                "organization_locations[]": [location],
                "organization_num_employees_ranges[]": [MID_TIER_AI_EMPLOYEE_RANGE],
                "revenue_range[min]": MID_TIER_AI_REVENUE_MIN,
                "revenue_range[max]": MID_TIER_AI_REVENUE_MAX,
                "per_page": 100,
                "page": 1,
            },
        })

    for location in TARGET_LOCATIONS:
        broad_queries.append({
            "label": f"DataML_IC_atHiringCos_{location.replace(', ', '_').replace(' ', '_')}",
            "params": {
                "person_titles[]": ic_titles,
                "person_seniorities[]": DATA_ML_SENIORITIES,
                "organization_locations[]": [location],
                "q_organization_job_titles[]": DATA_ML_IC_TITLES[:ORG_HIRING_QUERY_JOB_TITLE_LIMIT],
                "organization_num_employees_ranges[]": [MID_TIER_AI_EMPLOYEE_RANGE],
                "revenue_range[min]": MID_TIER_AI_REVENUE_MIN,
                "revenue_range[max]": MID_TIER_AI_REVENUE_MAX,
                "per_page": 100,
                "page": 1,
            },
        })

    total_queries = len(broad_queries)
    for qi, query in enumerate(broad_queries, 1):
        if len(all_people) >= PEOPLE_SEARCH_MAX_TOTAL:
            print(
                f"\n  [hackathon cap] {PEOPLE_SEARCH_MAX_TOTAL} people collected; "
                f"skipping remaining {total_queries - qi + 1} sub-queries."
            )
            break

        label = query["label"]
        params = query["params"]

        print(f"\n[{qi}/{total_queries}] {label}")

        try:
            pages_fetched = 0
            per_page = int(params.get("per_page") or 100)

            while pages_fetched < APOLLO_PEOPLE_SEARCH_MAX_PAGES:
                if len(all_people) >= PEOPLE_SEARCH_MAX_TOTAL:
                    break

                pages_fetched += 1
                print(f"  Page {params['page']}...", end=" ")

                data = api_post("mixed_people/api_search", params, params_in_query=True)
                if not data:
                    print("NO DATA")
                    break

                people = data.get("people", [])
                total_entries = data.get("total_entries", 0)
                pagination = data.get("pagination") or {}
                total_pages = pagination.get("total_pages") or 0

                new_count = 0
                for person in people:
                    if len(all_people) >= PEOPLE_SEARCH_MAX_TOTAL:
                        break
                    pid = person.get("id", "")
                    if not pid or pid in seen_ids:
                        continue
                    org_key = _person_employer_org_id(person)
                    if (
                        org_key
                        and per_org_people.get(org_key, 0) >= PEOPLE_SEARCH_MAX_PER_ORG
                    ):
                        continue
                    seen_ids.add(pid)
                    if org_key:
                        per_org_people[org_key] = per_org_people.get(org_key, 0) + 1
                    person["_search_label"] = label
                    person["_extraction_source"] = "apollo_people_search"
                    person["_extracted_at"] = datetime.now().isoformat()
                    all_people.append(person)
                    new_count += 1

                print(f"total={total_entries}, fetched={len(people)}, new={new_count}")

                if len(all_people) >= PEOPLE_SEARCH_MAX_TOTAL:
                    break
                if len(people) < per_page:
                    break
                if total_pages and params["page"] >= total_pages:
                    break

                params["page"] += 1
                time.sleep(RATE_LIMIT_SLEEP)
        finally:
            save_json(
                {
                    "metadata": {
                        "source": "apollo_people_search",
                        "extraction_timestamp": datetime.now().isoformat(),
                        "total_people": len(all_people),
                        "checkpoint": True,
                        "subqueries_completed": qi,
                        "last_subquery_label": label,
                        "hackathon_cap_people_total": PEOPLE_SEARCH_MAX_TOTAL,
                        "hackathon_cap_people_per_org": PEOPLE_SEARCH_MAX_PER_ORG,
                        "purpose": "DataPilot — people checkpoint (safe if run interrupted)",
                    },
                    "people": list(all_people),
                },
                OUTPUT_PEOPLE,
            )

        time.sleep(RATE_LIMIT_SLEEP)

    print(f"\n--- People Search Complete: {len(all_people)} unique people (data/ML IC) ---")
    return all_people


# ---------------------------------------------------------------------------
# PART 3: ORGANIZATION JOB POSTINGS
# ---------------------------------------------------------------------------

def fetch_org_job_postings(organizations, max_orgs=None):
    """Fetch current job postings for top organizations.
    
    Prioritizes orgs with hiring signal labels or those from Tier 1/2 queries.
    Costs credits — capped at max_orgs to manage usage.
    """
    if max_orgs is None:
        max_orgs = JOB_POSTINGS_MAX_ORGS

    all_postings = []

    print("\n" + "=" * 70)
    print(f"PART 3: ORGANIZATION JOB POSTINGS (up to {max_orgs} orgs)")
    print("=" * 70)

    # Prioritize orgs from hiring-tagged query first (stronger signal).
    priority_orgs = []
    other_orgs = []
    for org in organizations:
        label = org.get("_icp_query_label", "")
        if "Hiring" in label:
            priority_orgs.append(org)
        else:
            other_orgs.append(org)

    selected_orgs = (priority_orgs + other_orgs)[
        : max_orgs * JOB_POSTINGS_CANDIDATE_MULTIPLIER
    ]
    selected_orgs = [o for o in selected_orgs if org_passes_mid_tier_firmographics(o)][
        :max_orgs
    ]
    total = len(selected_orgs)
    if total < max_orgs:
        print(
            f"  Note: only {total} orgs pass mid-tier firmographics "
            f"(capped job posting fetches to save credits)."
        )

    last_org_index = 0

    def _checkpoint_jobs():
        save_json(
            {
                "metadata": {
                    "source": "apollo_organization_job_postings",
                    "extraction_timestamp": datetime.now().isoformat(),
                    "total_postings": len(all_postings),
                    "checkpoint": True,
                    "orgs_scraped": last_org_index,
                    "orgs_planned": total,
                    "hackathon_cap_job_posting_orgs": max_orgs,
                    "purpose": "DataPilot — job postings checkpoint (safe if run interrupted)",
                },
                "job_postings": list(all_postings),
            },
            OUTPUT_JOB_POSTINGS,
        )

    try:
        for i, org in enumerate(selected_orgs, 1):
            last_org_index = i
            org_id = org.get("id", "")
            org_name = org.get("name", "Unknown")
            if not org_id:
                continue

            print(f"[{i}/{total}] {org_name} ({org_id})...", end=" ")

            data = api_get(
                f"organizations/{org_id}/job_postings",
                {"per_page": 50, "page": 1},
            )

            if not data:
                print("NO DATA")
                time.sleep(RATE_LIMIT_SLEEP)
                continue

            postings = data.get("organization_job_postings", [])
            print(f"{len(postings)} postings")

            for posting in postings:
                posting["_organization_id"] = org_id
                posting["_organization_name"] = org_name
                posting["_icp_query_label"] = org.get("_icp_query_label", "")
                posting["_extraction_source"] = "apollo_org_job_postings"
                posting["_extracted_at"] = datetime.now().isoformat()

            all_postings.extend(postings)
            time.sleep(RATE_LIMIT_SLEEP)
    finally:
        _checkpoint_jobs()

    print(f"\n--- Job Postings Complete: {len(all_postings)} postings from {total} orgs ---")
    return all_postings


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


def main():
    start = datetime.now()
    print(f"DataPilot Demand Identification Engine — Apollo Data Extraction")
    print(f"Started: {start.isoformat()}")
    print(f"API Base: {BASE_URL}")
    print()

    organizations = []
    people = []
    job_postings = []
    run_error = None

    try:
        # ── Part 1: Organizations ──
        organizations = fetch_organizations()
        save_json(
            {
                "metadata": {
                    "source": "apollo_organization_search",
                    "extraction_timestamp": datetime.now().isoformat(),
                    "total_organizations": len(organizations),
                    "queries_run": [q["label"] for q in ORG_SEARCH_QUERIES],
                    "checkpoint": False,
                    "purpose": "DataPilot — mid-tier AI orgs ($8M–$80M rev, 30–800 HC) + AI/ML keywords or data/ML job postings; rows post-filtered on organization_revenue / estimated_num_employees",
                    "hackathon_cap_orgs_per_sector": ORG_SEARCH_MAX_ORGS_PER_SECTOR,
                    "hackathon_cap_org_search_pages": APOLLO_ORG_SEARCH_MAX_PAGES,
                },
                "organizations": organizations,
            },
            OUTPUT_ORGS,
        )

        # ── Part 2: Decision-Makers (FREE) ──
        people = fetch_decision_makers(organizations)
        save_json(
            {
                "metadata": {
                    "source": "apollo_people_search",
                    "extraction_timestamp": datetime.now().isoformat(),
                    "total_people": len(people),
                    "checkpoint": False,
                    "purpose": "DataPilot — IC data/ML titles at mid-tier AI employer band",
                    "note": "People API Search is free and does not consume credits. Emails/phones require enrichment.",
                    "hackathon_cap_people_total": PEOPLE_SEARCH_MAX_TOTAL,
                    "hackathon_cap_people_per_org": PEOPLE_SEARCH_MAX_PER_ORG,
                },
                "people": people,
            },
            OUTPUT_PEOPLE,
        )

        # ── Part 3: Job Postings ──
        job_postings = fetch_org_job_postings(organizations)
        save_json(
            {
                "metadata": {
                    "source": "apollo_organization_job_postings",
                    "extraction_timestamp": datetime.now().isoformat(),
                    "total_postings": len(job_postings),
                    "organizations_queried": min(JOB_POSTINGS_MAX_ORGS, len(organizations)),
                    "hackathon_cap_job_posting_orgs": JOB_POSTINGS_MAX_ORGS,
                    "checkpoint": False,
                    "purpose": "DataPilot Demand Identification Engine — hiring signals per company",
                },
                "job_postings": job_postings,
            },
            OUTPUT_JOB_POSTINGS,
        )

    except Exception as e:
        run_error = e
        print(f"\n[ERROR] Extraction aborted: {e}")
        traceback.print_exc()
    finally:
        elapsed = datetime.now() - start
        summary = {
            "extraction_summary": {
                "timestamp": datetime.now().isoformat(),
                "elapsed_time": str(elapsed),
                "status": "error" if run_error else "complete",
                "error": str(run_error) if run_error else None,
                "organizations_found": len(organizations),
                "data_ml_people_found": len(people),
                "job_postings_found": len(job_postings),
                "output_files": [
                    OUTPUT_ORGS,
                    OUTPUT_PEOPLE,
                    OUTPUT_JOB_POSTINGS,
                ],
            }
        }
        save_json(summary, OUTPUT_EXTRACTION_SUMMARY)

    print("\n" + "=" * 70)
    if run_error:
        print("EXTRACTION ENDED WITH ERROR (partial JSON may still be on disk; see checkpoints in metadata)")
    else:
        print("EXTRACTION COMPLETE")
    print(f"  Organizations:    {len(organizations)}")
    print(f"  Data/ML people:   {len(people)}")
    print(f"  Job Postings:     {len(job_postings)}")
    print(f"  Elapsed:          {elapsed}")
    print("=" * 70)

    if run_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
