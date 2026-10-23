"""Measure execution accuracy: does the result of the LLM-written SQL match the result of the reference (gold) SQL?

  python eval/run_eval.py                       # use the provider/model in config.yaml
  python eval/run_eval.py --provider groq --model llama-3.3-70b-versatile
  python eval/run_eval.py --gold-only           # only check that the gold SQL runs (no LLM calls)

Matching rule: ignore column names and row order, round floats to 2 decimals. The agent may return extra columns
(every gold column must appear in the agent's result, and the row counts must be equal).
Questions with expect_refusal: the agent must reply CANNOT_ANSWER.
"""
import argparse
import json
import pathlib
import sys
import time

import pandas as pd
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent import db, guard          # noqa: E402
from agent.pipeline import Agent, load_config   # noqa: E402


def _norm_col(s: pd.Series) -> list:
    def f(v):
        if pd.isna(v):
            return "NULL"
        if isinstance(v, (float,)) or hasattr(v, "dtype") and "float" in str(getattr(v, "dtype", "")):
            return round(float(v), 2)
        if isinstance(v, bool) or str(type(v)).endswith("bool'>"):
            return int(v)
        if isinstance(v, (int,)) or "int" in str(type(v)):
            return float(v)
        return str(v)
    return sorted(map(str, (f(v) for v in s.tolist())))


def match(gold: pd.DataFrame, cand: pd.DataFrame) -> bool:
    if len(gold) != len(cand):
        return False
    cand_cols = [_norm_col(cand.iloc[:, i]) for i in range(cand.shape[1])]
    used = set()
    for i in range(gold.shape[1]):
        g = _norm_col(gold.iloc[:, i])
        hit = next((j for j, c in enumerate(cand_cols) if j not in used and c == g), None)
        if hit is None:
            return False
        used.add(hit)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--gold-only", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    import os
    os.chdir(ROOT)
    cfg = load_config()
    questions = yaml.safe_load(open(ROOT / cfg["questions_path"], encoding="utf-8"))
    if a.limit:
        questions = questions[: a.limit]

    if a.gold_only:
        bad = 0
        for q in questions:
            if q.get("expect_refusal"):
                print(f"{q['id']}: skipped (expects refusal)")
                continue
            try:
                df = db.run_query(cfg["db_path"], guard.validate(q["sql"], set(yaml.safe_load(
                    open(cfg["dictionary_path"], encoding="utf-8"))["tables"]), 10000))
                print(f"{q['id']}: ok ({len(df)} rows)")
            except Exception as e:
                bad += 1
                print(f"{q['id']}: GOLD FAILS -> {e}")
        n = sum(1 for q in questions if not q.get("expect_refusal"))
        print(f"\n{n - bad}/{n} gold queries run.")
        sys.exit(1 if bad else 0)

    agent = Agent(cfg, a.provider, a.model)
    rows, correct = [], 0
    for q in questions:
        res = agent.ask(q["question"])
        if q.get("expect_refusal"):
            ok = res.status == "refused"
        elif res.status == "ok":
            gold = db.run_query(cfg["db_path"], q["sql"], 30)
            ok = match(gold, res.df)
        else:
            ok = False
        correct += ok
        rows.append({"id": q["id"], "question": q["question"], "correct": bool(ok), "status": res.status,
                     "attempts": res.attempts, "latency_ms": res.latency_ms, "sql": res.sql, "error": res.error})
        print(f"{q['id']}: {'PASS' if ok else 'FAIL'}  ({res.status}, {res.attempts} attempt(s), {res.latency_ms} ms)")
        time.sleep(0.5)    # be gentle with free-tier rate limits
    acc = 100.0 * correct / len(questions)
    print(f"\nExecution accuracy: {correct}/{len(questions)} = {acc:.1f}%  [{agent.provider} / {agent.model}]")

    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(exist_ok=True)
    name = f"{agent.provider}__{agent.model}".replace("/", "_").replace(":", "_")
    (out_dir / f"{name}.json").write_text(json.dumps(
        {"provider": agent.provider, "model": agent.model, "accuracy_pct": round(acc, 1), "n": len(questions),
         "correct": correct, "details": rows}, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
