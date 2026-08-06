import json
import os
import requests
import streamlit as st

BACKEND_URL = st.secrets.get("backend_url", "http://backend:8002")
BACKEND_PUBLIC_URL = st.secrets.get("backend_public_url", os.environ.get("BACKEND_PUBLIC_URL", "http://localhost:8002"))

st.set_page_config(page_title="AI Analytics Copilot", page_icon="🤖", layout="wide")

st.title("AI Analytics Copilot")
st.markdown("Enter a business question and let the system generate SQL, execute it, analyze the results, and render visuals.")

if "target_table" not in st.session_state:
    st.session_state.target_table = ""

uploaded_file = st.file_uploader("Upload a CSV dataset", type=["csv"])
if uploaded_file and st.button("Import CSV into analytics database"):
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/data/upload",
            files={"file": (uploaded_file.name, uploaded_file.getvalue(), "text/csv")},
            timeout=120,
        )
        response.raise_for_status()
        upload_result = response.json()
        st.session_state.target_table = upload_result["table_name"]
        st.success(
            f"Imported {upload_result['rows']} rows into '{upload_result['table_name']}' "
            f"with columns: {', '.join(upload_result['columns'])}"
        )
    except requests.RequestException as exc:
        detail = ""
        if exc.response is not None:
            try:
                detail = exc.response.json().get("detail", "")
            except ValueError:
                pass
        st.error(detail or f"Upload failed: {exc}")

with st.form("ask_form"):
    question = st.text_area("Ask a question", height=120)
    table_name = st.text_input("Target table name (optional)", key="target_table")
    time_range = st.text_input("Time range filter (optional)")
    submitted = st.form_submit_button("Run Analytics")

if submitted and question:
    payload = {
        "question": question,
        "table_name": table_name or None,
        "time_range": time_range or None,
    }
    try:
        response = requests.post(f"{BACKEND_URL}/api/ai/ask", json=payload, timeout=240)
        response.raise_for_status()
        result = response.json()

        st.subheader("Generated SQL")
        st.code(result.get("sql", ""), language="sql")

        if result.get("chart_html"):
            st.subheader("Chart")
            st.components.v1.html(result["chart_html"], height=500)

        st.subheader("Business Insight")
        st.write(result.get("insight", "No insight generated."))

        if result.get("report_url"):
            report_url = result["report_url"]
            if report_url.startswith("/"):
                report_url = f"{BACKEND_PUBLIC_URL.rstrip('/')}{report_url}"
            st.markdown(f"[Download PDF report]({report_url})")
    except requests.RequestException as exc:
        detail = ""
        if exc.response is not None:
            try:
                detail = exc.response.json().get("detail", "")
            except ValueError:
                pass
        st.error(detail or f"Backend request failed: {exc}")
