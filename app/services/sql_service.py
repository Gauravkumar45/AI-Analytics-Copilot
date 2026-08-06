import os
import re

import pandas as pd
from sqlalchemy import create_engine, inspect, text

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://admin:password@localhost:5432/analytics")

# Ensure SQLAlchemy uses the psycopg (psycopg3) DBAPI when available.
# If the URL is the generic `postgresql://...`, prefer `postgresql+psycopg://...`.
if DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)


class SqlService:
    def __init__(self) -> None:
        self.engine = create_engine(DATABASE_URL, future=True)

    def execute_sql(self, query: str) -> pd.DataFrame:
        if not re.match(r"^\s*(SELECT|WITH)\b", query, flags=re.IGNORECASE):
            raise ValueError("Only read-only SELECT queries can be executed")
        with self.engine.connect() as connection:
            result = connection.execute(text(query))
            return pd.DataFrame(result.mappings().all())

    def describe_schema(self, table_name: str | None = None) -> dict[str, list[str]]:
        """Return the public tables and columns available for analytics queries."""
        inspector = inspect(self.engine)
        tables = inspector.get_table_names(schema="public")
        if not tables:
            raise ValueError("The analytics database has no tables. Load data before asking a question.")

        if table_name:
            if table_name not in tables:
                available = ", ".join(sorted(tables))
                raise ValueError(f"Table '{table_name}' does not exist. Available tables: {available}")
            tables = [table_name]

        return {
            table: [
                f"{column['name']} ({column['type']})"
                for column in inspector.get_columns(table, schema="public")
            ]
            for table in sorted(tables)
        }
