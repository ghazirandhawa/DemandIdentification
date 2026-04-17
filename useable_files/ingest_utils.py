"""Shared helpers for CockroachDB ingestion."""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import psycopg2
from dateutil import parser as date_parser
from psycopg2.extras import Json, execute_batch

# Re-export for ingest scripts
__all__ = ["get_connection", "parse_ts", "parse_date", "Json", "execute_batch", "BASE_DIR"]


def _load_db_config() -> dict:
    try:
        from db_config import DB_CONFIG as cfg
    except ImportError as e:
        raise RuntimeError("Create db_config.py with DB_CONFIG dict (host, port, database, user, password, sslmode).") from e
    return dict(cfg)


def get_connection():
    return psycopg2.connect(**_load_db_config())


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def json_path(name: str) -> str:
    return os.path.join(BASE_DIR, name)


def parse_ts(val: Any) -> datetime | None:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val
    try:
        return date_parser.parse(str(val))
    except (ValueError, TypeError, OverflowError):
        return None


def parse_date(val: Any) -> date | None:
    if val is None or val == "":
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        dt = date_parser.parse(str(val), default=datetime(1970, 1, 1))
        return dt.date()
    except (ValueError, TypeError, OverflowError):
        return None


def as_int(val: Any) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return None


def as_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
