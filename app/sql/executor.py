"""Read-only query execution for SQLite (demo) and PostgreSQL.

Second and third safety layers after app.sql.guard: queries run on a
read-only connection with a statement timeout, and results are capped at
MAX_RESULT_ROWS.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from urllib.parse import urlparse

import pandas as pd

from app.core.settings import Settings, get_settings


class QueryError(RuntimeError):
    """The database rejected or timed out the query. Shown to the model for repair."""


@dataclass
class QueryResult:
    df: pd.DataFrame
    truncated: bool
    elapsed_ms: float


def _sqlite_path(url: str) -> str:
    # sqlite:////abs/path.db or sqlite:///rel/path.db
    return url.split("sqlite:///", 1)[1]


class Database:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._engine = None

    # ---- connections -----------------------------------------------------------
    def _sqlite_ro(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{_sqlite_path(self.settings.database_url)}?mode=ro", uri=True)
        deadline = time.monotonic() + self.settings.query_timeout_seconds
        # Abort long-running queries: the handler runs every N VM steps.
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        return conn

    @property
    def engine(self):
        if self._engine is None:
            from sqlalchemy import create_engine
            url = self.settings.database_url
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+psycopg://", 1)
            self._engine = create_engine(url, future=True, pool_pre_ping=True)
        return self._engine

    # ---- schema ----------------------------------------------------------------
    def list_tables(self) -> list[str]:
        if self.settings.is_sqlite:
            with closing(self._sqlite_ro()) as conn:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            tables = [r[0] for r in rows]
        else:
            from sqlalchemy import inspect
            tables = sorted(inspect(self.engine).get_table_names(schema="public"))
        allowed = self.settings.allowed_tables
        return [t for t in tables if not allowed or t.lower() in allowed]

    def describe(self, table: str) -> list[dict]:
        if table not in self.list_tables():
            raise QueryError(f"Unknown table '{table}'. Available: {', '.join(self.list_tables())}")
        if self.settings.is_sqlite:
            with closing(self._sqlite_ro()) as conn:
                rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            return [{"name": r[1], "type": r[2] or "TEXT"} for r in rows]
        from sqlalchemy import inspect
        return [{"name": c["name"], "type": str(c["type"])} for c in inspect(self.engine).get_columns(table, schema="public")]

    def schema_text(self) -> str:
        """Compact schema for the prompt: table(col TYPE, ...)."""
        lines = []
        for table in self.list_tables():
            cols = ", ".join(f"{c['name']} {c['type']}" for c in self.describe(table))
            lines.append(f"{table}({cols})")
        return "\n".join(lines)

    # ---- execution -------------------------------------------------------------
    def execute(self, limited_sql: str) -> QueryResult:
        started = time.perf_counter()
        max_rows = self.settings.max_rows
        try:
            if self.settings.is_sqlite:
                with closing(self._sqlite_ro()) as conn:
                    df = pd.read_sql_query(limited_sql, conn)
            else:
                from sqlalchemy import text
                timeout_ms = int(self.settings.query_timeout_seconds * 1000)
                with self.engine.connect() as conn:
                    with conn.begin():
                        conn.execute(text("SET TRANSACTION READ ONLY"))
                        conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
                        result = conn.execute(text(limited_sql))
                        df = pd.DataFrame(result.mappings().all())
        except sqlite3.OperationalError as exc:
            msg = "Query timed out" if "interrupted" in str(exc).lower() else str(exc)
            raise QueryError(msg) from exc
        except Exception as exc:  # database driver errors vary by backend
            raise QueryError(str(exc).splitlines()[0][:300]) from exc

        truncated = len(df) > max_rows
        return QueryResult(df=df.head(max_rows), truncated=truncated,
                           elapsed_ms=(time.perf_counter() - started) * 1000)

    def profile(self, table: str, max_columns: int = 25) -> dict:
        """Cheap column profile the model can request before writing SQL."""
        from app.sql.guard import check_sql
        columns = self.describe(table)[:max_columns]
        out: dict = {"table": table, "columns": []}
        count = self.execute(check_sql(f'SELECT COUNT(*) AS n FROM "{table}"', max_rows=1).limited_sql)
        out["row_count"] = int(count.df.iloc[0, 0]) if not count.df.empty else 0
        for col in columns:
            name = col["name"]
            q = (f'SELECT COUNT(DISTINCT "{name}") AS distinct_values, MIN("{name}") AS min_value, '
                 f'MAX("{name}") AS max_value FROM "{table}"')
            stats = self.execute(check_sql(q, max_rows=1).limited_sql).df.iloc[0].to_dict()
            sample = self.execute(check_sql(
                f'SELECT DISTINCT "{name}" AS v FROM "{table}" WHERE "{name}" IS NOT NULL', max_rows=5
            ).limited_sql).df["v"].head(5).tolist()
            out["columns"].append({"name": name, "type": col["type"], **{k: _jsonable(v) for k, v in stats.items()},
                                   "sample_values": [_jsonable(v) for v in sample]})
        return out


def _jsonable(value):
    if hasattr(value, "item"):
        return value.item()
    return value


def database_name(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path.rsplit("/", 1)[-1] or parsed.scheme
