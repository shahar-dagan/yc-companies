"""
pages/dashboard.py — YC Market Opportunity Dashboard.
Auto-appears in Streamlit sidebar nav as a second page.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st
from collections import Counter

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import COLORS, apply_dark_theme, get_db_connection, setup_conversations_table

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="YC Market Dashboard",
    page_icon="📊",
    layout="wide",
)

apply_dark_theme()

st.title("📊 YC Market Opportunity Dashboard")
st.caption("All charts sourced from local SQLite DB. Run `python ingest.py` to refresh data.")

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


# ── Chart helpers ─────────────────────────────────────────────────────────────
def show(fig):
    st.pyplot(fig)
    plt.close(fig)


# ── Load data ─────────────────────────────────────────────────────────────────
sorted_batches    = get_sorted_batches()
ind_batch_counts  = get_industry_batch_counts()
ind_status_rows   = get_industry_status_counts()
tag_rows          = get_tag_rows()
country_stats     = get_country_stats()
batch_size_raw    = get_batch_sizes()

# Derived: industry totals
from collections import defaultdict
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

        # Only industries present in both periods with ≥5 companies in last4
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
        bar_colors = [COLORS["green"] if v >= 0 else COLORS["red"] for v in values]

        fig, ax = plt.subplots(figsize=(8, 6))
        ax.barh(labels, values, color=bar_colors, alpha=0.85)
        ax.axvline(0, color=COLORS["muted"], linewidth=1)
        ax.set_xlabel("% Change")
        ax.set_title("Industry Momentum (last 4 vs prior 4 batches)")
        ax.xaxis.grid(True)
        ax.set_axisbelow(True)
        fig.tight_layout()
        show(fig)
    else:
        st.info("Need at least 8 batches of data for this chart.")

# ── Chart 2: Hiring Heat ──────────────────────────────────────────────────────
with col2:
    st.subheader("2. Hiring Heat")
    st.caption("% currently hiring by industry (Active companies, ≥20 total)")

    # Aggregate: active companies by industry
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

    # Gradient from muted to primary
    norm_vals = [(v - min(values)) / max(1, max(values) - min(values)) for v in values]
    bar_colors = [
        (1 - n) * np.array([0.545, 0.580, 0.620]) + n * np.array([1.0, 0.4, 0.0])
        for n in norm_vals
    ]

    fig, ax = plt.subplots(figsize=(8, 6))
    bars = ax.barh(labels, values, color=bar_colors, alpha=0.9)
    for bar, v in zip(bars, values):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                f"{v:.0f}%", va="center", fontsize=8)
    ax.set_xlabel("% Hiring")
    ax.set_title("Hiring Heat by Industry (Active companies)")
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    fig.tight_layout()
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

    # Red → green gradient
    norm_vals = [v / 100 for v in values]
    bar_colors = [
        (1 - n) * np.array([0.973, 0.318, 0.286]) + n * np.array([0.247, 0.729, 0.314])
        for n in norm_vals
    ]

    fig, ax = plt.subplots(figsize=(8, 6))
    bars = ax.barh(labels, values, color=bar_colors, alpha=0.9)
    for bar, v in zip(bars, values):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                f"{v:.0f}%", va="center", fontsize=8)
    ax.set_xlabel("% Active")
    ax.set_title("Survival Rates by Industry (% Active)")
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    fig.tight_layout()
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
            ratio = recent_rate * 5  # new tag with no history
        emerging[tag] = ratio

    top_emerging = sorted(emerging.items(), key=lambda x: x[1], reverse=True)[:15]
    top_emerging = list(reversed(top_emerging))

    if top_emerging:
        labels = [x[0] for x in top_emerging]
        values = [x[1] for x in top_emerging]

        fig, ax = plt.subplots(figsize=(8, 6))
        ax.barh(labels, values, color=COLORS["blue"], alpha=0.85)
        ax.axvline(1.0, color=COLORS["muted"], linewidth=1, linestyle="--",
                   label="baseline (1.0 = no change)")
        ax.set_xlabel("Recent vs Historical Ratio")
        ax.set_title(f"Emerging Tags (last {n_recent} batches vs prior)")
        ax.legend(fontsize=8, framealpha=0.3)
        ax.xaxis.grid(True)
        ax.set_axisbelow(True)
        fig.tight_layout()
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

        # Alpha = hiring rate (min 0.25 so bars are always visible)
        alphas = [max(0.25, min(1.0, hr)) for hr in hire_rates]

        fig, ax = plt.subplots(figsize=(8, 7))
        for i, (label, val, alpha) in enumerate(zip(countries, totals, alphas)):
            r, g, b = 1.0, 0.4, 0.0  # primary orange
            ax.barh(i, val, color=(r, g, b, alpha))
        ax.set_yticks(range(len(countries)))
        ax.set_yticklabels(countries, fontsize=8)
        ax.set_xlabel("Number of Companies")
        ax.set_title("Top 20 Countries\n(opacity = hiring rate)")
        ax.xaxis.grid(True)
        ax.set_axisbelow(True)
        fig.tight_layout()
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

    fig, ax = plt.subplots(figsize=(8, 6))
    bars = ax.barh(labels, values, color=COLORS["purple"], alpha=0.85)
    for bar, v in zip(bars, values):
        ax.text(bar.get_width() + 0.05, bar.get_y() + bar.get_height() / 2,
                f"{v:.1f}%", va="center", fontsize=8)
    ax.set_xlabel("% Top Company")
    ax.set_title("Top Company Density by Industry")
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    fig.tight_layout()
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

    step = max(1, len(sorted_batches) // 20)
    tick_positions = list(range(0, len(sorted_batches), step))
    tick_labels    = [sorted_batches[i] for i in tick_positions]

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(range(len(sorted_batches)), ordered_sizes,
           color=COLORS["primary"], alpha=0.85, width=0.8)
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("Companies")
    ax.set_title("Batch Size Trend")
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    fig.tight_layout()
    show(fig)

# ── Chart 8: Industry × Status Heatmap ───────────────────────────────────────
with col8:
    st.subheader("8. Industry × Status Heatmap")
    st.caption("% of companies in each status, for top 10 industries")

    key_statuses = ["Active", "Acquired", "Inactive", "Public"]

    # Top 10 industries by total company count
    top10_inds = sorted(ind_tot2.items(), key=lambda x: x[1], reverse=True)[:10]
    top10_inds = [x[0] for x in top10_inds if x[0]]

    # Build (industry, status) → count lookup
    ind_status_lookup = defaultdict(lambda: defaultdict(int))
    for r in ind_status_rows:
        ind_status_lookup[r["industry"]][r["status"]] += r["total"]

    # Build matrix
    matrix = np.zeros((len(top10_inds), len(key_statuses)))
    for i, ind in enumerate(top10_inds):
        row_total = sum(ind_status_lookup[ind][s] for s in key_statuses)
        if row_total > 0:
            for j, status in enumerate(key_statuses):
                matrix[i, j] = ind_status_lookup[ind][status] / row_total * 100

    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(matrix, aspect="auto", cmap="YlOrRd", vmin=0, vmax=100)
    ax.set_xticks(range(len(key_statuses)))
    ax.set_xticklabels(key_statuses)
    ax.set_yticks(range(len(top10_inds)))
    ax.set_yticklabels(top10_inds, fontsize=9)
    for i in range(len(top10_inds)):
        for j in range(len(key_statuses)):
            v = matrix[i, j]
            ax.text(j, i, f"{v:.0f}%", ha="center", va="center",
                    fontsize=9, color="black" if v > 50 else COLORS["text"])
    cb = plt.colorbar(im, ax=ax, fraction=0.03)
    cb.set_label("% of industry", color=COLORS["text"])
    plt.setp(cb.ax.yaxis.get_ticklabels(), color=COLORS["muted"])
    ax.set_title("Outcome by Industry (% in each status)")
    fig.tight_layout()
    show(fig)
