"""Agent, SQL guard and executor tests. No API key or network needed: a
scripted fake model plays the LLM and the demo SQLite DB is the database."""
import os
import tempfile
import unittest
from pathlib import Path

from app.agent.graph import CopilotAgent, run_agent, run_sequential
from app.core.settings import Settings
from app.llm.client import LLMError, LLMResponse, ToolCall
from app.sql.executor import Database, QueryError
from app.sql.guard import UnsafeSQLError, check_sql
from scripts.seed_demo_db import build

TMP = Path(tempfile.mkdtemp())
DB_PATH = build(TMP / "sales.db")


def settings(**overrides) -> Settings:
    os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
    return Settings(**overrides)


class FakeLLM:
    """Returns scripted responses in order; records the messages it saw."""
    model = "fake"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools=None, temperature=0.0, max_tokens=700):
        self.seen.append(messages)
        if not self.responses:
            return LLMResponse(content="Summary.", prompt_tokens=10, completion_tokens=5)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def submit(sql, chart="bar", x=None, y=None, call_id="c1"):
    return LLMResponse(content=None, prompt_tokens=100, completion_tokens=20, tool_calls=[
        ToolCall(call_id, "submit_sql", {"sql": sql, "chart_type": chart, "x": x, "y": y})])


class GuardTests(unittest.TestCase):
    def test_accepts_select_and_wraps_limit(self):
        r = check_sql("SELECT city, COUNT(*) AS n FROM customers GROUP BY city;", max_rows=50)
        self.assertEqual(r.tables, ["customers"])
        self.assertTrue(r.limited_sql.rstrip().endswith("LIMIT 51"))

    def test_rejects_writes_and_multiple_statements(self):
        for bad in ["DELETE FROM orders", "SELECT 1; DROP TABLE orders",
                    "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
                    "SELECT * INTO backup FROM orders", "SELECT * FROM orders FOR UPDATE",
                    "SELECT pg_sleep(10)", "SELECT * FROM sqlite_master", "SELECT * FROM pg_catalog.pg_user"]:
            with self.subTest(bad=bad), self.assertRaises(UnsafeSQLError):
                check_sql(bad)

    def test_keywords_inside_strings_and_comments_are_ignored(self):
        r = check_sql("SELECT 'drop; delete' AS note, REPLACE(name,'a','b') AS n FROM customers -- update")
        self.assertEqual(r.tables, ["customers"])

    def test_unknown_and_disallowed_tables(self):
        with self.assertRaisesRegex(UnsafeSQLError, "Unknown table"):
            check_sql("SELECT * FROM invoices", known_tables={"orders"})
        with self.assertRaisesRegex(UnsafeSQLError, "not allowed"):
            check_sql("SELECT * FROM customers", allowed_tables=("orders",))

    def test_cte_names_are_not_treated_as_tables(self):
        r = check_sql("WITH monthly AS (SELECT * FROM orders) SELECT * FROM monthly", known_tables={"orders"})
        self.assertEqual(r.tables, ["orders"])


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(settings(max_rows=10))

    def test_row_cap_and_truncation_flag(self):
        r = self.db.execute(check_sql("SELECT * FROM orders", max_rows=10).limited_sql)
        self.assertEqual(len(r.df), 10)
        self.assertTrue(r.truncated)

    def test_connection_is_read_only(self):
        with self.assertRaises(QueryError):
            self.db.execute("DELETE FROM orders")  # bypassing the guard still fails

    def test_schema_and_profile(self):
        self.assertEqual(self.db.list_tables(), ["customers", "order_items", "orders", "products"])
        prof = self.db.profile("orders")
        self.assertEqual(prof["row_count"], 3000)
        self.assertIn("delivered", next(c for c in prof["columns"] if c["name"] == "status")["sample_values"])


class AgentTests(unittest.TestCase):
    def run_agent(self, llm, **s):
        agent = CopilotAgent(llm, settings=settings(**s))
        return run_sequential(agent, {"question": "q", "steps": []}), llm

    def test_happy_path(self):
        state, _ = self.run_agent(FakeLLM(
            submit("SELECT city, COUNT(*) AS customers FROM customers GROUP BY city ORDER BY 2 DESC",
                   x="city", y="customers"),
            LLMResponse(content="Gurugram leads.", prompt_tokens=50, completion_tokens=10)))
        self.assertEqual(state["status"], "ok")
        self.assertEqual(state["analysis"]["rows"], 10)
        self.assertEqual(state["chart"], {"type": "bar", "x": "city", "y": "customers"})
        self.assertEqual(state["answer"], "Gurugram leads.")
        self.assertEqual(state["usage"]["llm_calls"], 2)
        self.assertGreater(state["usage"]["cost_usd"], 0)

    def test_tool_call_then_submit(self):
        llm = FakeLLM(
            LLMResponse(content=None, tool_calls=[ToolCall("t1", "get_distinct_values",
                                                           {"table": "orders", "column": "status"})]),
            submit("SELECT COUNT(*) AS n FROM orders WHERE status = 'returned'", chart="table"))
        state, llm = self.run_agent(llm)
        self.assertEqual(state["status"], "ok")
        tool_msg = llm.seen[1][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertIn("returned", tool_msg["content"])
        self.assertEqual([s["node"] for s in state["steps"]][:4],
                         ["load_schema", "generate_sql", "run_tools", "generate_sql"])

    def test_repair_loop_after_bad_column(self):
        llm = FakeLLM(submit("SELECT revenue FROM orders"),
                      submit("SELECT COUNT(*) AS n FROM orders", call_id="c2", chart="table"))
        state, llm = self.run_agent(llm)
        self.assertEqual(state["status"], "ok")
        self.assertEqual(state["attempts"], 1)
        feedback = llm.seen[1][-1]
        self.assertEqual(feedback["tool_call_id"], "c1")
        self.assertIn("ERROR", feedback["content"])

    def test_gives_up_after_max_attempts(self):
        state, _ = self.run_agent(FakeLLM(*[submit("DROP TABLE orders", call_id=f"c{i}") for i in range(3)]),
                                  max_sql_attempts=3)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["attempts"], 3)
        self.assertIn("couldn't", state["answer"])

    def test_llm_outage_fails_cleanly(self):
        state, _ = self.run_agent(FakeLLM(LLMError("down")))
        self.assertEqual(state["status"], "failed")

    def test_run_agent_falls_back_without_langgraph(self):
        final = run_agent("q", FakeLLM(submit("SELECT COUNT(*) AS n FROM customers", chart="table")),
                          settings=settings(), use_langgraph=False)
        self.assertEqual(final["status"], "ok")
        self.assertIn("total_ms", final)


if __name__ == "__main__":
    unittest.main()
