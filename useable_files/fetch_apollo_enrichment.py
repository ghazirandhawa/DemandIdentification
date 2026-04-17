"""
Apollo.io Enrichment — Organization + People
for DataPilot Demand Identification Engine

Reads apollo_organizations.json and apollo_people.json,
enriches each record with full firmographic and contact data.

Organization Enrichment returns: industry, estimated_num_employees,
  annual_revenue, total_funding, funding_events, city, state, country,
  keywords, short_description, etc.

People Enrichment returns: full name, email, linkedin_url, title,
  phone, city, state, country, organization details, etc.

Rate limit: 600 calls/hour per endpoint. Script uses 6.5s sleep.
Saves progress after every batch so nothing is lost if interrupted.

People: after PEOPLE_ENRICH_CONSECUTIVE_NOT_FOUND_STOP consecutive NOT_FOUND responses,
enrichment stops and writes apollo_people_enriched.json with success-only rows.

To export success-only from an existing checkpoint without calling Apollo:
  python fetch_apollo_enrichment.py export-people-success
"""

import json
import os
import sys
import time
import traceback
import requests
from datetime import datetime

API_KEY = "zonlKKKsnOLJEeUF3_og0w"
BASE_URL = "https://api.apollo.io/api/v1"
HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "x-api-key": API_KEY,
}

RATE_LIMIT_SLEEP = 6.5

# Optional cap on paid enrichment volume (org + people/match). None = enrich everyone remaining.
HACKATHON_MAX_ORGS_TO_ENRICH = None
HACKATHON_MAX_PEOPLE_TO_ENRICH = None

# After this many consecutive enrich_person failures (NOT FOUND), stop and write success-only JSON.
PEOPLE_ENRICH_CONSECUTIVE_NOT_FOUND_STOP = 8

# Input files (from fetch_apollo_data.py)
ORG_INPUT = "apollo_organizations.json"
PEOPLE_INPUT = "apollo_people.json"

# Output files
ORG_ENRICHED_OUTPUT = "apollo_organizations_enriched.json"
PEOPLE_ENRICHED_OUTPUT = "apollo_people_enriched.json"

# Progress tracking files (resume support)
ORG_PROGRESS_FILE = ".org_enrich_progress.json"
PEOPLE_PROGRESS_FILE = ".people_enrich_progress.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def load_progress(path):
    if os.path.exists(path):
        return load_json(path)
    return {"completed_ids": [], "enriched_data": {}}


def save_progress(progress, path):
    save_json(progress, path)


def _build_enriched_org_rows(organizations, enriched_map):
    enriched_orgs = []
    for org in organizations:
        org_id = org.get("id", "")
        enriched = enriched_map.get(org_id)
        if enriched:
            enriched_orgs.append(enriched)
        else:
            org = dict(org)
            org["_enrichment_status"] = "not_found"
            enriched_orgs.append(org)
    return enriched_orgs


def _save_org_enriched_file(organizations, enriched_map, *, checkpoint=False, error=None):
    enriched_orgs = _build_enriched_org_rows(organizations, enriched_map)
    output = {
        "metadata": {
            "source": "apollo_organization_enrichment",
            "extraction_timestamp": datetime.now().isoformat(),
            "total_organizations": len(enriched_orgs),
            "successfully_enriched": sum(
                1
                for o in enriched_orgs
                if o.get("annual_revenue") is not None or o.get("estimated_num_employees") is not None
            ),
            "checkpoint": checkpoint,
            "error": error,
            "purpose": "DataPilot Demand Identification Engine — enriched company firmographics for ICP scoring",
            "fields_added": [
                "industry",
                "estimated_num_employees",
                "annual_revenue",
                "total_funding",
                "funding_events",
                "latest_funding_stage",
                "city",
                "state",
                "country",
                "keywords",
                "short_description",
                "street_address",
                "postal_code",
            ],
        },
        "organizations": enriched_orgs,
    }
    save_json(output, ORG_ENRICHED_OUTPUT)
    return enriched_orgs


def _build_enriched_people_rows(people, enriched_map):
    enriched_people = []
    for person in people:
        pid = person.get("id", "")
        enriched = enriched_map.get(pid)
        if enriched:
            enriched_people.append(enriched)
        else:
            person = dict(person)
            person["_enrichment_status"] = "not_found"
            enriched_people.append(person)
    return enriched_people


