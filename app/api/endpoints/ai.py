from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from app.services.ai_service import AiService, OllamaModelError
from app.services.sql_service import SqlService
from app.services.pandas_service import PandasService
from app.services.chart_service import ChartService
from app.services.report_service import ReportService
from app.services.insight_service import InsightService
from app.dependencies import get_ai_service, get_sql_service, get_pandas_service, get_chart_service, get_report_service, get_insight_service

router = APIRouter()


class AskRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "question": "Summarize the dataset by year",
                "table_name": "annual_enterprise_survey_2025",
                "time_range": None,
            }
        }
    )

    question: str
    table_name: str | None = None
    time_range: str | None = None

    @field_validator("time_range")
    @classmethod
    def remove_openapi_placeholder(cls, value: str | None) -> str | None:
        return None if value and value.strip().lower() == "string" else value


class AskResponse(BaseModel):
    sql: str
    chart_html: str | None = None
    insight: str | None = None
    report_url: str | None = None


@router.post("/ask", response_model=AskResponse)
def ask_question(
    request: AskRequest,
    ai_service: AiService = Depends(get_ai_service),
    sql_service: SqlService = Depends(get_sql_service),
    pandas_service: PandasService = Depends(get_pandas_service),
    chart_service: ChartService = Depends(get_chart_service),
    insight_service: InsightService = Depends(get_insight_service),
    report_service: ReportService = Depends(get_report_service),
) -> AskResponse:
    try:
        schema = sql_service.describe_schema(request.table_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    prompt = ai_service.build_sql_prompt(request.question, request.table_name, request.time_range, schema)
    try:
        sql_query = ai_service.generate_sql(prompt)
    except OllamaModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        df = sql_service.execute_sql(sql_query)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    analysis = pandas_service.analyze_dataframe(df)
    chart_html = chart_service.build_chart_html(df)
    insight = insight_service.summarize_insight(analysis)
    report_url = report_service.generate_pdf_report(df, insight)

    return AskResponse(sql=sql_query, chart_html=chart_html, insight=insight, report_url=report_url)
