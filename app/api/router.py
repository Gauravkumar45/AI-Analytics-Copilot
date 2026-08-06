from fastapi import APIRouter

from app.api.endpoints import ai, auth, data, reports

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(data.router, prefix="/data", tags=["data"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
