import os
import re
import sqlite3
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional dependency for sqlite-only local runs
    psycopg = None
    dict_row = None


_QMARK_PATTERN = re.compile(r"\?")


def is_postgres_enabled() -> bool:
    database_url = os.getenv("DATABASE_URL", "").strip().strip('"').strip("'").lower()
    return database_url.startswith("postgres://") or database_url.startswith("postgresql://")


def _to_postgres_sql(query: str) -> str:
    return _QMARK_PATTERN.sub("%s", query)


class PostgresCompatCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class PostgresCompatConnection:
    def __init__(self, connection: Any):
        self._connection = connection

    def execute(self, query: str, params: tuple | list | None = None) -> PostgresCompatCursor:
        cur = self._connection.cursor()
        pg_query = _to_postgres_sql(query)
        if params is None:
            cur.execute(pg_query)
        else:
            cur.execute(pg_query, params)
        return PostgresCompatCursor(cur)

    def commit(self) -> None:
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()


def get_db_connection(sqlite_path: str) -> Any:
    if is_postgres_enabled():
        if psycopg is None:
            raise RuntimeError("DATABASE_URL is set for PostgreSQL but psycopg is not installed")
        database_url = os.getenv("DATABASE_URL", "").strip().strip('"').strip("'")
        if not database_url:
            raise RuntimeError("DATABASE_URL must be set when PostgreSQL is enabled")
        conn = psycopg.connect(database_url, row_factory=dict_row)
        return PostgresCompatConnection(conn)

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    return conn
