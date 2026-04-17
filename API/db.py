from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PgConnection

from .config import database_url


@contextmanager
def get_connection() -> Iterator[PgConnection]:
    conn = psycopg2.connect(database_url())
    try:
        yield conn
    finally:
        conn.close()