def _successful_enriched_rows_only(people, enriched_map):
    """Input order; only rows with a non-null enriched payload."""
    out = []
    for person in people:
        pid = person.get("id", "")
        enriched = enriched_map.get(pid)
        if enriched:
            out.append(enriched)
    return out


def _save_people_enriched_file(
    people,
    enriched_map,
    *,
    checkpoint=False,
    error=None,
    success_only=False,
    early_stop_not_found=False,
):
    if success_only:
        enriched_people = _successful_enriched_rows_only(people, enriched_map)
    else:
        enriched_people = _build_enriched_people_rows(people, enriched_map)
    output = {
        "metadata": {
            "source": "apollo_people_enrichment",
            "extraction_timestamp": datetime.now().isoformat(),
            "total_people": len(enriched_people),
            "successfully_enriched": sum(1 for p in enriched_people if p.get("email") is not None),
            "checkpoint": checkpoint,
            "error": error,
            "success_only_rows": success_only,
            "early_stop_consecutive_not_found": early_stop_not_found,
            "purpose": "DataPilot Demand Identification Engine — enriched decision-maker contacts for outreach",
            "fields_added": [
                "last_name (full)",
                "email",
                "linkedin_url",
                "phone",
                "city",
                "state",
                "country",
                "organization (full details)",
            ],
        },
        "people": enriched_people,
    }
    save_json(output, PEOPLE_ENRICHED_OUTPUT)
    return enriched_people


def export_people_success_only_from_checkpoint():
    """Rebuild apollo_people_enriched.json with only successful rows (no Apollo calls)."""
    if not os.path.exists(PEOPLE_INPUT):
        print(f"ERROR: {PEOPLE_INPUT} not found.")
        return
    if not os.path.exists(PEOPLE_PROGRESS_FILE):
        print(f"ERROR: {PEOPLE_PROGRESS_FILE} not found. Nothing to export.")
        return
    people_data = load_json(PEOPLE_INPUT)
    people = people_data.get("people", [])
    progress = load_progress(PEOPLE_PROGRESS_FILE)
    enriched_map = progress.get("enriched_data", {})
    n = sum(1 for v in enriched_map.values() if v)
    print(f"Exporting {n} successful enrichments to {PEOPLE_ENRICHED_OUTPUT} (success-only)...")
    _save_people_enriched_file(
        people,
        enriched_map,
        checkpoint=True,
        error=None,
        success_only=True,
        early_stop_not_found=True,
    )
    print("Done.")


# ---------------------------------------------------------------------------
# PART 1: ORGANIZATION ENRICHMENT
# ---------------------------------------------------------------------------

