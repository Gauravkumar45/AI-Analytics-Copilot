from functools import lru_cache

from app.llm.client import OpenAICompatibleClient
from app.services.auth_service import AuthService
from app.services.chart_service import ChartService
from app.services.data_service import DataService
from app.services.report_service import ReportService


@lru_cache
def get_llm() -> OpenAICompatibleClient:
    return OpenAICompatibleClient()


def get_auth_service() -> AuthService:
    return AuthService()


def get_chart_service() -> ChartService:
    return ChartService()


def get_data_service() -> DataService:
    return DataService()


def get_report_service() -> ReportService:
    return ReportService()
