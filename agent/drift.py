"""Schema drift: compare the data dictionary (YAML) with the real DuckDB schema and report differences."""
import yaml

from agent.db import connect

_ALIAS = {"INT": "INTEGER", "INT4": "INTEGER", "FLOAT8": "DOUBLE", "STRING": "VARCHAR", "TEXT": "VARCHAR"}


def _norm(t: str) -> str:
    t = t.upper().strip()
    return _ALIAS.get(t, t)


def load_dictionary(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def check(dictionary: dict, db_path: str) -> dict:
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'main'"
        ).fetchall()
    finally:
        con.close()
    actual: dict[str, dict[str, str]] = {}
    for t, c, d in rows:
        actual.setdefault(t, {})[c] = _norm(d)

    doc = {t: {c: _norm(m["type"]) for c, m in spec["columns"].items()} for t, spec in dictionary["tables"].items()}
    out = {"missing_tables": [], "undocumented_tables": [], "missing_columns": [], "undocumented_columns": [],
           "type_changes": []}
    for t in doc:
        if t not in actual:
            out["missing_tables"].append(t)
    for t in actual:
        if t not in doc:
            out["undocumented_tables"].append(t)
    for t in doc.keys() & actual.keys():
        for c in doc[t].keys() - actual[t].keys():
            out["missing_columns"].append(f"{t}.{c}")
        for c in actual[t].keys() - doc[t].keys():
            out["undocumented_columns"].append(f"{t}.{c}")
        for c in doc[t].keys() & actual[t].keys():
            if doc[t][c] != actual[t][c]:
                out["type_changes"].append(f"{t}.{c}: documented {doc[t][c]}, actual {actual[t][c]}")
    out["ok"] = not any(out[k] for k in ("missing_tables", "undocumented_tables", "missing_columns",
                                         "undocumented_columns", "type_changes"))
    return out
