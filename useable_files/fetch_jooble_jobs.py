import json
import time
import requests
from datetime import datetime

API_KEY = "cf9d762f-72da-4ddc-ba51-9e0ac9f3e606"
BASE_URL = f"https://jooble.org/api/{API_KEY}"

# ICP-aligned search queries derived from DataPilot ICP v3
# Tier 1 signals: data hiring, greenfield data, data infrastructure
# Tier 2 signals: ERP upgrade, digital transformation, platform migration
# Tier 3 signals: AI/ML startup roles, white-label delivery
SEARCH_QUERIES = [
    # --- HIGH-INTENT: Data hiring signals (Tier 1 signals) ---
    "data engineer",
    "data scientist",
    "BI developer",
    "analytics manager",
    "Chief Data Officer",
    "head of data",
    "VP analytics",
    "data architect",
    "MLOps engineer",
    "machine learning engineer",

    # --- Greenfield / build-from-scratch signals ---
    "data infrastructure",
    "data strategy",
    "AI roadmap",
    "build data team",
    "greenfield data",
    "data pipeline",
    "data warehouse",

    # --- Digital transformation / modernization signals (Tier 2) ---
    "digital transformation",
    "ERP migration",
    "data modernization",
    "platform migration",
    "analytics transformation",

    # --- AI/ML / GenAI demand signals ---
    "AI engineer",
    "generative AI",
    "NLP engineer",
    "LLM engineer",
    "AI product manager",

    # --- Marketing analytics (Tier 1 - agencies/DTC) ---
    "marketing analytics",
    "marketing data analyst",
    "CRM analytics",

    # --- Operations analytics (Tier 2 - manufacturing/F&B) ---
    "operations analytics",
    "supply chain analytics",
    "demand forecasting",
    "predictive maintenance",

    # --- Life Sciences (Tier 2) ---
    "life sciences data",
    "pharma data scientist",
    "clinical data analyst",

    # --- Automation signals ---
    "data automation",
    "reporting automation",
    "AI automation",
]

# Target U.S. locations from ICP geographic focus
LOCATIONS = [
    "Texas",
    "California",
    "New York",
    "New Jersey",
    "Florida",
    "Ohio",
    "Illinois",
    "Pennsylvania",
    "Washington DC",
    "Boston",
    "Dallas",
    "Austin",
    "San Francisco",
    "Chicago",
]

def fetch_jooble_jobs(keyword, location, page=1):
    payload = {
        "keywords": keyword,
        "location": location,
        "page": page,
    }
    try:
        resp = requests.post(BASE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"  [ERROR] {keyword} | {location} | page {page}: {e}")
        return None

def main():
    all_jobs = []
    seen_ids = set()
    total_queries = len(SEARCH_QUERIES) * len(LOCATIONS)
    query_count = 0

    print(f"Starting Jooble extraction: {len(SEARCH_QUERIES)} keywords x {len(LOCATIONS)} locations = {total_queries} queries")
    print(f"Timestamp: {datetime.now().isoformat()}\n")

    for keyword in SEARCH_QUERIES:
        for location in LOCATIONS:
            query_count += 1
            print(f"[{query_count}/{total_queries}] Fetching: '{keyword}' in '{location}'...", end=" ")

            for page in range(1, 4):  # up to 3 pages per combo
                data = fetch_jooble_jobs(keyword, location, page)
                if not data or "jobs" not in data:
                    break

                jobs = data.get("jobs", [])
                if not jobs:
                    break

                new_count = 0
                for job in jobs:
                    job_id = job.get("id", job.get("link", ""))
                    if job_id and job_id not in seen_ids:
                        seen_ids.add(job_id)
                        job["_search_keyword"] = keyword
                        job["_search_location"] = location
                        job["_extraction_source"] = "jooble"
                        job["_extracted_at"] = datetime.now().isoformat()
                        all_jobs.append(job)
                        new_count += 1

                if page == 1:
                    total = data.get("totalCount", 0)
                    print(f"total={total}, new={new_count}", end="")

                if len(jobs) < 20:
                    break

                time.sleep(0.3)

            print()
            time.sleep(0.25)

    print(f"\n--- Jooble Extraction Complete ---")
    print(f"Total unique jobs collected: {len(all_jobs)}")

    output = {
        "metadata": {
            "source": "jooble",
            "api_endpoint": "https://jooble.org/api/",
            "extraction_timestamp": datetime.now().isoformat(),
            "total_jobs": len(all_jobs),
            "search_keywords": SEARCH_QUERIES,
            "search_locations": LOCATIONS,
            "purpose": "DataPilot Demand Identification Engine - Job Postings Demand Signals",
        },
        "jobs": all_jobs,
    }

    output_path = "jooble_jobs_raw.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Data saved to {output_path}")

if __name__ == "__main__":
    main()
