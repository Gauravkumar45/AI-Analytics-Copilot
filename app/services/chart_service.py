import json

import plotly.express as px


class ChartService:
    def build_chart_html(self, df) -> str | None:
        if df.empty:
            return None

        numeric_columns = [col for col in df.columns if df[col].dtype.kind in "biufc"]
        if not numeric_columns:
            return None

        x_axis = df.columns[0]
        y_axis = numeric_columns[0]
        fig = px.bar(df, x=x_axis, y=y_axis, title="AI Analytics Chart")
        return fig.to_html(full_html=False, include_plotlyjs="cdn")
