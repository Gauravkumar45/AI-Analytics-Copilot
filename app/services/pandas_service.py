import pandas as pd


class PandasService:
    def analyze_dataframe(self, df: pd.DataFrame) -> dict[str, object]:
        if df.empty:
            return {"summary": "No data returned from query.", "rows": 0}

        summary = {
            "rows": int(len(df)),
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "preview": df.head(5).to_dict(orient="records"),
        }
        return summary
