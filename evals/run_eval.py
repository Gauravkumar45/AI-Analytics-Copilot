"""Execution-accuracy eval for the Text-to-SQL agent.

For each question the agent's SQL and the gold SQL both run on the demo DB.
A prediction is correct when every column of the gold result matches some
column of the predicted result (same values, compared after rounding), with
the same row count. Order matters only when the gold query uses ORDER BY.
Extra columns and different aliases are allowed.

    GROQ_API_KEY=... python -m evals.run_eval              # all 40 questions
    python -m evals.run_eval --limit 5 --out evals/results  # quick smoke run

Writes results.json and a markdown report with accuracy by difficulty,
first-try vs after-repair success, latency percentiles, tokens and cost.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

from app.core.settings import DEMO_DB_PATH, Settings

HERE = Path(__file__).resolve().parent


def _norm(v):
    if v is None:
        return None
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, int):
        return round(float(v), 2)
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return str(v).strip().lower()


def columns_of(rows: list[tuple]) -> list[list]:
    return [list(col) for col in zip(*rows)] if rows else []


def results_match(gold_rows: list[tuple], pred_rows: list[tuple], ordered: bool) -> bool:
    if len(gold_rows) != len(pred_rows):
        return False
    if not gold_rows:
        return True
    gold_cols = [[_norm(v) for v in c] for c in columns_of(gold_rows)]
    pred_cols = [[_norm(v) for v in c] for c in columns_of(pred_rows)]
    key = (lambda c: c) if ordered else (lambda c: sorted(c, key=lambda x: (x is None, str(x))))
    remaining = [key(c) for c in pred_cols]
    for g in gold_cols:
        gk = key(g)
        if gk in remaining:
            remaining.remove(gk)
        else:
            return False
    return True


def is_ordered(sql: str) -> bool:
    # Outer ORDER BY combined with LIMIT means rank order is part of the answer.
    s = re.sub(r"\s+", " ", sql.lower())
    return " order by " in s and " limit " in s


def run(limit: int | None, out_dir: Path) -> dict:
    from app.agent.graph import run_agent
    from app.llm.client import OpenAICompatibleClient

    settings = Settings()
    if not settings.is_sqlite:
        sys.exit("Evals run on the SQLite demo DB. Unset DATABASE_URL.")
    if not Path(DEMO_DB_PATH).exists():
        sys.exit("Demo DB missing: run python scripts/seed_demo_db.py")
    llm = OpenAICompatibleClient(settings)
    questions = [json.loads(l) for l in (HERE / "questions.jsonl").read_text().splitlines() if l.strip()]
    if limit:
        questions = questions[:limit]

    conn = sqlite3.connect(f"file:{DEMO_DB_PATH}?mode=ro", uri=True)
    records = []
    # Fail fast with a clear message if the model endpoint is unusable.
    from app.llm.client import LLMError
    try:
        llm.chat([{"role": "user", "content": "Reply with OK."}], max_tokens=300)
    except LLMError as exc:
        sys.exit(f"LLM preflight failed: {exc}")

    for q in questions:
        gold = conn.execute(q["gold_sql"]).fetchall()
        started = time.perf_counter()
        state = run_agent(q["question"], llm, settings=settings)
        elapsed = (time.perf_counter() - started) * 1000
        pred_rows, error = [], None
        if state.get("status") == "ok":
            df = state["df"]
            pred_rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
        else:
            error = (state.get("errors") or [state.get("answer")])[-1]
        correct = state.get("status") == "ok" and results_match(gold, pred_rows, is_ordered(q["gold_sql"]))
        usage = state.get("usage", {})
        records.append({
            "id": q["id"], "difficulty": q["difficulty"], "question": q["question"], "correct": correct,
            "status": state.get("status"), "attempts": state.get("attempts", 0) + (1 if state.get("status") == "ok" else 0),
            "repaired": state.get("status") == "ok" and state.get("attempts", 0) > 0,
            "tool_calls": state.get("tool_calls_used", 0), "latency_ms": round(elapsed, 1),
            "prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0),
            "cost_usd": usage.get("cost_usd", 0.0), "sql": state.get("sql"), "gold_sql": q["gold_sql"], "error": error,
        })
        mark = "PASS" if correct else "FAIL"
        print(f"[{mark}] {q['id']} ({q['difficulty']}) {elapsed:.0f} ms  {q['question']}")
        if not correct:
            print(f"        sql: {state.get('sql')}\n        error: {error}")
        time.sleep(0.5)  # stay under free-tier rate limits

    summary = summarize(records, settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps({"summary": summary, "records": records}, indent=2))
    (out_dir / "report.md").write_text(to_markdown(summary, records))
    print(json.dumps(summary, indent=2))
    return summary


def summarize(records: list[dict], settings: Settings) -> dict:
    n = len(records)
    lat = sorted(r["latency_ms"] for r in records)
    by_diff = {}
    for d in ("easy", "medium", "hard"):
        rs = [r for r in records if r["difficulty"] == d]
        if rs:
            by_diff[d] = {"n": len(rs), "accuracy": round(sum(r["correct"] for r in rs) / len(rs), 3)}
    return {
        "model": settings.llm_model, "questions": n,
        "execution_accuracy": round(sum(r["correct"] for r in records) / n, 3) if n else 0,
        "by_difficulty": by_diff,
        "valid_sql_rate": round(sum(r["status"] == "ok" for r in records) / n, 3) if n else 0,
        "solved_after_repair": sum(r["repaired"] and r["correct"] for r in records),
        "avg_tool_calls": round(statistics.mean(r["tool_calls"] for r in records), 2) if n else 0,
        "latency_p50_ms": lat[n // 2] if n else 0,
        "latency_p95_ms": lat[min(n - 1, int(n * 0.95))] if n else 0,
        "avg_tokens": round(statistics.mean(r["prompt_tokens"] + r["completion_tokens"] for r in records)) if n else 0,
        "total_cost_usd": round(sum(r["cost_usd"] for r in records), 4),
    }


def to_markdown(s: dict, records: list[dict]) -> str:
    lines = [f"# Eval report: {s['model']}", "",
             f"**Execution accuracy: {s['execution_accuracy']:.0%}** on {s['questions']} questions "
             f"(valid SQL {s['valid_sql_rate']:.0%}; {s['solved_after_repair']} solved only after self-repair).", "",
             "| Difficulty | Questions | Accuracy |", "| --- | --- | --- |"]
    lines += [f"| {d} | {v['n']} | {v['accuracy']:.0%} |" for d, v in s["by_difficulty"].items()]
    lines += ["", f"Latency p50 {s['latency_p50_ms']:.0f} ms, p95 {s['latency_p95_ms']:.0f} ms; "
              f"avg {s['avg_tokens']} tokens and {s['avg_tool_calls']} tool calls per question; "
              f"total cost ${s['total_cost_usd']}.", "", "## Failures", ""]
    fails = [r for r in records if not r["correct"]]
    lines += [f"- **{r['id']}** {r['question']}  \n  predicted: `{r['sql']}`  \n  error: {r['error']}" for r in fails] or ["None."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int)
    p.add_argument("--out", type=Path, default=HERE / "results")
    p.add_argument("--min-accuracy", type=float, default=0.0, help="exit 1 below this (for CI)")
    a = p.parse_args()
    result = run(a.limit, a.out)
    sys.exit(1 if result["execution_accuracy"] < a.min_accuracy else 0)
