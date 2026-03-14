"""
pages/analyze.py — In-app YC Companies Analysis with live data from the YC OSS API.
Plots 8 interactive Plotly charts matching the app's dark theme.
Data fetched via utils.fetch_yc_data() (shared, cached 1 hour).
"""

import sys
from collections import Counter
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import fetch_yc_data

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="YC Analysis",
    page_icon="📈",
    layout="wide",
)

# ── Theme constants (matches dashboard.py) ───────────────────────────────────
BG     = "#09090b"
CARD   = "#18181b"
BORDER = "#27272a"
FG     = "#fafafa"
MUTED  = "#a1a1aa"
DIM    = "#71717a"

C_ORANGE = "#f97316"
C_BLUE   = "#3b82f6"
C_GREEN  = "#22c55e"
C_RED    = "#ef4444"
C_PURPLE = "#a855f7"

BASE_LAYOUT = dict(
    paper_bgcolor=BG, plot_bgcolor=BG,
    font=dict(family="Inter, system-ui, sans-serif", color=FG, size=12),
    margin=dict(l=16, r=16, t=48, b=16),
    hoverlabel=dict(bgcolor=CARD, bordercolor=BORDER, font=dict(color=FG)),
    legend=dict(bgcolor=CARD, bordercolor=BORDER, font=dict(color=MUTED)),
)
AXIS = dict(gridcolor=BORDER, linecolor=BORDER,
            tickfont=dict(color=DIM), title_font=dict(color=MUTED),
            zerolinecolor=BORDER)


def make_layout(title, xaxis_title="", yaxis_title="", **kw):
    layout = dict(**BASE_LAYOUT,
                  title=dict(text=title, x=0, xanchor="left", font=dict(color=FG)),
                  xaxis=dict(**AXIS, title=xaxis_title),
                  yaxis=dict(**AXIS, title=yaxis_title))
    layout.update(kw)
    return layout


def show(fig):
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": "hover", "displaylogo": False})


# ── Page header ───────────────────────────────────────────────────────────────
col_title, col_refresh = st.columns([6, 1])
with col_title:
    st.title("📈 YC Companies Analysis")
    st.caption("Live data from the YC OSS API · cached 1 hour")
with col_refresh:
    if st.button("↻ Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── Load data ─────────────────────────────────────────────────────────────────
try:
    companies, sorted_batches = fetch_yc_data()
except Exception as e:
    st.error(f"Failed to fetch data from YC API: {e}")
    st.stop()

if not companies:
    st.error("No data returned from API.")
    st.stop()

st.caption(f"{len(companies):,} companies across {len(sorted_batches)} batches")

# ── Precompute aggregations ───────────────────────────────────────────────────
batch_counts: dict[str, int] = {b: 0 for b in sorted_batches}
for c in companies:
    batch_counts[c["batch_label"]] = batch_counts.get(c["batch_label"], 0) + 1

status_counts: Counter = Counter(c["status"] for c in companies)

industry_counts: Counter = Counter(
    c["industry"] for c in companies if c["industry"] != "Unknown"
)

tag_counts: Counter = Counter()
for c in companies:
    tag_counts.update(c["tags"])

batch_hiring: dict[str, dict] = {b: {"sum": 0, "count": 0} for b in sorted_batches}
for c in companies:
    b = c["batch_label"]
    if b in batch_hiring:
        batch_hiring[b]["count"] += 1
        if c["is_hiring"]:
            batch_hiring[b]["sum"] += 1

country_counts: Counter = Counter(
    c["country"] for c in companies if c["country"]
)

b2b_per_batch: dict[str, int] = {b: 0 for b in sorted_batches}
b2c_per_batch: dict[str, int] = {b: 0 for b in sorted_batches}
for c in companies:
    b = c["batch_label"]
    tags_lower = [t.lower() for t in c["tags"]]
    if "b2b" in tags_lower:
        b2b_per_batch[b] = b2b_per_batch.get(b, 0) + 1
    if "b2c" in tags_lower:
        b2c_per_batch[b] = b2c_per_batch.get(b, 0) + 1

batch_team: dict[str, list] = {b: [] for b in sorted_batches}
for c in companies:
    ts = c["team_size"]
    if ts is not None:
        try:
            batch_team[c["batch_label"]].append(float(ts))
        except (TypeError, ValueError):
            pass


def _rolling3(values: list) -> list:
    return [
        sum(values[max(0, i - 1): i + 2]) / len(values[max(0, i - 1): i + 2])
        for i in range(len(values))
    ]


def _median(lst: list):
    if not lst:
        return None
    s = sorted(lst)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# ══════════════════════════════════════════════════════════════════════════════
# Row 1: Companies per batch  |  Status breakdown
# ══════════════════════════════════════════════════════════════════════════════
col1, col2 = st.columns(2)

with col1:
    st.subheader("1. Companies per Batch")
    counts = [batch_counts.get(b, 0) for b in sorted_batches]
    fig = go.Figure(go.Bar(
        x=sorted_batches, y=counts,
        marker_color=C_ORANGE, opacity=0.85,
        hovertemplate="%{x}: %{y} companies<extra></extra>",
    ))
    fig.update_layout(**make_layout(
        "YC Companies per Batch",
        yaxis_title="Companies",
        xaxis=dict(**AXIS, tickangle=60, title=""),
    ))
    show(fig)

with col2:
    st.subheader("2. Status Breakdown")
    status_color_map = {
        "Active": C_GREEN, "Acquired": C_BLUE,
        "Inactive": C_RED, "Public": C_PURPLE,
    }
    s_labels = list(status_counts.keys())
    s_values = list(status_counts.values())
    fig = go.Figure(go.Pie(
        labels=s_labels, values=s_values,
        marker=dict(
            colors=[status_color_map.get(s, MUTED) for s in s_labels],
            line=dict(color=BG, width=2),
        ),
        hole=0.5,
        textinfo="label+percent",
        textfont=dict(color=FG),
        hovertemplate="%{label}: %{value:,} (%{percent})<extra></extra>",
    ))
    fig.update_layout(**make_layout("Company Status Distribution"))
    show(fig)

# ══════════════════════════════════════════════════════════════════════════════
# Row 2: Top industries  |  Top tags
# ══════════════════════════════════════════════════════════════════════════════
col3, col4 = st.columns(2)

with col3:
    st.subheader("3. Top 15 Industries")
    top_inds = list(reversed(industry_counts.most_common(15)))
    fig = go.Figure(go.Bar(
        x=[x[1] for x in top_inds], y=[x[0] for x in top_inds],
        orientation="h",
        marker_color=C_ORANGE, opacity=0.85,
        hovertemplate="%{y}: %{x:,}<extra></extra>",
    ))
    fig.update_layout(**make_layout("Top 15 Industries", xaxis_title="Number of Companies"))
    show(fig)

with col4:
    st.subheader("4. Top 30 Tags")
    top_tags = list(reversed(tag_counts.most_common(30)))
    fig = go.Figure(go.Bar(
        x=[x[1] for x in top_tags], y=[x[0] for x in top_tags],
        orientation="h",
        marker_color=C_BLUE, opacity=0.85,
        hovertemplate="%{y}: %{x:,}<extra></extra>",
    ))
    fig.update_layout(**make_layout("Top 30 Tags Across All YC Companies", xaxis_title="Number of Companies"))
    show(fig)

# ══════════════════════════════════════════════════════════════════════════════
# Row 3: Hiring rate per batch  |  Geographic distribution
# ══════════════════════════════════════════════════════════════════════════════
col5, col6 = st.columns(2)

with col5:
    st.subheader("5. Hiring Rate per Batch")
    hire_batches = [b for b in sorted_batches if batch_hiring[b]["count"] >= 5]
    hire_rates   = [batch_hiring[b]["sum"] / batch_hiring[b]["count"] * 100 for b in hire_batches]
    avg_rate     = sum(hire_rates) / len(hire_rates) if hire_rates else 0
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=hire_batches, y=hire_rates,
        marker_color=C_GREEN, opacity=0.85,
        hovertemplate="%{x}: %{y:.1f}%<extra></extra>",
    ))
    fig.add_hline(
        y=avg_rate, line_color=C_ORANGE, line_width=1.5, line_dash="dash",
        annotation_text=f"avg {avg_rate:.0f}%", annotation_font_color=MUTED,
    )
    fig.update_layout(**make_layout(
        "Hiring Rate per Batch",
        yaxis_title="% Companies Hiring",
        xaxis=dict(**AXIS, tickangle=60, title=""),
    ))
    show(fig)

