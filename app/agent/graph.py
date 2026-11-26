"""LangGraph agent: question -> schema -> SQL (with tool calls) -> guard ->
execute -> analyze -> answer, with a self-repair loop on validation or
database errors.

    load_schema -> generate_sql <-> run_tools
                        |
                   validate_sql --error--> generate_sql (repair, max N attempts)
                        |
                   execute_sql  --error--> generate_sql
                        |
                     analyze -> summarize -> END        (fail -> END after N attempts)

Node methods are plain functions over a dict state, so the same logic runs
under LangGraph in production and under a small sequential runner in tests.
"""
from __future__ import annotations

import json
import time
from typing import Any, TypedDict

import pandas as pd

from app.agent.prompts import (POSTGRES_DATE_HINT, SQL_SYSTEM_PROMPT, SQLITE_DATE_HINT,
                               SUMMARY_SYSTEM_PROMPT)
from app.agent.tools import CHART_TYPES, TOOL_SCHEMAS, run_tool
from app.core.settings import Settings, get_settings
from app.llm.client import ChatModel, LLMError, LLMResponse, ToolCallRejected, estimate_cost
from app.sql.executor import Database, QueryError
from app.sql.guard import UnsafeSQLError, check_sql, extract_sql


class AgentState(TypedDict, total=False):
    question: str
    table_hint: str | None
    schema: str
    tables: list[str]
    messages: list[dict]
    pending_tool_calls: list[dict]
    submit_call_id: str | None
    sql: str
    limited_sql: str
    chart: dict
    explanation: str
    attempts: int
    tool_calls_used: int
    error: str | None
    errors: list[str]
    df: Any
    truncated: bool
    analysis: dict
    answer: str
    status: str                      # "ok" | "failed"
    steps: list[dict]                # trace: node, ms, detail
    usage: dict                      # llm_calls, prompt_tokens, completion_tokens, cost_usd


