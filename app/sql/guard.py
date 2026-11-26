"""SQL safety guard for model-generated queries.

Defense in depth. This module is the first layer; the executor adds a
read-only connection, a statement timeout and a row cap.

Checks: one statement only, SELECT/WITH only, no write/DDL/admin keywords or
dangerous functions, and every referenced table must exist (and be allowlisted
when ALLOWED_TABLES is set). The query is then wrapped so a row limit always
applies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Keywords that can write or change state even inside a single SELECT/WITH
# statement (e.g. PostgreSQL data-modifying CTEs, SELECT ... INTO, FOR UPDATE).
# Kept short on purpose so ordinary column names do not trigger false positives;
# the read-only connection is the backstop.
FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate", "grant", "revoke",
    "merge", "attach", "detach", "pragma", "vacuum", "copy", "call", "execute", "into",
}
FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "lo_import",
    "lo_export", "dblink", "load_extension", "readfile", "writefile", "pg_terminate_backend",
    "pg_cancel_backend", "set_config", "current_setting",
}
SYSTEM_TABLE_PREFIXES = ("pg_", "sqlite_", "information_schema")

_IDENT = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*)'
_TABLE_REF = re.compile(rf"\b(?:from|join)\s+(?!\()({_IDENT}(?:\s*\.\s*{_IDENT})?)", re.IGNORECASE)
_CTE_NAME = re.compile(rf"(?:\bwith\b|,)\s*(?:recursive\s+)?({_IDENT})\s*(?:\([^)]*\)\s*)?as\s*\(", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z_][\w$]*")


class UnsafeSQLError(ValueError):
    """The query failed a safety check. The message is shown to the model for repair."""


@dataclass
class GuardResult:
    sql: str                      # the cleaned, single statement
    limited_sql: str              # wrapped with a row limit, ready to execute
    tables: list[str] = field(default_factory=list)


def _strip_comments_and_mask(sql: str) -> tuple[str, str]:
    """Return (sql_without_comments, masked) where masked has string-literal and
    quoted-identifier contents replaced, so keyword checks never match inside them."""
    out, masked = [], []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j == -1:
                raise UnsafeSQLError("Unterminated block comment")
            i = j + 2
            out.append(" "); masked.append(" ")
            continue
        if ch in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:  # escaped quote ('' or "")
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise UnsafeSQLError("Unterminated quoted string")
            literal = sql[i:j + 1]
            out.append(literal)
            # keep quoted identifiers visible for table extraction, mask string literals
            masked.append(literal if ch != "'" else "'" + "x" * (len(literal) - 2) + "'")
            i = j + 1
            continue
        out.append(ch); masked.append(ch)
        i += 1
    return "".join(out), "".join(masked)


def _unquote(identifier: str) -> str:
    identifier = identifier.strip()
    if identifier[:1] in '"`[' and len(identifier) >= 2:
        return identifier[1:-1]
    return identifier


def extract_sql(content: str) -> str:
    """Pull a SQL statement out of a model reply (handles ```sql fences)."""
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", content or "", flags=re.IGNORECASE | re.DOTALL)
    return (fenced.group(1) if fenced else content or "").strip()


def check_sql(sql: str, known_tables: set[str] | None = None,
              allowed_tables: tuple[str, ...] = (), max_rows: int = 500) -> GuardResult:
    if not sql or not sql.strip():
        raise UnsafeSQLError("Empty query")
    cleaned, masked = _strip_comments_and_mask(sql)
    cleaned, masked = cleaned.strip(), masked.strip()

    # Allow a single trailing semicolon only.
    if masked.endswith(";"):
        cleaned, masked = cleaned[:-1].rstrip(), masked[:-1].rstrip()
    if ";" in masked:
        raise UnsafeSQLError("Only one SQL statement is allowed")

    first = _WORD.match(masked.lstrip("( \n\t"))
    if not first or first.group(0).lower() not in {"select", "with"}:
        raise UnsafeSQLError("Only read-only SELECT queries are allowed")

    words = {w.lower() for w in _WORD.findall(masked)}
    bad = sorted(words & FORBIDDEN_KEYWORDS)
    if bad:
        raise UnsafeSQLError(f"Forbidden keyword(s) in a read-only query: {', '.join(bad).upper()}")
    bad_fn = sorted(fn for fn in FORBIDDEN_FUNCTIONS if re.search(rf"\b{fn}\s*\(", masked, re.IGNORECASE))
    if bad_fn:
        raise UnsafeSQLError(f"Forbidden function(s): {', '.join(bad_fn)}")

    cte_names = {_unquote(m.group(1)).lower() for m in _CTE_NAME.finditer(masked)}
    tables: list[str] = []
    for match in _TABLE_REF.finditer(masked):
        ref = match.group(1)
        name = _unquote(re.split(r"\s*\.\s*", ref)[-1]).lower()
        schema = _unquote(re.split(r"\s*\.\s*", ref)[0]).lower() if "." in ref else ""
        if name in cte_names:
            continue
        if name.startswith(SYSTEM_TABLE_PREFIXES) or schema.startswith(SYSTEM_TABLE_PREFIXES):
            raise UnsafeSQLError(f"System catalog '{ref}' cannot be queried")
        if name not in tables:
            tables.append(name)

    if allowed_tables:
        blocked = [t for t in tables if t not in allowed_tables]
        if blocked:
            raise UnsafeSQLError(f"Table(s) not allowed: {', '.join(blocked)}. Allowed: {', '.join(allowed_tables)}")
    if known_tables is not None:
        known = {t.lower() for t in known_tables}
        missing = [t for t in tables if t not in known]
        if missing:
            raise UnsafeSQLError(f"Unknown table(s): {', '.join(missing)}. Available: {', '.join(sorted(known))}")

    limited = f"SELECT * FROM (\n{cleaned}\n) AS _copilot_q LIMIT {int(max_rows) + 1}"
    return GuardResult(sql=cleaned, limited_sql=limited, tables=tables)
