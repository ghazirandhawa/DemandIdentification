import json
import time
import requests
from datetime import datetime

APP_ID = "aa7581fb"
APP_KEY = "c132d45b8b81912720dc91dc38e443fc"
BASE_URL = "https://api.adzuna.com/v1/api/jobs/us/search"

# ICP-aligned search queries from DataPilot ICP v3
SEARCH_QUERIES = [
    # --- HIGH-INTENT: Data hiring signals (Tier 1) ---
    "data engineer",
    "data scientist",
    "BI developer",
    "business intelligence analyst",
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
    "build data team",
    "data pipeline engineer",
    "data warehouse engineer",

    # --- Digital transformation / modernization (Tier 2) ---
    "digital transformation",
    "ERP migration",
    "data modernization",
    "platform migration",
    "analytics transformation",
    "data governance",

    # --- AI/ML / GenAI demand signals ---
    "AI engineer",
    "generative AI",
    "NLP engineer",
    "LLM engineer",
    "AI product manager",
    "applied AI",

    # --- Marketing analytics (Tier 1 - agencies/DTC) ---
    "marketing analytics",
    "marketing data analyst",
    "CRM analytics",
    "campaign analytics",

    # --- Operations analytics (Tier 2 - manufacturing/F&B) ---
    "operations analytics",
    "supply chain analytics",
    "demand forecasting analyst",
    "predictive maintenance",
    "manufacturing analytics",

    # --- Life Sciences (Tier 2) ---
    "life sciences data analyst",
    "pharma data scientist",
    "clinical data analyst",
    "biotech data engineer",

    # --- Automation signals ---
    "data automation engineer",
    "reporting automation",
    "AI automation",
    "workflow automation",
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
    "Boston, Massachusetts",
    "Dallas, Texas",
    "Austin, Texas",
    "San Francisco, California",
    "Chicago, Illinois",
]

def fetch_adzuna_jobs(keyword, location, page=1, results_per_page=50):
    url = f"{BASE_URL}/{page}"
    params = {
        "app_id": APP_ID,
        "app_key": APP_KEY,
        "results_per_page": results_per_page,
        "what": keyword,
        "where": location,
        "content-type": "application/json",
        "sort_by": "date",
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
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

    print(f"Starting Adzuna extraction: {len(SEARCH_QUERIES)} keywords x {len(LOCATIONS)} locations = {total_queries} queries")
    print(f"Timestamp: {datetime.now().isoformat()}\n")

    for keyword in SEARCH_QUERIES:
        for location in LOCATIONS:
            query_count += 1
            print(f"[{query_count}/{total_queries}] Fetching: '{keyword}' in '{location}'...", end=" ")

            for page in range(1, 4):  # up to 3 pages (150 results per combo)
                data = fetch_adzuna_jobs(keyword, location, page)
                if not data or "results" not in data:
                    break

                results = data.get("results", [])
                if not results:
                    break

                new_count = 0
                for job in results:
                    job_id = job.get("id", job.get("redirect_url", ""))
                    job_id_str = str(job_id)
                    if job_id_str and job_id_str not in seen_ids:
                        seen_ids.add(job_id_str)
                        job["_search_keyword"] = keyword
                        job["_search_location"] = location
                        job["_extraction_source"] = "adzuna"
                        job["_extracted_at"] = datetime.now().isoformat()
                        all_jobs.append(job)
                        new_count += 1

                if page == 1:
                    total = data.get("count", 0)
                    print(f"total={total}, new={new_count}", end="")

                if len(results) < 50:
                    break

                time.sleep(0.5)

            print()
            time.sleep(0.35)

    print(f"\n--- Adzuna Extraction Complete ---")
    print(f"Total unique jobs collected: {len(all_jobs)}")

    output = {
        "metadata": {
            "source": "adzuna",
            "api_endpoint": BASE_URL,
            "extraction_timestamp": datetime.now().isoformat(),
            "total_jobs": len(all_jobs),
            "search_keywords": SEARCH_QUERIES,
            "search_locations": LOCATIONS,
            "purpose": "DataPilot Demand Identification Engine - Job Postings Demand Signals",
        },
        "jobs": all_jobs,
    }

    output_path = "adzuna_jobs_raw.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Data saved to {output_path}")

if __name__ == "__main__":
    main()
