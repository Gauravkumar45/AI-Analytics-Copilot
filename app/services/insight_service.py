class InsightService:
    def summarize_insight(self, analysis: dict[str, object]) -> str:
        if analysis.get("rows") == 0:
            return "No results were produced by the query, so no insights are available."

        columns = analysis.get("columns", [])
        rows = analysis.get("rows", 0)
        return (
            f"The dataset contains {rows} rows and {len(columns)} columns. "
            f"Columns available: {', '.join(columns)}. "
            "Use the chart output to review the primary numeric trend."
        )
