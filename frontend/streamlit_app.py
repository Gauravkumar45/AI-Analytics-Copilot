import os

import pandas as pd
import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8002").rstrip("/")

EXAMPLES = [
    "What is the total delivered revenue by product category?",
    "How many orders were placed each month in 2024?",
    "Who are the top 5 customers by delivered revenue?",
    "What is the cancellation rate by sales channel, as a percentage?",
    "What was the year-over-year growth in delivered revenue from 2024 to 2025?",
]

st.set_page_config(page_title="AI Analytics Copilot", page_icon="📊", layout="wide")


def api_error(exc: requests.RequestException) -> str:
    if exc.response is not None:
        try:
            return exc.response.json().get("detail", str(exc))
        except ValueError:
            pass
    return str(exc)


@st.cache_data(ttl=60)
def health() -> dict:
    try:
        return requests.get(f"{BACKEND_URL}/api/health", timeout=10).json()
    except requests.RequestException:
        return {}


info = health()

with st.sidebar:
    st.header("About")
    st.markdown(
        "A **LangGraph agent** turns questions into SQL. It inspects the data with tools, "
        "validates every query through a read-only SQL guard, repairs its own errors, then "
        "charts and explains the result."
    )
    if info:
        st.caption(f"Model: `{info.get('model')}` · Database: {info.get('database')}")
        st.markdown("**Tables:** " + ", ".join(f"`{t}`" for t in info.get("tables", [])))
        if not info.get("llm_key_configured"):
            st.warning("No LLM API key configured on the server.")
    else:
        st.error("Backend is not reachable.")
    st.divider()
    st.subheader("Bring your own data")
    uploaded = st.file_uploader("Upload a CSV (max 10 MB)", type=["csv"])
    if uploaded and st.button("Import CSV"):
        try:
            r = requests.post(f"{BACKEND_URL}/api/data/upload",
                              files={"file": (uploaded.name, uploaded.getvalue(), "text/csv")}, timeout=120)
            r.raise_for_status()
            res = r.json()
            st.success(f"Imported {res['rows']} rows into `{res['table_name']}`")
            health.clear()
        except requests.RequestException as exc:
            st.error(api_error(exc))

st.title("📊 AI Analytics Copilot")
st.caption("Ask a business question about the demo retail dataset (2024–2025 orders, customers and products).")

cols = st.columns(len(EXAMPLES))
for col, example in zip(cols, EXAMPLES):
    if col.button(example, use_container_width=True):
        st.session_state.question = example

question = st.text_area("Your question", key="question", height=80,
                        placeholder="e.g. Which city generated the most delivered revenue?")
run = st.button("Ask", type="primary")

if run and question.strip():
    with st.spinner("Agent is planning, querying and validating..."):
        try:
            r = requests.post(f"{BACKEND_URL}/api/ai/ask", json={"question": question}, timeout=180)
            r.raise_for_status()
            st.session_state.result = r.json()
        except requests.RequestException as exc:
            st.session_state.result = None
            st.error(api_error(exc))

result = st.session_state.get("result")
if result:
    if result["status"] != "ok":
        st.error(result["answer"])
    else:
        st.subheader("Answer")
        st.write(result["answer"])

    usage = result.get("usage", {})
    m = st.columns(5)
    m[0].metric("Latency", f"{result.get('total_ms', 0) / 1000:.1f} s")
    m[1].metric("LLM calls", usage.get("llm_calls", 0))
    m[2].metric("Tokens", usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
    m[3].metric("Est. cost", f"${usage.get('cost_usd', 0):.4f}")
    m[4].metric("Repairs", result.get("attempts", 0))

    if result.get("chart_html"):
        st.components.v1.html(result["chart_html"], height=440)
    if result.get("rows"):
        st.markdown(f"**Result** ({result['row_count']} rows{', truncated' if result.get('truncated') else ''})")
        st.dataframe(pd.DataFrame(result["rows"]), use_container_width=True, hide_index=True)

    with st.expander("Generated SQL", expanded=result["status"] != "ok"):
        if result.get("explanation"):
            st.caption(result["explanation"])
        st.code(result.get("sql") or "-", language="sql")

    with st.expander("Agent trace"):
        st.dataframe(pd.DataFrame([{"step": s["node"], "ms": s["ms"],
                                    "detail": ", ".join(f"{k}={v}" for k, v in s.get("detail", {}).items())}
                                   for s in result.get("steps", [])]),
                     use_container_width=True, hide_index=True)

    if result.get("report_url"):
        try:
            pdf = requests.get(f"{BACKEND_URL}{result['report_url']}", timeout=30)
            pdf.raise_for_status()
            st.download_button("Download PDF report", pdf.content, file_name="analytics_report.pdf",
                               mime="application/pdf")
        except requests.RequestException:
            pass
