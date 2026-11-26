"""Tools the SQL-generation step may call. Each is read-only and runs through
the same SQL guard and read-only connection as the final query."""
from __future__ import annotations

import json

from app.sql.executor import Database, QueryError
from app.sql.guard import UnsafeSQLError, check_sql

CHART_TYPES = ["bar", "line", "pie", "scatter", "table"]

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "profile_table",
        "description": "Row count plus, for each column, distinct count, min, max and up to 5 sample values.",
        "parameters": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]}}},
    {"type": "function", "function": {
        "name": "get_distinct_values",
        "description": "Up to 25 distinct values of one column, to match filters exactly (e.g. statuses, categories).",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "column": {"type": "string"}}, "required": ["table", "column"]}}},
    {"type": "function", "function": {
        "name": "submit_sql",
        "description": "Submit the final read-only SQL query and how to chart its result.",
        "parameters": {"type": "object", "properties": {
            "sql": {"type": "string", "description": "One SELECT/WITH query"},
            "chart_type": {"type": "string", "enum": CHART_TYPES},
            "x": {"type": ["string", "null"], "description": "result column for the x axis / labels; null for table"},
            "y": {"type": ["string", "null"], "description": "numeric result column for the y axis / values; null for table"},
            "explanation": {"type": "string", "description": "one sentence on what the query computes"}},
            "required": ["sql", "chart_type"]}}},
]

# Tool-level authorization: only these may be executed by the model.
ALLOWED_TOOLS = {"profile_table", "get_distinct_values", "submit_sql"}


def run_tool(db: Database, name: str, args: dict) -> str:
    """Execute a lookup tool and return a JSON string for the model."""
    if name not in ALLOWED_TOOLS or name == "submit_sql":
        return json.dumps({"error": f"tool '{name}' is not available"})
    try:
        table = str(args.get("table", ""))
        if table not in db.list_tables():
            return json.dumps({"error": f"unknown table '{table}'", "available": db.list_tables()})
        if name == "profile_table":
            return json.dumps(db.profile(table), default=str)
        column = str(args.get("column", ""))
        if column not in {c["name"] for c in db.describe(table)}:
            return json.dumps({"error": f"unknown column '{column}' in {table}"})
        q = f'SELECT DISTINCT "{column}" AS v FROM "{table}" WHERE "{column}" IS NOT NULL ORDER BY 1'
        df = db.execute(check_sql(q, max_rows=25).limited_sql).df
        return json.dumps({"table": table, "column": column, "values": df["v"].head(25).tolist()}, default=str)
    except (QueryError, UnsafeSQLError) as exc:
        return json.dumps({"error": str(exc)})
