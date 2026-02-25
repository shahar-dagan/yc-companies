"""
pages/dashboard.py — YC Market Opportunity Dashboard.
Auto-appears in Streamlit sidebar nav as a second page.
"""

import streamlit as st
import plotly.graph_objects as go
from collections import Counter, defaultdict

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import get_db_connection, setup_conversations_table

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="YC Market Dashboard",
    page_icon="📊",
    layout="wide",
)

# ── Shadcn zinc-dark theme constants ──────────────────────────────────────────
BG     = "#09090b"   # zinc-950
CARD   = "#18181b"   # zinc-900
BORDER = "#27272a"   # zinc-800
FG     = "#fafafa"   # zinc-50
MUTED  = "#a1a1aa"   # zinc-400
DIM    = "#71717a"   # zinc-500

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


# ── Page header with refresh button ───────────────────────────────────────────
col_title, col_refresh = st.columns([6, 1])
with col_title:
    st.title("📊 YC Market Opportunity Dashboard")
    st.caption("All charts sourced from local SQLite DB. Run `python ingest.py` to refresh data.")
with col_refresh:
    if st.button("↻ Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── DB setup ──────────────────────────────────────────────────────────────────
conn = get_db_connection()
setup_conversations_table(conn)

# Check DB has data
try:
    count = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    if count == 0:
        st.error("No company data found. Run `python ingest.py` first.")
        st.stop()
except Exception:
    st.error("Database not found or not initialised. Run `python ingest.py` first.")
    st.stop()


# ── Data-fetching helpers (cached) ────────────────────────────────────────────
@st.cache_data(ttl=3600)
def get_sorted_batches() -> list:
    """All batch labels sorted chronologically (oldest first)."""
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT DISTINCT batch_label FROM companies WHERE batch_label != ''"
    ).fetchall()
    labels = [r[0] for r in rows]

    def _key(label):
        if not label or len(label) < 2:
            return (0, 9)
        prefix = label[0]
        suffix = label[1:]
        year   = int(suffix) if suffix.isdigit() else 0
        season = {"W": 0, "S": 1, "F": 2}.get(prefix, 9)
        return (year, season)

    return sorted(labels, key=_key)


@st.cache_data(ttl=3600)
def get_industry_batch_counts() -> dict:
    """company counts keyed by (industry, batch_label)."""
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT industry, batch_label, COUNT(*) AS n "
        "FROM companies WHERE batch_label != '' AND industry != '' "
        "GROUP BY industry, batch_label"
    ).fetchall()
    return {(r[0], r[1]): r[2] for r in rows}


@st.cache_data(ttl=3600)
def get_industry_status_counts() -> list:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT industry, status, "
        "COUNT(*) AS total, "
        "SUM(is_hiring) AS hiring, "
        "SUM(top_company) AS top "
        "FROM companies WHERE industry != '' "
        "GROUP BY industry, status"
    ).fetchall()
    return [{"industry": r[0], "status": r[1], "total": r[2],
             "hiring": r[3], "top": r[4]} for r in rows]


@st.cache_data(ttl=3600)
def get_tag_rows() -> list:
    """All (batch_label, tags_str) pairs for tag analysis."""
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT batch_label, tags FROM companies "
        "WHERE batch_label != '' AND tags != ''"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


@st.cache_data(ttl=3600)
def get_country_stats() -> list:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT country, COUNT(*) AS total, "
        "CAST(SUM(is_hiring) AS FLOAT) / COUNT(*) AS hire_rate "
        "FROM companies "
        "WHERE country != '' AND country NOT LIKE '%; Remote' "
        "GROUP BY country "
        "ORDER BY total DESC LIMIT 20"
    ).fetchall()
    return [{"country": r[0], "total": r[1], "hire_rate": r[2]} for r in rows]


@st.cache_data(ttl=3600)
def get_batch_sizes() -> list:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT batch_label, COUNT(*) AS n FROM companies "
        "WHERE batch_label != '' GROUP BY batch_label"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


# ── Load data ─────────────────────────────────────────────────────────────────
sorted_batches    = get_sorted_batches()
ind_batch_counts  = get_industry_batch_counts()
ind_status_rows   = get_industry_status_counts()
tag_rows          = get_tag_rows()
country_stats     = get_country_stats()
batch_size_raw    = get_batch_sizes()

