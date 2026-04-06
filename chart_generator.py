"""
Chart Generator
───────────────
Converts query result data + a chart spec from the AI agent into
a Plotly figure JSON that the front-end renders via plotly.js.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

logger = logging.getLogger(__name__)

# Colour palette (IBM Carbon-inspired, works on dark backgrounds)
PALETTE = [
    "#4589FF", "#42BE65", "#FF832B", "#EE5396",
    "#08BDBA", "#A56EFF", "#FFD700", "#FA4D56",
]

LAYOUT_DEFAULTS = dict(
    font_family="'JetBrains Mono', 'Fira Code', monospace",
    font_color="#E8E8E8",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(15,15,20,0.6)",
    margin=dict(l=48, r=24, t=48, b=48),
    colorway=PALETTE,
    legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="rgba(255,255,255,0.1)"),
    xaxis=dict(gridcolor="rgba(255,255,255,0.07)", zerolinecolor="rgba(255,255,255,0.1)"),
    yaxis=dict(gridcolor="rgba(255,255,255,0.07)", zerolinecolor="rgba(255,255,255,0.1)"),
)


def generate_chart(
    data: list[dict],
    columns: list[str],
    chart_spec: dict,
) -> dict | None:
    """
    Build a Plotly figure from the agent's chart recommendation.

    Returns the Plotly figure as a JSON-serialisable dict, or None if
    the data is unsuitable for charting.
    """
    if not data or not columns:
        return None

    chart_type = (chart_spec.get("chart_type") or "table").lower()
    if chart_type == "table":
        return _make_table(data, columns, chart_spec)

    x_col = chart_spec.get("x_column") or columns[0]
    y_col = chart_spec.get("y_column") or (columns[1] if len(columns) > 1 else columns[0])
    title = chart_spec.get("title", "Query Results")

    df = pd.DataFrame(data)

    # Normalise column names (case-insensitive match)
    col_map = {c.lower(): c for c in df.columns}
    x_col = col_map.get(x_col.lower(), df.columns[0])
    y_col = col_map.get(y_col.lower(), df.columns[min(1, len(df.columns) - 1)])

    try:
        if chart_type == "bar":
            fig = _bar(df, x_col, y_col, title)
        elif chart_type == "line":
            fig = _line(df, x_col, y_col, title)
        elif chart_type == "pie":
            fig = _pie(df, x_col, y_col, title)
        elif chart_type == "scatter":
            fig = _scatter(df, x_col, y_col, title)
        else:
            fig = _bar(df, x_col, y_col, title)  # fallback

        fig.update_layout(**LAYOUT_DEFAULTS)
        return fig.to_dict()

    except Exception as exc:
        logger.warning("Chart generation failed: %s", exc)
        return _make_table(data, columns, chart_spec)


# ── Chart builders ────────────────────────────────────────────────────────────

def _bar(df: pd.DataFrame, x: str, y: str, title: str) -> go.Figure:
    fig = px.bar(df, x=x, y=y, title=title, color_discrete_sequence=PALETTE)
    fig.update_traces(marker_line_width=0, opacity=0.9)
    return fig


def _line(df: pd.DataFrame, x: str, y: str, title: str) -> go.Figure:
    fig = px.line(df, x=x, y=y, title=title, color_discrete_sequence=PALETTE,
                  markers=True)
    fig.update_traces(line_width=2.5)
    return fig


def _pie(df: pd.DataFrame, names: str, values: str, title: str) -> go.Figure:
    fig = px.pie(df, names=names, values=values, title=title,
                 color_discrete_sequence=PALETTE, hole=0.35)
    fig.update_traces(textposition="inside", textinfo="percent+label",
                      marker=dict(line=dict(color="#0D0D14", width=2)))
    return fig


def _scatter(df: pd.DataFrame, x: str, y: str, title: str) -> go.Figure:
    fig = px.scatter(df, x=x, y=y, title=title, color_discrete_sequence=PALETTE)
    fig.update_traces(marker_size=8, opacity=0.85)
    return fig


def _make_table(data: list[dict], columns: list[str], spec: dict) -> dict:
    """Fallback: render a styled Plotly table."""
    title = spec.get("title", "Query Results")
    df = pd.DataFrame(data, columns=columns)
    fig = go.Figure(data=[go.Table(
        header=dict(
            values=[f"<b>{c}</b>" for c in columns],
            fill_color="#1E1E2E",
            font=dict(color="#A9B1D6", size=12),
            align="left",
            line_color="#313244",
            height=32,
        ),
        cells=dict(
            values=[df[c].astype(str).tolist() for c in columns],
            fill_color=["#13131F", "#16161E"],
            font=dict(color="#CDD6F4", size=11),
            align="left",
            line_color="#313244",
            height=28,
        ),
    )])
    fig.update_layout(
        title=title,
        **LAYOUT_DEFAULTS,
    )
    return fig.to_dict()
