#!/usr/bin/env sh
# Runs the FastAPI backend (internal port 8002) and the Streamlit UI (port 7860).
set -e
[ -f demo/sales.db ] || python scripts/seed_demo_db.py
uvicorn app.main:app --host 127.0.0.1 --port 8002 &
exec streamlit run frontend/streamlit_app.py \
  --server.port "${PORT:-7860}" --server.address 0.0.0.0 --server.headless true \
  --server.enableCORS false --server.enableXsrfProtection false --browser.gatherUsageStats false