# Derived: industry totals
ind_totals = defaultdict(int)
for r in ind_status_rows:
    ind_totals[r["industry"]] += r["total"]

# ═══════════════════════════════════════════════════════════════════════════════
# Row 1: Industry Momentum  |  Hiring Heat
# ═══════════════════════════════════════════════════════════════════════════════
col1, col2 = st.columns(2)

# ── Chart 1: Industry Momentum ────────────────────────────────────────────────
with col1:
    st.subheader("1. Industry Momentum")
    st.caption("% change: companies in last 4 batches vs prior 4 batches")

    if len(sorted_batches) >= 8:
        last4  = set(sorted_batches[-4:])
        prior4 = set(sorted_batches[-8:-4])

        ind_last4  = defaultdict(int)
        ind_prior4 = defaultdict(int)
        for (ind, batch), n in ind_batch_counts.items():
            if batch in last4:
                ind_last4[ind] += n
            elif batch in prior4:
                ind_prior4[ind] += n

        momentum = {}
        for ind, n_last in ind_last4.items():
            if n_last < 5:
                continue
            n_prior = ind_prior4.get(ind, 0)
            if n_prior > 0:
                pct = (n_last - n_prior) / n_prior * 100
            else:
                pct = 100.0
            momentum[ind] = pct

        top_mom = sorted(momentum.items(), key=lambda x: abs(x[1]), reverse=True)[:15]
        top_mom = sorted(top_mom, key=lambda x: x[1])

        labels = [x[0] for x in top_mom]
        values = [x[1] for x in top_mom]
        bar_colors = [C_GREEN if v >= 0 else C_RED for v in values]

        fig = go.Figure(go.Bar(
            x=values, y=labels,
            orientation="h",
            marker_color=bar_colors,
            hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
        ))
        fig.add_vline(x=0, line_color=MUTED, line_width=1)
        fig.update_layout(**make_layout(
            "Industry Momentum (last 4 vs prior 4 batches)",
            xaxis_title="% Change",
        ))
        show(fig)
    else:
        st.info("Need at least 8 batches of data for this chart.")

# ── Chart 2: Hiring Heat ──────────────────────────────────────────────────────
with col2:
    st.subheader("2. Hiring Heat")
    st.caption("% currently hiring by industry (Active companies, ≥20 total)")

    ind_active_total   = defaultdict(int)
    ind_active_hiring  = defaultdict(int)
    for r in ind_status_rows:
        if r["status"] == "Active":
            ind_active_total[r["industry"]]  += r["total"]
            ind_active_hiring[r["industry"]] += r["hiring"]

    hire_heat = {}
    for ind, total in ind_active_total.items():
        if total >= 20 and ind:
            hire_heat[ind] = ind_active_hiring[ind] / total * 100

    hire_sorted = sorted(hire_heat.items(), key=lambda x: x[1])[-20:]
    labels = [x[0] for x in hire_sorted]
    values = [x[1] for x in hire_sorted]

    min_v = min(values) if values else 0
    max_v = max(values) if values else 1
    norm_vals = [(v - min_v) / max(1, max_v - min_v) for v in values]
    bar_colors = [f"rgba(249,115,22,{0.3 + 0.7 * n:.2f})" for n in norm_vals]

    fig = go.Figure(go.Bar(
        x=values, y=labels,
        orientation="h",
        marker_color=bar_colors,
        hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
    ))
    fig.update_layout(**make_layout(
        "Hiring Heat by Industry (Active companies)",
        xaxis_title="% Hiring",
    ))
    show(fig)

# ═══════════════════════════════════════════════════════════════════════════════
# Row 2: Survival Rates  |  Emerging Tags
# ═══════════════════════════════════════════════════════════════════════════════
col3, col4 = st.columns(2)

