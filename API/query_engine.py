from __future__ import annotations

from typing import Any

from psycopg2 import sql
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import RealDictCursor

from .config import TABLE_SPECS, gold_schema


def fetch_table_columns(conn: PgConnection, table: str) -> list[str]:
    """Lowercase column names as in information_schema."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (gold_schema(), table),
        )
        return [str(r[0]).lower() for r in cur.fetchall()]


def _resolve_dedupe_order(spec: dict[str, Any], available: set[str]) -> str:
    col = str(spec["dedupe_order"]).lower()
    if col in available:
        return col
    for fallback in ("created_at", "generated_at", "signal_date", "updated_at"):
        if fallback in available:
            return fallback
    raise RuntimeError(
        f"No dedupe time column for {spec['table']}; need {spec['dedupe_order']} or fallback in schema."
    )


def _partition_identifiers(spec: dict[str, Any], available: set[str]) -> list[sql.Identifier]:
    out: list[sql.Identifier] = []
    for p in spec["partition"]:
        pl = str(p).lower()
        if pl not in available:
            raise ValueError(f"Dedupe partition column {p!r} not found on {spec['table']}")
        out.append(sql.Identifier(pl))
    return out


def validate_fetch_columns(
    available: set[str],
    fetch_cols: list[str],
    sort_col: str,
) -> tuple[list[str] | None, dict[str, Any] | None]:
    raw = [str(c).strip() for c in fetch_cols if str(c).strip()]
    if not raw:
        return None, {
            "error": "empty_columns",
            "message": "COLUMNS_TO_FETCH must be a non-empty list of column names.",
            "available_columns": sorted(available),
        }
    normalized = [c.lower() for c in raw]
    missing = [c for c in normalized if c not in available]
    if missing:
        return None, {
            "error": "unknown_columns",
            "message": "One or more requested columns do not exist on this table.",
            "requested_columns": normalized,
            "missing_columns": missing,
            "available_columns": sorted(available),
        }
    sl = sort_col.strip().lower()
    if sl not in available:
        return None, {
            "error": "unknown_sort_column",
            "message": f"SORT_COL {sort_col!r} is not a column on this table.",
            "sort_col": sort_col,
            "available_columns": sorted(available),
        }
    out_cols = list(dict.fromkeys(normalized))
    if sl not in out_cols:
        out_cols.append(sl)
    return out_cols, None


def _dedupe_cte_sql(spec: dict[str, Any], dedupe_order: str, partition: list[sql.Identifier]) -> sql.Composed:
    sch = sql.Identifier(gold_schema())
    tbl = sql.Identifier(spec["table"])
    part = sql.SQL(", ").join(partition)
    return sql.SQL(
        """
        ranked AS (
          SELECT *,
            ROW_NUMBER() OVER (
              PARTITION BY {part}
              ORDER BY {ord} DESC NULLS LAST
            ) AS _rn
          FROM {sch}.{tbl}
        ),
        deduped AS (
          SELECT * FROM ranked WHERE _rn = 1
        )
        """
    ).format(part=part, ord=sql.Identifier(dedupe_order), sch=sch, tbl=tbl)


def count_deduped(conn: PgConnection, spec: dict[str, Any], available: set[str]) -> int:
    dedupe_order = _resolve_dedupe_order(spec, available)
    partition = _partition_identifiers(spec, available)
    cte = _dedupe_cte_sql(spec, dedupe_order, partition)
    query = sql.SQL("WITH {cte} SELECT COUNT(*)::bigint AS c FROM deduped").format(cte=cte)
    with conn.cursor() as cur:
        cur.execute(query)
        row = cur.fetchone()
        return int(row[0]) if row else 0


def query_deduped_page(
    conn: PgConnection,
    spec: dict[str, Any],
    available: set[str],
    columns: list[str],
    sort_col: str,
    n_rows: int,
    page_num: int,
) -> list[dict[str, Any]]:
    dedupe_order = _resolve_dedupe_order(spec, available)
    partition = _partition_identifiers(spec, available)
    offset = (page_num - 1) * n_rows
    cte = _dedupe_cte_sql(spec, dedupe_order, partition)
    flds = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    query = sql.SQL(
        """
        WITH {cte}
        SELECT {flds} FROM deduped
        ORDER BY {sort} ASC NULLS LAST
        LIMIT %s OFFSET %s
        """
    ).format(cte=cte, flds=flds, sort=sql.Identifier(sort_col))

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, [n_rows, offset])
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r.pop("_rn", None)

    return rows


def get_spec(table_key: str) -> dict[str, Any]:
    if table_key not in TABLE_SPECS:
        raise KeyError(table_key)
    return TABLE_SPECS[table_key]
