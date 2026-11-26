from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.graph import run_agent
from app.core.rate_limit import rate_limit
from app.dependencies import get_chart_service, get_llm, get_report_service
from app.llm.client import ChatModel
from app.services.chart_service import ChartService
from app.services.report_service import ReportService

router = APIRouter()


class AskRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "question": "What is the total delivered revenue by product category?", "table_name": None}})

    question: str = Field(min_length=3, max_length=500)
    table_name: str | None = None
    time_range: str | None = None

    @field_validator("time_range", "table_name")
    @classmethod
    def remove_openapi_placeholder(cls, value: str | None) -> str | None:
        return None if value and value.strip().lower() == "string" else value


class Step(BaseModel):
    node: str
    ms: float
    detail: dict = {}


class AskResponse(BaseModel):
    status: str
    answer: str
    sql: str | None = None
    explanation: str | None = None
    columns: list[str] = []
    rows: list[dict] = []
    row_count: int = 0
    truncated: bool = False
    chart_html: str | None = None
    insight: str | None = None          # kept for older clients; same as answer
    report_url: str | None = None
    attempts: int = 0
    steps: list[Step] = []
    usage: dict = {}
    total_ms: float = 0


@router.post("/ask", response_model=AskResponse, dependencies=[Depends(rate_limit)])
def ask_question(
    request: AskRequest,
    llm: ChatModel = Depends(get_llm),
    chart_service: ChartService = Depends(get_chart_service),
    report_service: ReportService = Depends(get_report_service),
) -> AskResponse:
    question = request.question
    if request.time_range:
        question += f" (time range: {request.time_range})"
    state = run_agent(question, llm, table_hint=request.table_name)

    steps = [Step(node=s["node"], ms=s["ms"], detail={k: v for k, v in s.items() if k not in ("node", "ms")})
             for s in state.get("steps", [])]
    base = dict(status=state.get("status", "failed"), answer=state.get("answer", ""), sql=state.get("sql"),
                explanation=state.get("explanation"), attempts=state.get("attempts", 0), steps=steps,
                usage=state.get("usage", {}), total_ms=state.get("total_ms", 0))
    if state.get("status") != "ok":
        if not state.get("sql") and "unavailable" in base["answer"]:
            raise HTTPException(status_code=503, detail=base["answer"])
        return AskResponse(**base)

    df = state["df"]
    analysis = state["analysis"]
    return AskResponse(
        **base,
        columns=analysis["columns"], rows=analysis["preview"], row_count=analysis["rows"],
        truncated=state.get("truncated", False), insight=state["answer"],
        chart_html=chart_service.build_from_spec(df, state["chart"]),
        report_url=report_service.generate_pdf_report(df, state["answer"]),
    )
