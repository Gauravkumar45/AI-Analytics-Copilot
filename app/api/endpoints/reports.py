from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ReportRequest(BaseModel):
    report_name: str


class ReportResponse(BaseModel):
    url: str


@router.post("/generate", response_model=ReportResponse)
def generate_report(request: ReportRequest) -> ReportResponse:
    # This endpoint can be extended to generate custom reports on demand.
    return ReportResponse(url=f"/reports/{request.report_name}.pdf")
