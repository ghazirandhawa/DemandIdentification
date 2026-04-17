from __future__ import annotations

import os
from typing import TypedDict


def database_url() -> str:
    u = (os.environ.get("DATABASE_URL") or "").strip()
    if not u:
        raise RuntimeError("DATABASE_URL is not set (add to repo .env or export).")
    return u


def gold_schema() -> str:
    return (os.environ.get("API_GOLD_SCHEMA") or "gold").strip() or "gold"


class TableSpec(TypedDict):
    table: str
    partition: list[str]
    dedupe_order: str
    default_sort: str


# Deduplication: keep one row per partition key, newest by dedupe_order.
# Adjust partition/dedupe_order if your gold schema changes.
TABLE_SPECS: dict[str, TableSpec] = {
    "opportunities": {
        "table": "opportunities",
        "partition": ["silver_signal_id"],
        "dedupe_order": "created_at",
        "default_sort": "priority",
    },
    "opportunity_insights": {
        "table": "opportunity_insights",
        "partition": ["opportunity_id"],
        "dedupe_order": "generated_at",
        "default_sort": "generated_at",
    },
}