# ── Chart 3: Survival Rates ───────────────────────────────────────────────────
with col3:
    st.subheader("3. Survival Rates")
    st.caption("% Active by industry — color gradient red → green")

    ind_all_total  = defaultdict(int)
    ind_all_active = defaultdict(int)
    for r in ind_status_rows:
        if r["industry"]:
            ind_all_total[r["industry"]] += r["total"]
            if r["status"] == "Active":
                ind_all_active[r["industry"]] += r["total"]

    survival = {
        ind: ind_all_active[ind] / total * 100
        for ind, total in ind_all_total.items()
        if total >= 20 and ind
    }
    surv_sorted = sorted(survival.items(), key=lambda x: x[1])

    labels = [x[0] for x in surv_sorted]
    values = [x[1] for x in surv_sorted]

    # Red → green gradient via rgba interpolation
    bar_colors = []
    for v in values:
        n = v / 100
        r = int((1 - n) * 248 + n * 34)
        g = int((1 - n) * 81  + n * 197)
        b = int((1 - n) * 73  + n * 94)
        bar_colors.append(f"rgba({r},{g},{b},0.85)")

    fig = go.Figure(go.Bar(
        x=values, y=labels,
        orientation="h",
        marker_color=bar_colors,
        hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
    ))
    fig.update_layout(**make_layout(
        "Survival Rates by Industry (% Active)",
        xaxis_title="% Active",
    ))
    show(fig)

# ── Chart 4: Emerging Tags ────────────────────────────────────────────────────
with col4:
    st.subheader("4. Emerging Tags")
    st.caption("Tags gaining momentum: frequency in last 6 batches vs historical avg")

    n_recent     = min(6, len(sorted_batches))
    recent_set   = set(sorted_batches[-n_recent:]) if sorted_batches else set()
    n_historical = max(1, len(sorted_batches) - n_recent)

    tag_recent_cnt = Counter()
    tag_hist_cnt   = Counter()

    for batch_label, tags_str in tag_rows:
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        if batch_label in recent_set:
            tag_recent_cnt.update(tags)
        else:
            tag_hist_cnt.update(tags)

    emerging = {}
    for tag, recent_n in tag_recent_cnt.items():
        total_n = recent_n + tag_hist_cnt.get(tag, 0)
        if total_n < 10:
            continue
        recent_rate = recent_n / n_recent
        hist_rate   = tag_hist_cnt.get(tag, 0) / n_historical
        if hist_rate > 0:
            ratio = recent_rate / hist_rate
        else:
            ratio = recent_rate * 5
        emerging[tag] = ratio

    top_emerging = sorted(emerging.items(), key=lambda x: x[1], reverse=True)[:15]
    top_emerging = list(reversed(top_emerging))

    if top_emerging:
        labels = [x[0] for x in top_emerging]
        values = [x[1] for x in top_emerging]

        fig = go.Figure(go.Bar(
            x=values, y=labels,
            orientation="h",
            marker_color=C_BLUE,
            opacity=0.85,
            hovertemplate="%{y}: %{x:.2f}x<extra></extra>",
        ))
        fig.add_vline(x=1.0, line_color=MUTED, line_width=1,
                      line_dash="dash", annotation_text="baseline",
                      annotation_font_color=MUTED)
        fig.update_layout(**make_layout(
            f"Emerging Tags (last {n_recent} batches vs prior)",
            xaxis_title="Recent vs Historical Ratio",
        ))
        show(fig)
    else:
        st.info("Not enough tag data.")

# ═══════════════════════════════════════════════════════════════════════════════
# Row 3: Geographic Distribution  |  Top Company Density
# ═══════════════════════════════════════════════════════════════════════════════
col5, col6 = st.columns(2)

# ── Chart 5: Geographic Distribution ─────────────────────────────────────────
with col5:
    st.subheader("5. Geographic Distribution")
    st.caption("Top 20 countries — bar opacity reflects hiring rate")

    if country_stats:
        countries  = [r["country"] for r in country_stats]
        totals     = [r["total"] for r in country_stats]
        hire_rates = [r["hire_rate"] or 0 for r in country_stats]

        # Reverse for horizontal bar (largest at top)
        countries  = countries[::-1]
        totals     = totals[::-1]
        hire_rates = hire_rates[::-1]

        bar_colors = [
            f"rgba(249,115,22,{max(0.25, min(1.0, hr)):.2f})"
            for hr in hire_rates
        ]

        fig = go.Figure(go.Bar(
            x=totals, y=countries,
            orientation="h",
            marker_color=bar_colors,
            hovertemplate="%{y}: %{x:,} companies<extra></extra>",
        ))
        fig.update_layout(**make_layout(
            "Top 20 Countries (opacity = hiring rate)",
            xaxis_title="Number of Companies",
        ))
        show(fig)

