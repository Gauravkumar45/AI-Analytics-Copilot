import re
from io import BytesIO

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class DataUploadError(ValueError):
    """Raised when an uploaded dataset cannot be safely imported."""


class DataService:
    def __init__(self) -> None:
        from app.services.sql_service import SqlService

        self.sql_service = SqlService()

    @staticmethod
    def normalize_identifier(value: str, fallback: str) -> str:
        normalized = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
        if not normalized:
            normalized = fallback
        if normalized[0].isdigit():
            normalized = f"_{normalized}"
        return normalized[:63]

    @staticmethod
    def coerce_numeric_columns(dataframe):
        """Convert mostly numeric CSV columns while keeping non-numeric suppression markers empty."""
        import pandas as pd

        for column in dataframe.columns:
            if not pd.api.types.is_object_dtype(dataframe[column]):
                continue
            original = dataframe[column].astype("string").str.strip()
            numeric = pd.to_numeric(original.str.replace(",", "", regex=False), errors="coerce")
            populated = original.notna() & original.ne("")
            if populated.any() and numeric[populated].notna().mean() >= 0.9:
                dataframe[column] = numeric
        return dataframe

    def import_csv(self, filename: str, content: bytes, table_name: str | None = None) -> tuple[str, int, list[str]]:
        import pandas as pd
        from sqlalchemy.exc import SQLAlchemyError

        if not content:
            raise DataUploadError("The uploaded CSV is empty")
        if len(content) > MAX_UPLOAD_BYTES:
            raise DataUploadError("CSV uploads are limited to 10 MB")

        try:
            dataframe = pd.read_csv(BytesIO(content))
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            raise DataUploadError("Upload a valid UTF-8 CSV file with a header row") from exc

        if dataframe.empty:
            raise DataUploadError("The CSV contains no data rows")
        if dataframe.columns.empty:
            raise DataUploadError("The CSV must include a header row")

        # Spreadsheet exports commonly include trailing unnamed columns that contain no data.
        dataframe = dataframe.dropna(axis=1, how="all")
        if dataframe.columns.empty:
            raise DataUploadError("The CSV does not contain any populated columns")

        dataframe = self.coerce_numeric_columns(dataframe)

        source_name = table_name or filename.rsplit(".", 1)[0]
        target_table = self.normalize_identifier(source_name, "uploaded_data")

        seen_columns: set[str] = set()
        columns: list[str] = []
        for index, column in enumerate(dataframe.columns):
            normalized = self.normalize_identifier(str(column), f"column_{index + 1}")
            base = normalized
            suffix = 2
            while normalized in seen_columns:
                normalized = f"{base[:60]}_{suffix}"
                suffix += 1
            seen_columns.add(normalized)
            columns.append(normalized)
        dataframe.columns = columns

        try:
            dataframe.to_sql(
                target_table,
                self.sql_service.engine,
                schema="public",
                if_exists="replace",
                index=False,
                method="multi",
                chunksize=1_000,
            )
        except SQLAlchemyError as exc:
            raise DataUploadError("The CSV could not be imported into the analytics database") from exc
        return target_table, len(dataframe), columns
