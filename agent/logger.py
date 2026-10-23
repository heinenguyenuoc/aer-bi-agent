"""Log mọi câu hỏi vào SQLite: SQL sinh ra, trạng thái, độ trễ, token, feedback đúng/sai (cho Admin console)."""
import sqlite3
import pathlib
from datetime import datetime, timezone

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, question TEXT, sql TEXT, status TEXT, error TEXT, attempts INTEGER,
  latency_ms INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER,
  provider TEXT, model TEXT, row_count INTEGER, feedback INTEGER
)"""


class QueryLog:
    def __init__(self, path: str):
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as c:
            c.execute(SCHEMA)

    def _conn(self):
        return sqlite3.connect(self.path)

    def add(self, **r) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO queries (ts, question, sql, status, error, attempts, latency_ms, prompt_tokens,"
                " completion_tokens, provider, model, row_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), r["question"], r.get("sql"), r["status"],
                 r.get("error"), r.get("attempts", 1), r.get("latency_ms", 0), r.get("prompt_tokens", 0),
                 r.get("completion_tokens", 0), r.get("provider"), r.get("model"), r.get("row_count")))
            return cur.lastrowid

    def set_feedback(self, log_id: int, value: int) -> None:
        """value: 1 = đúng, -1 = sai."""
        with self._conn() as c:
            c.execute("UPDATE queries SET feedback=? WHERE id=?", (value, log_id))

    def recent(self, n: int = 100) -> pd.DataFrame:
        with self._conn() as c:
            return pd.read_sql_query("SELECT * FROM queries ORDER BY id DESC LIMIT ?", c, params=(n,))

    def stats(self) -> pd.DataFrame:
        with self._conn() as c:
            return pd.read_sql_query(
                """SELECT COALESCE(provider,'?') || ' / ' || COALESCE(model,'?') AS model,
                          COUNT(*) AS queries,
                          ROUND(100.0 * SUM(status = 'ok') / COUNT(*), 1) AS success_pct,
                          ROUND(AVG(latency_ms)) AS avg_latency_ms,
                          SUM(prompt_tokens) AS prompt_tokens, SUM(completion_tokens) AS completion_tokens,
                          SUM(feedback = 1) AS thumbs_up, SUM(feedback = -1) AS thumbs_down
                   FROM queries GROUP BY 1 ORDER BY queries DESC""", c)
