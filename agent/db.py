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


def data_coverage(path: str) -> dict:
    con = connect(path)
    try:
        lo, hi = con.execute(
            "SELECT MIN(production_month), MAX(production_month) FROM fact_well_production_monthly").fetchone()
        return {"min_month": str(lo), "max_month": str(hi)}
    finally:
        con.close()