with col6:
    st.subheader("6. Geographic Distribution")
    top_countries = list(reversed(country_counts.most_common(20)))
    fig = go.Figure(go.Bar(
        x=[x[1] for x in top_countries], y=[x[0] for x in top_countries],
        orientation="h",
        marker_color=C_PURPLE, opacity=0.85,
        hovertemplate="%{y}: %{x:,} companies<extra></extra>",
    ))
    fig.update_layout(**make_layout("Top 20 Countries by Company Count", xaxis_title="Number of Companies"))
    show(fig)

# ══════════════════════════════════════════════════════════════════════════════
# Row 4: B2B vs B2C over time  |  Team size trend
# ══════════════════════════════════════════════════════════════════════════════
col7, col8 = st.columns(2)

with col7:
    st.subheader("7. B2B vs B2C Over Time")
    b2b_vals = _rolling3([b2b_per_batch.get(b, 0) for b in sorted_batches])
    b2c_vals = _rolling3([b2c_per_batch.get(b, 0) for b in sorted_batches])
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=sorted_batches, y=b2b_vals,
        mode="lines", name="B2B",
        line=dict(color=C_BLUE, width=2),
        fill="tozeroy", fillcolor="rgba(59,130,246,0.12)",
        hovertemplate="%{x}: %{y:.1f}<extra>B2B</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=sorted_batches, y=b2c_vals,
        mode="lines", name="B2C",
        line=dict(color=C_GREEN, width=2),
        fill="tozeroy", fillcolor="rgba(34,197,94,0.12)",
        hovertemplate="%{x}: %{y:.1f}<extra>B2C</extra>",
    ))
    fig.update_layout(**make_layout(
        "B2B vs B2C Tag Count Over Time (3-batch rolling avg)",
        yaxis_title="Companies",
        xaxis=dict(**AXIS, tickangle=60, title=""),
    ))
    show(fig)

with col8:
    st.subheader("8. Team Size Trend")
    ts_batches = [b for b in sorted_batches if len(batch_team[b]) >= 5]
    ts_medians = [_median(batch_team[b]) for b in ts_batches]
    fig = go.Figure(go.Scatter(
        x=ts_batches, y=ts_medians,
        mode="lines+markers",
        line=dict(color=C_ORANGE, width=2),
        marker=dict(color=C_ORANGE, size=4),
        fill="tozeroy", fillcolor="rgba(249,115,22,0.10)",
        hovertemplate="%{x}: %{y:.0f} employees (median)<extra></extra>",
    ))
    fig.update_layout(**make_layout(
        "Median Team Size per Batch",
        yaxis_title="Employees (median)",
        xaxis=dict(**AXIS, tickangle=60, title=""),
    ))
    show(fig)
