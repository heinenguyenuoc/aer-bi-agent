"""Pipeline: câu hỏi -> prompt (data dictionary + few-shot) -> LLM sinh SQL -> guard -> chạy -> tự sửa nếu lỗi -> log."""
import os
import re
import time
from dataclasses import dataclass, field

import pandas as pd
import yaml

from agent import db, guard, llm
from agent.drift import load_dictionary
from agent.logger import QueryLog


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["llm"]["provider"] = os.getenv("LLM_PROVIDER", cfg["llm"]["provider"])
    cfg["llm"]["model"] = os.getenv("LLM_MODEL", cfg["llm"].get("model") or "")
    return cfg


@dataclass
class Result:
    question: str
    status: str = "error"            # ok | refused | error
    sql: str | None = None
    df: pd.DataFrame | None = None
    error: str | None = None
    attempts: int = 0
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    log_id: int | None = None
    history: list = field(default_factory=list)   # [(sql, lỗi)] các lần thử


def render_dictionary(d: dict, coverage: dict) -> str:
    lines = [f"Database: {d['database']}", "", "UNITS:"]
    lines += [f"- {k}: {v}" for k, v in d["units"].items()]
    lines += ["", "TIME RULES:"] + [f"- {x}" for x in d["time_rules"]]
    lines += [f"- Data coverage: production_month from {coverage['min_month']} to {coverage['max_month']}."]
    lines += ["", "TABLES:"]
    for t, spec in d["tables"].items():
        lines.append(f"\n{t}: {' '.join(str(spec['description']).split())}")
        for c, m in spec["columns"].items():
            unit = f" [{m['unit']}]" if m.get("unit") else ""
            lines.append(f"  - {c} {m['type']}{unit}: {m['description']}")
    lines += ["", "RELATIONSHIPS:"] + [f"- {x}" for x in d["relationships"]]
    lines += ["", "METRICS:"] + [f"- {k}: {v}" for k, v in d["metrics"].items()]
    lines += ["", "RULES:"] + [f"- {x}" for x in d["rules"]]
    lines += ["", "EXAMPLES:"]
    for ex in d["examples"]:
        lines += [f"Q: {ex['question']}", "```sql", ex["sql"].strip(), "```", ""]
    return "\n".join(lines)


SYSTEM = (
    "You are a senior data analyst who writes DuckDB SQL for an Alberta oil and gas database.\n"
    "Given a business question, reply with ONE SQL SELECT query in a ```sql code block and nothing else.\n\n"
)


def extract_sql(text: str) -> str:
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.S | re.I)
    return (m.group(1) if m else text).strip()


class Agent:
    def __init__(self, cfg: dict, provider: str | None = None, model: str | None = None):
        self.cfg = cfg
        self.provider = provider or cfg["llm"]["provider"]
        self.model = model or cfg["llm"].get("model") or llm.default_model(self.provider)
        self.dictionary = load_dictionary(cfg["dictionary_path"])
        self.tables = set(self.dictionary["tables"])
        self.log = QueryLog(cfg["log_path"])
        self.coverage = db.data_coverage(cfg["db_path"])
        self.system_prompt = SYSTEM + render_dictionary(self.dictionary, self.coverage)

    def ask(self, question: str, log: bool = True) -> Result:
        t0 = time.time()
        res = Result(question=question)
        messages = [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": question}]
        max_attempts = 1 + int(self.cfg["max_repair_attempts"])
        try:
            for attempt in range(1, max_attempts + 1):
                res.attempts = attempt
                out = llm.chat(messages, self.provider, self.model, self.cfg["llm"].get("temperature", 0.0))
                res.prompt_tokens += out["prompt_tokens"]
                res.completion_tokens += out["completion_tokens"]
                text = out["text"].strip()
                if text.upper().startswith("CANNOT_ANSWER"):
                    res.status, res.error = "refused", text.split(":", 1)[-1].strip()
                    break
                raw_sql = extract_sql(text)
                res.sql = raw_sql
                try:
                    safe_sql = guard.validate(raw_sql, self.tables, self.cfg["max_rows"])
                    res.df = db.run_query(self.cfg["db_path"], safe_sql, self.cfg["query_timeout_s"])
                    res.sql, res.status, res.error = safe_sql, "ok", None
                    break
                except Exception as e:     # guard / DuckDB / timeout -> đưa lỗi lại cho LLM
                    res.error = f"{type(e).__name__}: {e}"
                    res.history.append((raw_sql, res.error))
                    messages += [{"role": "assistant", "content": f"```sql\n{raw_sql}\n```"},
                                 {"role": "user", "content": f"That query failed with this error:\n{res.error}\n"
                                  "Fix it and reply with the corrected SQL only."}]
        except llm.LLMError as e:
            res.status, res.error = "error", f"LLM error: {e}"
        res.latency_ms = int((time.time() - t0) * 1000)
        if log:
            res.log_id = self.log.add(question=question, sql=res.sql, status=res.status, error=res.error,
                                      attempts=res.attempts, latency_ms=res.latency_ms,
                                      prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
                                      provider=self.provider, model=self.model,
                                      row_count=None if res.df is None else len(res.df))
        return res
