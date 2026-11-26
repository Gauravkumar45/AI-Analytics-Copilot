from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router

BASE_DIR = Path(__file__).resolve().parents[1]
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="AI Analytics Copilot",
    description="Agentic Text-to-SQL analytics API: LangGraph agent, tool calling, SQL safety guard, Plotly charts and PDF reports.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
app.mount("/reports", StaticFiles(directory=REPORT_DIR), name="reports")


@app.get("/api/health", tags=["health"])
def health() -> dict:
    from app.core.settings import get_settings
    from app.sql.executor import Database

    settings = get_settings()
    return {"status": "ok", "model": settings.llm_model, "database": settings.sql_dialect,
            "tables": Database(settings).list_tables(), "llm_key_configured": bool(settings.llm_api_key)}