def enrich_organization(domain):
    """Enrich a single org by domain. Returns full org data or None."""
    url = f"{BASE_URL}/organizations/enrich"
    params = {"domain": domain}
    headers = {"x-api-key": API_KEY, "Cache-Control": "no-cache"}

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)

            if resp.status_code == 429:
                wait = 60 * (attempt + 1)
                print(f"    [RATE LIMITED] Waiting {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code == 404:
                return None

            resp.raise_for_status()
            data = resp.json()
            return data.get("organization", None)

        except requests.exceptions.RequestException as e:
            print(f"    [ERROR] attempt {attempt+1}: {e}")
            if attempt < 2:
                time.sleep(10)
    return None


def run_org_enrichment():
    """Enrich all organizations from apollo_organizations.json."""

    if not os.path.exists(ORG_INPUT):
        print(f"ERROR: {ORG_INPUT} not found. Run fetch_apollo_data.py first.")
        return

    orgs_data = load_json(ORG_INPUT)
    organizations = orgs_data.get("organizations", [])
    progress = load_progress(ORG_PROGRESS_FILE)
    completed_ids = set(progress.get("completed_ids", []))
    enriched_map = progress.get("enriched_data", {})

    # Collect domains to enrich (skip already done)
    to_enrich = []
    for org in organizations:
        org_id = org.get("id", "")
        domain = org.get("primary_domain", "")
        if org_id and domain and org_id not in completed_ids:
            to_enrich.append((org_id, domain, org))

    if (
        HACKATHON_MAX_ORGS_TO_ENRICH is not None
        and len(to_enrich) > HACKATHON_MAX_ORGS_TO_ENRICH
    ):
        print(
            f"[enrichment cap] Enriching first {HACKATHON_MAX_ORGS_TO_ENRICH} of "
            f"{len(to_enrich)} remaining orgs (set HACKATHON_MAX_ORGS_TO_ENRICH=None for all)."
        )
        to_enrich = to_enrich[:HACKATHON_MAX_ORGS_TO_ENRICH]

    total = len(to_enrich)
    already_done = len(completed_ids)

    print("=" * 70)
    print("PART 1: ORGANIZATION ENRICHMENT")
    print(f"Total orgs: {len(organizations)} | Already enriched: {already_done} | Remaining: {total}")
    print(f"Estimated time: ~{total * 7 // 60} minutes")
    print("=" * 70)

    enrich_error = None
    try:
        for i, (org_id, domain, org) in enumerate(to_enrich, 1):
            name = org.get("name", "Unknown")
            print(f"[{already_done + i}/{len(organizations)}] {name} ({domain})...", end=" ")

            enriched = enrich_organization(domain)

            if enriched:
                enriched["_original_id"] = org_id
                enriched["_icp_query_label"] = org.get("_icp_query_label", "")
                enriched["_enriched_at"] = datetime.now().isoformat()
                enriched_map[org_id] = enriched

                rev = enriched.get("annual_revenue_printed", "N/A")
                emp = enriched.get("estimated_num_employees", "N/A")
                ind = enriched.get("industry", "N/A")
                funding = enriched.get("total_funding_printed", "N/A")
                print(f"rev={rev}, emp={emp}, industry={ind}, funding={funding}")
            else:
                enriched_map[org_id] = None
                print("NOT FOUND")

            completed_ids.add(org_id)

            if i % 10 == 0:
                progress["completed_ids"] = list(completed_ids)
                progress["enriched_data"] = enriched_map
                save_progress(progress, ORG_PROGRESS_FILE)
                print(f"  [checkpoint saved: {len(completed_ids)} orgs]")

            time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        enrich_error = e
        print(f"\n[ERROR] Org enrichment aborted: {e}")
        traceback.print_exc()
    finally:
        progress["completed_ids"] = list(completed_ids)
        progress["enriched_data"] = enriched_map
        save_progress(progress, ORG_PROGRESS_FILE)
        enriched_orgs = _save_org_enriched_file(
            organizations,
            enriched_map,
            checkpoint=enrich_error is not None,
            error=str(enrich_error) if enrich_error else None,
        )
        print(f"\n  -> Saved enriched orgs to {ORG_ENRICHED_OUTPUT} (checkpoint={enrich_error is not None})")

    if enrich_error:
        raise enrich_error
    return enriched_orgs


# ---------------------------------------------------------------------------
# PART 2: PEOPLE ENRICHMENT
# ---------------------------------------------------------------------------

def enrich_person(person_id):
    """Enrich a single person by Apollo ID. Returns full person data."""
    url = f"{BASE_URL}/people/match"
    payload = {"id": person_id, "reveal_personal_emails": False}

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)

            if resp.status_code == 429:
                wait = 60 * (attempt + 1)
                print(f"    [RATE LIMITED] Waiting {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code in (404, 422):
                return None

            resp.raise_for_status()
            data = resp.json()
            return data.get("person", None)

        except requests.exceptions.RequestException as e:
            print(f"    [ERROR] attempt {attempt+1}: {e}")
            if attempt < 2:
                time.sleep(10)
    return None


def run_people_enrichment():
    """Enrich all people from apollo_people.json."""

    if not os.path.exists(PEOPLE_INPUT):
        print(f"ERROR: {PEOPLE_INPUT} not found. Run fetch_apollo_data.py first.")
        return

    people_data = load_json(PEOPLE_INPUT)
    people = people_data.get("people", [])
    progress = load_progress(PEOPLE_PROGRESS_FILE)
    completed_ids = set(progress.get("completed_ids", []))
    enriched_map = progress.get("enriched_data", {})

    to_enrich = []
    for person in people:
        pid = person.get("id", "")
        if pid and pid not in completed_ids:
            to_enrich.append((pid, person))

    if (
        HACKATHON_MAX_PEOPLE_TO_ENRICH is not None
        and len(to_enrich) > HACKATHON_MAX_PEOPLE_TO_ENRICH
    ):
        print(
            f"[enrichment cap] Enriching first {HACKATHON_MAX_PEOPLE_TO_ENRICH} of "
            f"{len(to_enrich)} remaining people (set HACKATHON_MAX_PEOPLE_TO_ENRICH=None for all)."
        )
        to_enrich = to_enrich[:HACKATHON_MAX_PEOPLE_TO_ENRICH]

    total = len(to_enrich)
    already_done = len(completed_ids)

    print("\n" + "=" * 70)
    print("PART 2: PEOPLE ENRICHMENT")
    print(f"Total people: {len(people)} | Already enriched: {already_done} | Remaining: {total}")
    print(f"Estimated time: ~{total * 7 // 60} minutes")
    print("=" * 70)

    enrich_error = None
    consecutive_not_found = 0
    early_stop_not_found = False

    try:
        for i, (pid, person) in enumerate(to_enrich, 1):
            first_name = person.get("first_name", "?")
            org_name = person.get("organization", {}).get("name", "?")
            print(f"[{already_done + i}/{len(people)}] {first_name} at {org_name}...", end=" ")

            enriched = enrich_person(pid)

            if enriched:
                consecutive_not_found = 0
                enriched["_search_label"] = person.get("_search_label", "")
                enriched["_enriched_at"] = datetime.now().isoformat()
                enriched_map[pid] = enriched

                full_name = f"{enriched.get('first_name', '')} {enriched.get('last_name', '')}"
                email = enriched.get("email", "N/A")
                linkedin = enriched.get("linkedin_url", "N/A")
                title = enriched.get("title", "N/A")
                print(f"{full_name} | {title} | email={'YES' if email else 'NO'} | linkedin={'YES' if linkedin else 'NO'}")
            else:
                enriched_map[pid] = None
                print("NOT FOUND")
                consecutive_not_found += 1

            completed_ids.add(pid)

            if consecutive_not_found >= PEOPLE_ENRICH_CONSECUTIVE_NOT_FOUND_STOP:
                early_stop_not_found = True
                print(
                    f"\n[stop] {PEOPLE_ENRICH_CONSECUTIVE_NOT_FOUND_STOP} consecutive NOT_FOUND — "
                    "halting people enrichment. Writing success-only JSON from checkpoint."
                )
                break

            if i % 10 == 0:
                progress["completed_ids"] = list(completed_ids)
                progress["enriched_data"] = enriched_map
                save_progress(progress, PEOPLE_PROGRESS_FILE)
                print(f"  [checkpoint saved: {len(completed_ids)} people]")

            time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        enrich_error = e
        print(f"\n[ERROR] People enrichment aborted: {e}")
        traceback.print_exc()
    finally:
        progress["completed_ids"] = list(completed_ids)
        progress["enriched_data"] = enriched_map
        save_progress(progress, PEOPLE_PROGRESS_FILE)
        success_only = early_stop_not_found and enrich_error is None
        enriched_people = _save_people_enriched_file(
            people,
            enriched_map,
            checkpoint=early_stop_not_found or enrich_error is not None,
            error=str(enrich_error) if enrich_error else None,
            success_only=success_only,
            early_stop_not_found=early_stop_not_found,
        )
        n_ok = sum(1 for v in enriched_map.values() if v)
        print(
            f"\n  -> Saved to {PEOPLE_ENRICHED_OUTPUT} "
            f"(success_only={success_only}, checkpoint={early_stop_not_found or enrich_error is not None}, "
            f"successful_in_map={n_ok})"
        )

    if enrich_error:
        raise enrich_error
    return enriched_people


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    start = datetime.now()
    print(f"Apollo Enrichment Pipeline — DataPilot Demand Identification Engine")
    print(f"Started: {start.isoformat()}")
    print(f"API Base: {BASE_URL}")
    print()

    enriched_orgs = run_org_enrichment()
    if enriched_orgs is None:
        return

    enriched_people = None
    try:
        enriched_people = run_people_enrichment()
    except Exception:
        print("\n[ERROR] People enrichment failed; org output and progress files are preserved.")
        traceback.print_exc()
        sys.exit(1)

    elapsed = datetime.now() - start

    # Only remove progress scratch files after both pipelines finish without error.
    for f in [ORG_PROGRESS_FILE, PEOPLE_PROGRESS_FILE]:
        if os.path.exists(f):
            os.remove(f)

    org_count = len(enriched_orgs) if enriched_orgs else 0
    people_count = len(enriched_people) if enriched_people else 0

    print("\n" + "=" * 70)
    print("ENRICHMENT COMPLETE")
    print(f"  Organizations enriched: {org_count}")
    print(f"  People enriched:        {people_count}")
    print(f"  Output files:")
    print(f"    - {ORG_ENRICHED_OUTPUT}")
    print(f"    - {PEOPLE_ENRICHED_OUTPUT}")
    print(f"  Elapsed: {elapsed}")
    print("=" * 70)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "export-people-success":
        export_people_success_only_from_checkpoint()
    else:
        main()
