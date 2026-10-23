"""Read-only DuckDB connection with external file access disabled and a query timeout."""
import threading

import duckdb
import pandas as pd


def connect(path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(path, read_only=True, config={"enable_external_access": False})


def run_query(path: str, sql: str, timeout_s: int = 30) -> pd.DataFrame:
    con = connect(path)
    timer = threading.Timer(timeout_s, con.interrupt)
    timer.start()
    try:
        return con.execute(sql).fetchdf()
    except duckdb.InterruptException:
        raise TimeoutError(f"Query exceeded {timeout_s}s and was cancelled.")
    finally:
        timer.cancel()
        con.close()


def data_coverage(path: str, sql: str, label: str = "") -> dict:
    """Run a 'SELECT MIN(x), MAX(x)' query and return {'label', 'min', 'max'} for the prompt and the UI caption."""
    con = connect(path)
    try:
        lo, hi = con.execute(sql).fetchone()
        return {"label": label, "min": str(lo), "max": str(hi)}
    finally:
        con.close()
