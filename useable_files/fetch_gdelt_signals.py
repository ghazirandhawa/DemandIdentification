"""
GDELT DOC 2.0 API — Pain & Strategic Signal Extraction
for DataPilot Demand Identification Engine

FREE. No API key. No registration.
Searches last 3 months of global news coverage across 65 languages.

Fixes from v1:
  - Timeout increased to 90s with 3 retries + exponential backoff
  - Sleep between queries increased to 10s to avoid 429
  - Vertical queries rewritten: dropped invalid near10:() OR syntax
  - Reduced maxrecords to 75 (GDELT default, more reliable)
"""

import json
import time
import requests
from datetime import datetime

BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# ---------------------------------------------------------------------------
# ICP-ALIGNED SEARCH QUERIES
# ---------------------------------------------------------------------------

PAIN_SIGNAL_QUERIES = [
    {
        "label": "Pain_FailedAI_Pilot",
        "query": '("failed AI project" OR "AI pilot failed" OR "AI implementation failure")',
        "signal_type": "pain",
    },
    {
        "label": "Pain_DataQuality",
        "query": '("data quality" OR "poor data" OR "data silos" OR "dirty data")',
        "signal_type": "pain",
    },
    {
        "label": "Pain_Legacy_Systems",
        "query": '("legacy systems" OR "legacy infrastructure" OR "technical debt" OR "outdated technology")',
        "signal_type": "pain",
    },
    {
        "label": "Pain_ManualReporting",
        "query": '("manual reporting" OR "manual data entry" OR "reporting bottleneck" OR "spreadsheet")',
        "signal_type": "pain",
    },
    {
        "label": "Pain_DataBreach_Governance",
        "query": '("data breach" OR "compliance violation" OR "regulatory fine" OR "data governance")',
        "signal_type": "pain",
    },
    {
        "label": "Pain_TransformationFailed",
        "query": '("digital transformation failed" OR "transformation stalled" OR "modernization challenges")',
        "signal_type": "pain",
    },
]

STRATEGIC_SIGNAL_QUERIES = [
    {
        "label": "Strategic_DigitalTransformation",
        "query": '("digital transformation" OR "data transformation" OR "enterprise modernization")',
        "signal_type": "strategic",
    },
    {
        "label": "Strategic_ERP_Migration",
        "query": '("ERP migration" OR "ERP upgrade" OR "SAP implementation" OR "ERP modernization")',
        "signal_type": "strategic",
    },
    {
        "label": "Strategic_CloudMigration",
        "query": '("cloud migration" OR "cloud transformation" OR "AWS migration" OR "Azure migration")',
        "signal_type": "strategic",
    },
    {
        "label": "Strategic_AI_Adoption",
        "query": '("AI adoption" OR "AI strategy" OR "AI roadmap" OR "generative AI" OR "enterprise AI")',
        "signal_type": "strategic",
    },
    {
        "label": "Strategic_DataStrategy",
        "query": '("data strategy" OR "chief data officer" OR "head of data" OR "data-driven")',
        "signal_type": "strategic",
    },
    {
        "label": "Strategic_Industry40",
        "query": '("Industry 4.0" OR "smart manufacturing" OR "industrial IoT" OR "manufacturing automation")',
        "signal_type": "strategic",
    },
]