# ── Chart 6: Top Company Density ──────────────────────────────────────────────
with col6:
    st.subheader("6. Top Company Density")
    st.caption("% flagged as YC top company by industry (≥20 companies)")

    ind_top  = defaultdict(int)
    ind_tot2 = defaultdict(int)
    for r in ind_status_rows:
        if r["industry"]:
            ind_tot2[r["industry"]] += r["total"]
            ind_top[r["industry"]]  += r["top"]

    density = {
        ind: ind_top[ind] / total * 100
        for ind, total in ind_tot2.items()
        if total >= 20 and ind
    }
    dens_sorted = sorted(density.items(), key=lambda x: x[1])[-20:]

    labels = [x[0] for x in dens_sorted]
    values = [x[1] for x in dens_sorted]

    fig = go.Figure(go.Bar(
        x=values, y=labels,
        orientation="h",
        marker_color=C_PURPLE,
        opacity=0.85,
        hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
    ))
    fig.update_layout(**make_layout(
        "Top Company Density by Industry",
        xaxis_title="% Top Company",
    ))
    show(fig)

# ═══════════════════════════════════════════════════════════════════════════════
# Row 4: Batch Size Trend  |  Industry × Status Heatmap
# ═══════════════════════════════════════════════════════════════════════════════
col7, col8 = st.columns(2)

# ── Chart 7: Batch Size Trend ─────────────────────────────────────────────────
with col7:
    st.subheader("7. Batch Size Trend")
    st.caption("Companies per batch over time (Python-side chronological sort)")

    batch_map = {label: n for label, n in batch_size_raw}
    ordered_sizes = [batch_map.get(b, 0) for b in sorted_batches]

    fig = go.Figure(go.Bar(
        x=sorted_batches,
        y=ordered_sizes,
        marker_color=C_ORANGE,
        opacity=0.85,
        hovertemplate="%{x}: %{y} companies<extra></extra>",
    ))
    fig.update_layout(**make_layout(
        "Batch Size Trend",
        yaxis_title="Companies",
        xaxis=dict(**AXIS, tickangle=60, title=""),
    ))
    show(fig)

# ── Chart 8: Industry × Status Heatmap ───────────────────────────────────────
with col8:
    st.subheader("8. Industry × Status Heatmap")
    st.caption("% of companies in each status, for top 10 industries")

    key_statuses = ["Active", "Acquired", "Inactive", "Public"]

    top10_inds = sorted(ind_tot2.items(), key=lambda x: x[1], reverse=True)[:10]
    top10_inds = [x[0] for x in top10_inds if x[0]]

    ind_status_lookup = defaultdict(lambda: defaultdict(int))
    for r in ind_status_rows:
        ind_status_lookup[r["industry"]][r["status"]] += r["total"]

    matrix = []
    text_matrix = []
    for ind in top10_inds:
        row_total = sum(ind_status_lookup[ind][s] for s in key_statuses)
        row = []
        text_row = []
        for status in key_statuses:
            val = ind_status_lookup[ind][status] / row_total * 100 if row_total > 0 else 0
            row.append(val)
            text_row.append(f"{val:.0f}%")
        matrix.append(row)
        text_matrix.append(text_row)

    fig = go.Figure(go.Heatmap(
        z=matrix,
        x=key_statuses,
        y=top10_inds,
        colorscale="YlOrRd",
        zmin=0, zmax=100,
        text=text_matrix,
        texttemplate="%{text}",
        textfont=dict(color=FG, size=11),
        hovertemplate="%{y} / %{x}: %{z:.1f}%<extra></extra>",
        colorbar=dict(
            title=dict(text="% of industry", font=dict(color=MUTED)),
            tickfont=dict(color=DIM),
            bgcolor=CARD,
            bordercolor=BORDER,
        ),
    ))
    fig.update_layout(**make_layout(
        "Outcome by Industry (% in each status)",
    ))
    show(fig)
