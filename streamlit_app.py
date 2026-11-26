"""Single-process Streamlit app for Streamlit Community Cloud (free).

Runs the LangGraph agent in-process, so no separate API server is needed.
Set GROQ_API_KEY in the app's Secrets. The FastAPI backend (app/main.py) and
frontend/streamlit_app.py remain for Docker deployments.
"""
from __future__ import annotations

import os
import time

import pandas as pd
import streamlit as st

st.set_page_config(page_title="AI Analytics Copilot", page_icon="📊", layout="wide")

# Community Cloud secrets -> environment, before settings are read.
try:
    for key in ("GROQ_API_KEY", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "LLM_REASONING_EFFORT"):
        if key in st.secrets and not os.environ.get(key):
            os.environ[key] = str(st.secrets[key])
except Exception:  # no secrets file locally
    pass

from app.agent.graph import run_agent  # noqa: E402
from app.core.settings import DEMO_DB_PATH, get_settings  # noqa: E402
from app.llm.client import OpenAICompatibleClient  # noqa: E402
from app.services.chart_service import ChartService  # noqa: E402
from app.sql.executor import Database  # noqa: E402

EXAMPLES = [
    "What is the total delivered revenue by product category?",
    "How many orders were placed each month in 2024?",
    "Who are the top 5 customers by delivered revenue? Show customer id and revenue.",
    "What is the cancellation rate by sales channel, as a percentage?",
    "What was the year-over-year growth in delivered revenue from 2024 to 2025, as a percentage?",
]
SESSION_LIMIT_PER_MINUTE = 5


@st.cache_resource
def resources():
    if not DEMO_DB_PATH.exists():
        from scripts.seed_demo_db import build
        build(DEMO_DB_PATH)
    settings = get_settings()
    return settings, OpenAICompatibleClient(settings), ChartService(), Database(settings).schema_text()


settings, llm, charts, schema = resources()


def rate_limited() -> bool:
    now = time.monotonic()
    hits = [t for t in st.session_state.get("hits", []) if now - t < 60]
    limited = len(hits) >= SESSION_LIMIT_PER_MINUTE
    if not limited:
        hits.append(now)
    st.session_state.hits = hits
    return limited


with st.sidebar:
    st.header("How it works")
    st.markdown(
        "1. A **LangGraph agent** reads the schema.\n"
        "2. It may call tools (`profile_table`, `get_distinct_values`) to check real values.\n"
        "3. It submits SQL, which passes a **read-only SQL guard** (single SELECT, no writes, "
        "allowlisted tables, row cap, timeout).\n"
        "4. Errors go back to the model, which **repairs** its query.\n"
        "5. The result is charted and explained."
    )
    st.caption(f"Model: `{settings.llm_model}` · 40/40 on the project's execution-accuracy eval")
    with st.expander("Database schema"):
        st.code(schema, language="sql")
    st.markdown("[Source code on GitHub](https://github.com/Gauravkumar45/AI-Analytics-Copilot)")

st.title("📊 AI Analytics Copilot")
st.caption("Ask a business question about a demo retail dataset: 2024–2025 orders, customers and products.")

cols = st.columns(len(EXAMPLES))
for col, example in zip(cols, EXAMPLES):
    if col.button(example, use_container_width=True):
        st.session_state.question = example

question = st.text_area("Your question", key="question", height=80,
                        placeholder="e.g. Which city generated the most delivered revenue?")

if st.button("Ask", type="primary") and question.strip():
    if not settings.llm_api_key:
        st.error("No LLM key configured. Add GROQ_API_KEY in the app's Secrets.")
    elif rate_limited():
        st.warning(f"Limit: {SESSION_LIMIT_PER_MINUTE} questions per minute. Please wait a moment.")
    else:
        with st.spinner("Agent is planning, querying and validating..."):
            st.session_state.result = run_agent(question.strip()[:500], llm)

state = st.session_state.get("result")
if state:
    usage = state.get("usage", {})
    if state.get("status") != "ok":
        st.error(state.get("answer", "Something went wrong."))
    else:
        st.subheader("Answer")
        st.write(state["answer"])

    m = st.columns(5)
    m[0].metric("Latency", f"{state.get('total_ms', 0) / 1000:.1f} s")
    m[1].metric("LLM calls", usage.get("llm_calls", 0))
    m[2].metric("Tokens", usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
    m[3].metric("Est. cost", f"${usage.get('cost_usd', 0):.4f}")
    m[4].metric("Self-repairs", state.get("attempts", 0))

    if state.get("status") == "ok":
        df: pd.DataFrame = state["df"]
        fig = charts.build_figure(df, state.get("chart") or {})
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)
        note = ", truncated" if state.get("truncated") else ""
        st.markdown(f"**Result** ({len(df)} rows{note})")
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button("Download CSV", df.to_csv(index=False).encode(), "result.csv", "text/csv")

    with st.expander("Generated SQL", expanded=state.get("status") != "ok"):
        if state.get("explanation"):
            st.caption(state["explanation"])
        st.code(state.get("sql") or "-", language="sql")

    with st.expander("Agent trace"):
        st.dataframe(pd.DataFrame([
            {"step": s["node"], "ms": s["ms"],
             "detail": ", ".join(f"{k}={v}" for k, v in s.items() if k not in ("node", "ms"))}
            for s in state.get("steps", [])]), use_container_width=True, hide_index=True)