class CopilotAgent:
    def __init__(self, llm: ChatModel, db: Database | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.llm = llm
        self.db = db or Database(self.settings)

    # ---- helpers -----------------------------------------------------------
    def _trace(self, state: AgentState, node: str, started: float, **detail) -> list[dict]:
        return [*state.get("steps", []), {"node": node, "ms": round((time.perf_counter() - started) * 1000, 1), **detail}]

    def _usage(self, state: AgentState, resp: LLMResponse) -> dict:
        u = dict(state.get("usage") or {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0})
        u["llm_calls"] += 1
        u["prompt_tokens"] += resp.prompt_tokens
        u["completion_tokens"] += resp.completion_tokens
        u["cost_usd"] = round(u["cost_usd"] + estimate_cost(resp.prompt_tokens, resp.completion_tokens, self.settings), 6)
        return u

    def _feedback(self, state: AgentState, error: str) -> list[dict]:
        """Send the error back to the model so it can repair its query."""
        msgs = list(state["messages"])
        text = f"ERROR: {error}\nFix the query and call submit_sql again."
        if state.get("submit_call_id"):
            msgs.append({"role": "tool", "tool_call_id": state["submit_call_id"], "content": text})
        else:
            msgs.append({"role": "user", "content": text})
        return msgs

    # ---- nodes ---------------------------------------------------------------
    def load_schema(self, state: AgentState) -> dict:
        t = time.perf_counter()
        tables = self.db.list_tables()
        schema = self.db.schema_text()
        hint = SQLITE_DATE_HINT if self.settings.is_sqlite else POSTGRES_DATE_HINT
        system = SQL_SYSTEM_PROMPT.format(dialect=self.settings.sql_dialect, schema=schema, date_hint=hint)
        user = state["question"]
        if state.get("table_hint"):
            user += f"\n(Use table: {state['table_hint']})"
        return {"schema": schema, "tables": tables, "attempts": 0, "tool_calls_used": 0, "errors": [],
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "steps": self._trace(state, "load_schema", t, tables=len(tables))}

    def generate_sql(self, state: AgentState) -> dict:
        t = time.perf_counter()
        try:
            resp = self.llm.chat(state["messages"], tools=TOOL_SCHEMAS)
        except ToolCallRejected as exc:
            # The provider rejected the model's tool call: treat it like a bad query and let it retry.
            attempts = state.get("attempts", 0) + 1
            msgs = [*state["messages"], {"role": "user", "content":
                    f"ERROR: {exc}\nCall submit_sql again with valid arguments (use null for x and y when chart_type is table)."}]
            return {"error": str(exc), "attempts": attempts, "errors": [*state.get("errors", []), str(exc)],
                    "messages": msgs, "pending_tool_calls": [],
                    "steps": self._trace(state, "generate_sql", t, error=str(exc), attempt=attempts)}
        except LLMError as exc:
            return {"error": str(exc), "status": "failed", "answer": f"The language model is unavailable: {exc}",
                    "steps": self._trace(state, "generate_sql", t, error=str(exc))}

        msgs = [*state["messages"], _assistant_message(resp)]
        update: dict = {"messages": msgs, "usage": self._usage(state, resp), "pending_tool_calls": [],
                        "error": None}
        submit = next((c for c in resp.tool_calls if c.name == "submit_sql"), None)
        lookups = [c for c in resp.tool_calls if c.name != "submit_sql"]

        if submit:
            args = submit.arguments
            update.update(sql=str(args.get("sql", "")), submit_call_id=submit.id,
                          explanation=str(args.get("explanation", "")),
                          chart={"type": args.get("chart_type", "table"), "x": args.get("x"), "y": args.get("y")})
            # Answer any lookups sent in the same turn so the transcript stays valid.
            if lookups:
                msgs.extend({"role": "tool", "tool_call_id": c.id, "content": '{"note":"ignored, SQL submitted"}'}
                            for c in lookups)
        elif lookups:
            update["pending_tool_calls"] = [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in lookups]
        else:  # model replied with plain text: try to read SQL from it
            update.update(sql=extract_sql(resp.content or ""), submit_call_id=None,
                          chart={"type": "table", "x": None, "y": None})
        update["steps"] = self._trace(state, "generate_sql", t, tool_calls=[c.name for c in resp.tool_calls],
                                      tokens=resp.prompt_tokens + resp.completion_tokens)
        return update

    def run_tools(self, state: AgentState) -> dict:
        t = time.perf_counter()
        msgs = list(state["messages"])
        used = state.get("tool_calls_used", 0)
        names = []
        for call in state.get("pending_tool_calls", []):
            used += 1
            names.append(call["name"])
            if used > self.settings.max_tool_calls:
                content = json.dumps({"error": "tool budget exhausted; call submit_sql now"})
            else:
                content = run_tool(self.db, call["name"], call["arguments"])
            msgs.append({"role": "tool", "tool_call_id": call["id"], "content": content[:6000]})
        return {"messages": msgs, "pending_tool_calls": [], "tool_calls_used": used,
                "steps": self._trace(state, "run_tools", t, tools=names)}

    def validate_sql(self, state: AgentState) -> dict:
        t = time.perf_counter()
        try:
            result = check_sql(state.get("sql", ""), known_tables=set(state["tables"]),
                               allowed_tables=self.settings.allowed_tables, max_rows=self.settings.max_rows)
        except UnsafeSQLError as exc:
            return self._attempt_failed(state, "validate_sql", t, str(exc))
        return {"sql": result.sql, "limited_sql": result.limited_sql, "error": None,
                "steps": self._trace(state, "validate_sql", t, tables=result.tables)}

    def execute_sql(self, state: AgentState) -> dict:
        t = time.perf_counter()
        try:
            result = self.db.execute(state["limited_sql"])
        except QueryError as exc:
            return self._attempt_failed(state, "execute_sql", t, str(exc))
        return {"df": result.df, "truncated": result.truncated, "error": None,
                "steps": self._trace(state, "execute_sql", t, rows=len(result.df), truncated=result.truncated)}

    def _attempt_failed(self, state: AgentState, node: str, t: float, error: str) -> dict:
        attempts = state.get("attempts", 0) + 1
        return {"error": error, "attempts": attempts, "errors": [*state.get("errors", []), error],
                "messages": self._feedback(state, error),
                "steps": self._trace(state, node, t, error=error, attempt=attempts)}

    def analyze(self, state: AgentState) -> dict:
        t = time.perf_counter()
        df: pd.DataFrame = state["df"]
        chart = _validate_chart(state.get("chart") or {}, df)
        analysis = {
            "rows": int(len(df)),
            "columns": [str(c) for c in df.columns],
            "preview": json.loads(df.head(20).to_json(orient="records", date_format="iso")),
            "numeric_summary": json.loads(df.describe().round(2).to_json()) if not df.empty and
            len(df.select_dtypes("number").columns) else {},
        }
        return {"analysis": analysis, "chart": chart, "steps": self._trace(state, "analyze", t, chart=chart["type"])}

    def summarize(self, state: AgentState) -> dict:
        t = time.perf_counter()
        a = state["analysis"]
        payload = {"question": state["question"], "rows": a["rows"], "columns": a["columns"],
                   "result_preview": a["preview"][:15], "truncated": state.get("truncated", False)}
        try:
            resp = self.llm.chat([{"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                                  {"role": "user", "content": json.dumps(payload, default=str)}], max_tokens=800)
            answer = (resp.content or "").strip() or _fallback_answer(a)
            usage = self._usage(state, resp)
        except LLMError:
            answer, usage = _fallback_answer(a), state.get("usage", {})
        return {"answer": answer, "usage": usage, "status": "ok", "steps": self._trace(state, "summarize", t)}

    def fail(self, state: AgentState) -> dict:
        errors = state.get("errors", [])
        return {"status": "failed", "answer": state.get("answer") or (
            "I couldn't build a valid query for that question after "
            f"{state.get('attempts', 0)} attempts. Last error: {errors[-1] if errors else 'unknown'}. "
            "Try rephrasing or naming the table.")}

    # ---- routing -------------------------------------------------------------
    def route_after_generate(self, state: AgentState) -> str:
        if state.get("status") == "failed":
            return "fail"
        if state.get("error"):  # rejected tool call: retry generation within the attempt budget
            return "generate_sql" if state.get("attempts", 0) < self.settings.max_sql_attempts else "fail"
        if state.get("pending_tool_calls"):
            return "run_tools"
        return "validate_sql"

    def _route_on_error(self, state: AgentState, ok: str) -> str:
        if not state.get("error"):
            return ok
        return "generate_sql" if state.get("attempts", 0) < self.settings.max_sql_attempts else "fail"

    def route_after_validate(self, state: AgentState) -> str:
        return self._route_on_error(state, "execute_sql")

    def route_after_execute(self, state: AgentState) -> str:
        return self._route_on_error(state, "analyze")


# ---- graph construction ------------------------------------------------------
NODES = ["load_schema", "generate_sql", "run_tools", "validate_sql", "execute_sql", "analyze", "summarize", "fail"]


def build_graph(agent: CopilotAgent):
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(AgentState)
    for name in NODES:
        g.add_node(name, getattr(agent, name))
    g.add_edge(START, "load_schema")
    g.add_edge("load_schema", "generate_sql")
    g.add_conditional_edges("generate_sql", agent.route_after_generate,
                            {"run_tools": "run_tools", "validate_sql": "validate_sql",
                             "generate_sql": "generate_sql", "fail": "fail"})
    g.add_edge("run_tools", "generate_sql")
    g.add_conditional_edges("validate_sql", agent.route_after_validate,
                            {"execute_sql": "execute_sql", "generate_sql": "generate_sql", "fail": "fail"})
    g.add_conditional_edges("execute_sql", agent.route_after_execute,
                            {"analyze": "analyze", "generate_sql": "generate_sql", "fail": "fail"})
    g.add_edge("analyze", "summarize")
    g.add_edge("summarize", END)
    g.add_edge("fail", END)
    return g.compile()


def run_sequential(agent: CopilotAgent, state: AgentState, max_steps: int = 60) -> AgentState:
    """Same nodes and routes as build_graph, without LangGraph (tests, minimal installs)."""
    routes = {"generate_sql": agent.route_after_generate, "validate_sql": agent.route_after_validate,
              "execute_sql": agent.route_after_execute}
    fixed = {"load_schema": "generate_sql", "run_tools": "generate_sql", "analyze": "summarize",
             "summarize": None, "fail": None}
    node: str | None = "load_schema"
    for _ in range(max_steps):
        if node is None:
            return state
        state.update(getattr(agent, node)(state))
        node = routes[node](state) if node in routes else fixed[node]
    state.update(agent.fail(state))
    return state


def run_agent(question: str, llm: ChatModel, db: Database | None = None, table_hint: str | None = None,
              settings: Settings | None = None, use_langgraph: bool = True) -> AgentState:
    agent = CopilotAgent(llm, db, settings)
    started = time.perf_counter()
    initial: AgentState = {"question": question, "table_hint": table_hint, "steps": []}
    if use_langgraph:
        try:
            graph = build_graph(agent)
        except ImportError:
            graph = None
        if graph is not None:
            final = graph.invoke(initial, config={"recursion_limit": 60})
            final["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
            return final
    final = run_sequential(agent, initial)
    final["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return final


# ---- small pure helpers -------------------------------------------------------
def _assistant_message(resp: LLMResponse) -> dict:
    msg: dict = {"role": "assistant", "content": resp.content or ""}
    if resp.tool_calls:
        msg["tool_calls"] = [{"id": c.id, "type": "function",
                              "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                             for c in resp.tool_calls]
    return msg


def _validate_chart(chart: dict, df: pd.DataFrame) -> dict:
    cols = [str(c) for c in df.columns]
    numeric = [str(c) for c in df.select_dtypes("number").columns]
    ctype = chart.get("type") if chart.get("type") in CHART_TYPES else "bar"
    x = chart.get("x") if chart.get("x") in cols else None
    y = chart.get("y") if chart.get("y") in numeric else None
    if df.empty or not numeric or len(df) == 1 and len(cols) == 1:
        return {"type": "table", "x": None, "y": None}
    y = y or numeric[0]
    x = x or next((c for c in cols if c != y), None)
    if x is None:
        return {"type": "table", "x": None, "y": None}
    if ctype == "pie" and len(df) > 12:
        ctype = "bar"
    return {"type": ctype, "x": x, "y": y}


def _fallback_answer(a: dict) -> str:
    if a["rows"] == 0:
        return "No matching data was found."
    if a["rows"] == 1 and a["preview"]:
        parts = ", ".join(f"{k}: {v}" for k, v in a["preview"][0].items())
        return f"Result: {parts}."
    return f"The query returned {a['rows']} rows with columns {', '.join(a['columns'])}."
