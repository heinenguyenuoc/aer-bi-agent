"""SQL guard: allow exactly one SELECT statement, only on tables in the data dictionary, and add a LIMIT.

This is the second layer of protection. The first is the read-only DB with external file access disabled (see agent/db.py).
"""
import sqlglot
from sqlglot import exp


class GuardError(ValueError):
    pass


FORBIDDEN_FUNCS = {
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_ndjson", "read_text",
    "read_blob", "glob", "parquet_scan", "parquet_metadata", "sniff_csv", "getenv", "query", "query_table",
    "duckdb_settings", "duckdb_extensions", "pragma_database_list", "current_setting", "write_csv", "load_extension",
}
_BAD_NODE_NAMES = ["Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Copy", "Attach", "Detach",
                   "Pragma", "Set", "Use", "Merge", "TruncateTable", "Export", "Install", "LoadData"]
BAD_NODES = tuple(getattr(exp, n) for n in _BAD_NODE_NAMES if hasattr(exp, n))
QUERY_TYPES = tuple(t for t in (getattr(exp, "Select", None), getattr(exp, "SetOperation", None),
                                getattr(exp, "Union", None)) if t)


def validate(sql: str, allowed_tables: set[str], max_rows: int = 1000) -> str:
    """Return the normalized SQL (with LIMIT), or raise GuardError with a message that is fed back to the LLM to fix it."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise GuardError("Empty SQL.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.SqlglotError as e:
        raise GuardError(f"SQL could not be parsed: {e}")
    if len(statements) != 1:
        raise GuardError("Exactly one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, QUERY_TYPES):
        raise GuardError("Only SELECT queries are allowed.")

    for node in tree.walk():
        if isinstance(node, BAD_NODES):
            raise GuardError(f"Statement type not allowed: {type(node).__name__}.")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    allowed = {t.lower() for t in allowed_tables}
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            raise GuardError("Table functions (e.g. read_csv, range) are not allowed.")
        if t.catalog or (t.db and t.db.lower() != "main"):
            raise GuardError("Schema/catalog-qualified tables are not allowed.")
        name = t.name.lower()
        if name not in allowed and name not in cte_names:
            raise GuardError(f"Unknown table '{t.name}'. Allowed tables: {', '.join(sorted(allowed))}.")

    for f in tree.find_all(exp.Func):
        fname = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if fname in FORBIDDEN_FUNCS:
            raise GuardError(f"Function '{fname}' is not allowed.")

    limit = tree.args.get("limit")
    too_big = False
    if limit is not None:
        lit = limit.args.get("expression")
        too_big = isinstance(lit, exp.Literal) and lit.is_int and int(lit.this) > max_rows
    if limit is None or too_big:
        tree = tree.limit(max_rows)
    return tree.sql(dialect="duckdb")
