from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.services.data_service import DataService, DataUploadError

router = APIRouter()


class UploadResponse(BaseModel):
    table_name: str
    rows: int
    columns: list[str]


@router.post("/upload", response_model=UploadResponse)
async def upload_csv(
    file: UploadFile = File(...), table_name: str | None = Form(None)
) -> UploadResponse:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV file")

    try:
        imported_table, rows, columns = DataService().import_csv(
            file.filename, await file.read(), table_name
        )
    except DataUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return UploadResponse(table_name=imported_table, rows=rows, columns=columns)
