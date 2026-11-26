import plotly.express as px


class ChartService:
    def build_from_spec(self, df, spec: dict) -> str | None:
        """Render the chart the agent chose (bar, line, pie, scatter) as HTML, or None for 'table'."""
        fig = self.build_figure(df, spec)
        return fig.to_html(full_html=False, include_plotlyjs="cdn") if fig is not None else None

    def build_figure(self, df, spec: dict):
        """Plotly figure for the agent's chart choice, or None when a table fits better."""
        if df.empty or not spec or spec.get("type") == "table":
            return None
        x, y, kind = spec.get("x"), spec.get("y"), spec.get("type", "bar")
        if x not in df.columns or y not in df.columns:
            return None
        if kind == "line":
            fig = px.line(df, x=x, y=y, markers=True)
        elif kind == "pie":
            fig = px.pie(df, names=x, values=y)
        elif kind == "scatter":
            fig = px.scatter(df, x=x, y=y)
        else:
            fig = px.bar(df, x=x, y=y)
        fig.update_layout(margin=dict(l=20, r=20, t=30, b=20), height=420)
        return fig

    def build_chart_html(self, df) -> str | None:
        if df.empty:
            return None
        numeric_columns = [col for col in df.columns if df[col].dtype.kind in "biufc"]
        if not numeric_columns:
            return None
        fig = px.bar(df, x=df.columns[0], y=numeric_columns[0])
        return fig.to_html(full_html=False, include_plotlyjs="cdn")
