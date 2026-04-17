"""
Gold-layer read API for CockroachDB (opportunities, opportunity_insights).

Run from repository root::

  pip install -r requirements.txt
  uvicorn API.app:app --reload --host 0.0.0.0 --port 8000

Expose publicly with ngrok (install ngrok separately)::

  ngrok http 8000
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from .config import TABLE_SPECS
from .db import get_connection
from .query_engine import (
    count_deduped,
    fetch_table_columns,
    get_spec,
    query_deduped_page,
    validate_fetch_columns,
)

TableName = Literal["opportunities", "opportunity_insights"]

app = FastAPI(title="DemandIdentification Gold API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TableQueryPayload(BaseModel):
    N_ROWS: int = Field(..., ge=1, le=500, description="Page size (max 500).")
    PAGE_NUM: int = Field(..., ge=1, description="1-based page index.")
    COLUMNS_TO_FETCH: list[str] = Field(..., description="Column names to return (non-empty).")
    SORT_COL: str | None = Field(
        None,
        description="Column to sort by (must exist on table). Default: table-specific.",
    )

    @field_validator("COLUMNS_TO_FETCH", mode="before")
    @classmethod
    def normalize_columns(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("COLUMNS_TO_FETCH must be a JSON array of column name strings.")
        out = [str(x).strip() for x in v if str(x).strip()]
        if not out:
            raise ValueError("COLUMNS_TO_FETCH must contain at least one non-empty column name.")
        return out


def _default_sort(table_key: TableName) -> str:
    return TABLE_SPECS[table_key]["default_sort"]


def _paginate_payload(table_key: TableName, body: TableQueryPayload) -> dict[str, Any]:
    spec = get_spec(table_key)
    table = spec["table"]
    sort_col = (body.SORT_COL or _default_sort(table_key)).strip()

    with get_connection() as conn:
        available_list = fetch_table_columns(conn, table)
        available = set(available_list)

        cols, err = validate_fetch_columns(available, list(body.COLUMNS_TO_FETCH), sort_col)
        if err is not None:
            raise HTTPException(status_code=400, detail=err)

        assert cols is not None
        total = count_deduped(conn, spec, available)
        n = body.N_ROWS
        total_pages = max(1, math.ceil(total / n)) if n else 1
        if body.PAGE_NUM > total_pages:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "page_out_of_range",
                    "message": f"PAGE_NUM {body.PAGE_NUM} exceeds total_pages {total_pages}.",
                    "PAGE_NUM": body.PAGE_NUM,
                    "total_pages": total_pages,
                    "total_rows_after_dedupe": total,
                    "N_ROWS": n,
                },
            )

        rows = query_deduped_page(conn, spec, available, cols, sort_col, n, body.PAGE_NUM)

    next_page = body.PAGE_NUM + 1 if body.PAGE_NUM < total_pages else None
    return {
        "rows": rows,
        "current_page_num": body.PAGE_NUM,
        "next_page_num": next_page,
        "total_pages": total_pages,
        "total_rows_after_dedupe": total,
        "N_ROWS": n,
        "SORT_COL": sort_col,
        "table": table,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/opportunities/columns")
def list_opportunity_columns() -> dict[str, list[str]]:
    spec = get_spec("opportunities")
    with get_connection() as conn:
        cols = fetch_table_columns(conn, spec["table"])
    return {"table": spec["table"], "columns": cols}


@app.get("/api/opportunity_insights/columns")
def list_insight_columns() -> dict[str, list[str]]:
    spec = get_spec("opportunity_insights")
    with get_connection() as conn:
        cols = fetch_table_columns(conn, spec["table"])
    return {"table": spec["table"], "columns": cols}


@app.post("/api/opportunities/query")
def query_opportunities(body: TableQueryPayload) -> dict[str, Any]:
    return _paginate_payload("opportunities", body)


@app.post("/api/opportunity_insights/query")
def query_opportunity_insights(body: TableQueryPayload) -> dict[str, Any]:
    return _paginate_payload("opportunity_insights", body)