# Vertical queries: use simple keyword combos (near operator only takes single quoted words)
VERTICAL_SIGNAL_QUERIES = [
    {
        "label": "Vertical_Manufacturing_Data",
        "query": '"manufacturing" "data analytics" sourcecountry:US',
        "signal_type": "strategic",
    },
    {
        "label": "Vertical_Manufacturing_Legacy",
        "query": '"manufacturing" "legacy systems" sourcecountry:US',
        "signal_type": "pain",
    },
    {
        "label": "Vertical_Retail_Analytics",
        "query": '"retail" "data analytics" sourcecountry:US',
        "signal_type": "strategic",
    },
    {
        "label": "Vertical_Ecommerce_Data",
        "query": '"ecommerce" ("data silos" OR "analytics") sourcecountry:US',
        "signal_type": "pain",
    },
    {
        "label": "Vertical_LifeSciences_AI",
        "query": '("life sciences" OR "pharmaceutical") ("artificial intelligence" OR "data management") sourcecountry:US',
        "signal_type": "strategic",
    },
    {
        "label": "Vertical_FoodBeverage_Supply",
        "query": '("food and beverage" OR "restaurant") ("supply chain" OR "demand forecasting") sourcecountry:US',
        "signal_type": "strategic",
    },
    {
        "label": "Vertical_Agency_Reporting",
        "query": '"marketing agency" ("data analytics" OR "reporting automation") sourcecountry:US',
        "signal_type": "pain",
    },
    {
        "label": "Vertical_Startup_DataInfra",
        "query": '("startup" OR "Series A" OR "Series B") "data infrastructure" sourcecountry:US',
        "signal_type": "strategic",
    },
]

ALL_QUERIES = PAIN_SIGNAL_QUERIES + STRATEGIC_SIGNAL_QUERIES + VERTICAL_SIGNAL_QUERIES


def fetch_gdelt_articles(query_str, max_records=75, timespan="3months"):
    """Fetch articles from GDELT DOC 2.0 API with retries."""
    params = {
        "query": query_str,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": max_records,
        "timespan": timespan,
        "sort": "DateDesc",
    }

    for attempt in range(3):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=90)

            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"  [429 RATE LIMITED] Waiting {wait}s before retry...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.JSONDecodeError:
            return None
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            wait = 15 * (attempt + 1)
            print(f"  [TIMEOUT/CONN ERROR] attempt {attempt+1}/3 — retrying in {wait}s...")
            time.sleep(wait)
            continue
        except requests.exceptions.RequestException as e:
            print(f"  [ERROR] {e}")
            return None

    print(f"  [FAILED] All 3 attempts exhausted")
    return None


def main():
    all_articles = []
    seen_urls = set()
    total_queries = len(ALL_QUERIES)

    print("=" * 70)
    print("GDELT DOC 2.0 — Pain & Strategic Signal Extraction")
    print("FREE — No API key required")
    print(f"Queries: {total_queries} | Timespan: last 3 months")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 70)

    for qi, query_cfg in enumerate(ALL_QUERIES, 1):
        label = query_cfg["label"]
        query_str = query_cfg["query"]
        signal_type = query_cfg["signal_type"]

        print(f"\n[{qi}/{total_queries}] {label}")
        print(f"  Query: {query_str[:80]}...")

        data = fetch_gdelt_articles(query_str)

        if not data or "articles" not in data:
            print(f"  No articles returned")
            time.sleep(10)
            continue

        articles = data.get("articles", [])
        new_count = 0

        for article in articles:
            url = article.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                article["_signal_type"] = signal_type
                article["_query_label"] = label
                article["_extraction_source"] = "gdelt"
                article["_extracted_at"] = datetime.now().isoformat()
                all_articles.append(article)
                new_count += 1

        print(f"  Found {len(articles)} articles, {new_count} new")
        time.sleep(10)

    print(f"\n{'=' * 70}")
    print(f"GDELT Extraction Complete")
    print(f"Total unique articles: {len(all_articles)}")

    pain_count = sum(1 for a in all_articles if a["_signal_type"] == "pain")
    strategic_count = sum(1 for a in all_articles if a["_signal_type"] == "strategic")
    print(f"  Pain signals:      {pain_count}")
    print(f"  Strategic signals:  {strategic_count}")

    output = {
        "metadata": {
            "source": "gdelt_doc_2.0",
            "api_endpoint": BASE_URL,
            "extraction_timestamp": datetime.now().isoformat(),
            "total_articles": len(all_articles),
            "pain_signal_count": pain_count,
            "strategic_signal_count": strategic_count,
            "queries_run": [q["label"] for q in ALL_QUERIES],
            "timespan": "3 months",
            "purpose": "DataPilot Demand Identification Engine — Pain & Strategic Signals from global news",
            "api_key_required": False,
            "cost": "Free",
        },
        "articles": all_articles,
    }

    output_path = "gdelt_signals_raw.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()
