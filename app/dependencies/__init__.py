from app.services.ai_service import AiService
from app.services.auth_service import AuthService
from app.services.chart_service import ChartService
from app.services.data_service import DataService
from app.services.insight_service import InsightService
from app.services.pandas_service import PandasService
from app.services.report_service import ReportService
from app.services.sql_service import SqlService


def get_ai_service() -> AiService:
    return AiService()


def get_auth_service() -> AuthService:
    return AuthService()


def get_sql_service() -> SqlService:
    return SqlService()


def get_pandas_service() -> PandasService:
    return PandasService()


def get_chart_service() -> ChartService:
    return ChartService()


def get_data_service() -> DataService:
    return DataService()


def get_insight_service() -> InsightService:
    return InsightService()


def get_report_service() -> ReportService:
    return ReportService()
