import os
from datetime import datetime

import pandas as pd
from fpdf import FPDF

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "reports")
if not os.path.isdir(REPORT_DIR):
    os.makedirs(REPORT_DIR, exist_ok=True)


class ReportService:
    def generate_insight(self, analysis: dict[str, object]) -> str:
        if analysis.get("rows") == 0:
            return "No meaningful insight could be generated because the query returned no data."

        preview = analysis.get("preview", [])
        columns = analysis.get("columns", [])
        rows = analysis.get("rows", 0)
        return (
            f"The query returned {rows} rows and {len(columns)} columns. "
            f"Columns include: {', '.join(columns)}. "
            f"Here is a preview of the first records: {preview[:3]}"
        )

    def generate_pdf_report(self, df: pd.DataFrame, insight: str) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"report_{timestamp}.pdf"
        filepath = os.path.abspath(os.path.join(REPORT_DIR, filename))

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)
        pdf.cell(0, 10, "AI Analytics Copilot Report", ln=True)
        pdf.ln(5)
        pdf.multi_cell(0, 8, insight)
        pdf.ln(5)

        if not df.empty:
            pdf.cell(0, 8, "Data preview:", ln=True)
            preview = df.head(10)
            for _, row in preview.iterrows():
                pdf.multi_cell(0, 7, str(row.to_dict()))

        pdf.output(filepath)
        return f"/reports/{filename}"
