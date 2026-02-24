"""
YC Companies Trend Analysis
Data source: https://github.com/yc-oss/api
"""

import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from collections import Counter
from pathlib import Path
import re

# ── Config ─────────────────────────────────────────────────────────────────────
API_URL = "https://yc-oss.github.io/api/companies/all.json"
OUT_DIR = Path("output")
OUT_DIR.mkdir(exist_ok=True)

COLORS = {
    "primary": "#FF6600",
    "bg":      "#0D1117",
    "panel":   "#161B22",
    "text":    "#E6EDF3",
    "muted":   "#8B949E",
    "grid":    "#21262D",
    "green":   "#3FB950",
    "red":     "#F85149",
    "blue":    "#58A6FF",
    "purple":  "#D2A8FF",
}

SEASON_MAP = {"winter": ("W", 0), "spring": ("S", 1), "summer": ("S", 1), "fall": ("F", 2)}

def parse_batch(raw: str):
    """'Winter 2012' → ('W12', (2012, 0))  for sorting."""
    raw = str(raw).strip().lower()
    m = re.match(r"(\w+)\s+(\d{4})", raw)
    if not m:
        return None, (9999, 9)
    season_word, year = m.group(1), int(m.group(2))
    letter, order = SEASON_MAP.get(season_word, ("?", 9))
    label = f"{letter}{str(year)[2:]}"   # e.g. "W12"
    return label, (year, order)

# ── Fetch & clean ──────────────────────────────────────────────────────────────
print("Fetching data …")
resp = requests.get(API_URL, timeout=30)
resp.raise_for_status()
raw = resp.json()
print(f"  {len(raw):,} companies loaded")

df = pd.DataFrame(raw)
df["status"]    = df["status"].fillna("Unknown")
df["industry"]  = df["industry"].fillna("Unknown")
df["team_size"] = pd.to_numeric(df["team_size"], errors="coerce")
df["isHiring"]  = df["isHiring"].fillna(False).astype(bool)
df["top_company"] = df["top_company"].fillna(False).astype(bool)
df["nonprofit"]   = df["nonprofit"].fillna(False).astype(bool)

# Parse batch
df[["batch_label", "batch_sort"]] = pd.DataFrame(
    df["batch"].apply(parse_batch).tolist(), index=df.index
)
df = df.dropna(subset=["batch_label"])   # drop malformed batches

# Ordered batch list
batch_meta = (df.groupby("batch_label")["batch_sort"]
                .first()
                .sort_values()
                .reset_index())
batch_order = batch_meta["batch_label"].tolist()

# Parse country from all_locations (last comma-separated element)
def extract_country(loc):
    if not isinstance(loc, str) or not loc.strip():
        return "Unknown"
    return loc.split(",")[-1].strip()

df["country"] = df["all_locations"].apply(extract_country)

# Launched year
df["launch_year"] = pd.to_datetime(
    pd.to_numeric(df["launched_at"], errors="coerce"), unit="s", errors="coerce"
).dt.year

# ── Matplotlib style ───────────────────────────────────────────────────────────
plt.rcParams.update({
    "figure.facecolor": COLORS["bg"],
    "axes.facecolor":   COLORS["panel"],
    "axes.edgecolor":   COLORS["grid"],
    "axes.labelcolor":  COLORS["text"],
    "xtick.color":      COLORS["muted"],
    "ytick.color":      COLORS["muted"],
    "text.color":       COLORS["text"],
    "grid.color":       COLORS["grid"],
    "grid.linewidth":   0.6,
    "font.family":      "DejaVu Sans",
    "font.size":        10,
    "axes.titlesize":   13,
    "axes.titleweight": "bold",
    "axes.titlepad":    12,
})

def save(fig, name):
    path = OUT_DIR / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved → {path}")

def batch_x(ax, labels, step=1):
    """Set batch labels on x-axis, showing every `step` labels."""
    ticks = list(range(0, len(labels), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([labels[i] for i in ticks], rotation=70, ha="right", fontsize=7)

# Helper: reindex a series to full batch_order
def reindex_batches(s):
    return s.reindex(batch_order, fill_value=0)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Companies per batch
# ══════════════════════════════════════════════════════════════════════════════
print("\n[1] Companies per batch …")
batch_counts = reindex_batches(df.groupby("batch_label").size())

fig, ax = plt.subplots(figsize=(18, 5))
ax.bar(range(len(batch_order)), batch_counts.values,
       color=COLORS["primary"], alpha=0.85, width=0.7)
for i, (b, v) in enumerate(zip(batch_order[-6:], batch_counts.values[-6:])):
    idx = len(batch_order) - 6 + i
    ax.text(idx, v + 0.5, str(int(v)), ha="center", va="bottom", fontsize=8, color=COLORS["text"])
batch_x(ax, batch_order, step=2)
ax.set_ylabel("Companies")
ax.set_title("YC Companies per Batch")
ax.yaxis.grid(True); ax.set_axisbelow(True)
fig.tight_layout()
save(fig, "1_companies_per_batch.png")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Status donut
# ══════════════════════════════════════════════════════════════════════════════
print("[2] Status breakdown …")
status_counts = df["status"].value_counts()
sc_colors = {"Active": COLORS["green"], "Acquired": COLORS["blue"],
             "Inactive": COLORS["red"], "Public": COLORS["purple"], "Unknown": COLORS["muted"]}

fig, ax = plt.subplots(figsize=(7, 7))
wedges, texts, autotexts = ax.pie(
    status_counts.values,
    labels=status_counts.index,
    colors=[sc_colors.get(s, COLORS["muted"]) for s in status_counts.index],
    autopct="%1.1f%%",
    startangle=140,
    wedgeprops=dict(width=0.55, edgecolor=COLORS["bg"]),
    pctdistance=0.78,
)
for t in texts: t.set_color(COLORS["text"])
for t in autotexts: t.set_color(COLORS["bg"]); t.set_fontsize(9)
ax.set_title("Company Status Distribution")
save(fig, "2_status_breakdown.png")


# ══════════════════════════════════════════════════════════════════════════════
# 3. Top 15 industries
# ══════════════════════════════════════════════════════════════════════════════
print("[3] Industries …")
ind_counts = df["industry"].value_counts().drop("Unknown", errors="ignore").head(15)

fig, ax = plt.subplots(figsize=(10, 6))
bars = ax.barh(ind_counts.index[::-1], ind_counts.values[::-1], color=COLORS["primary"], alpha=0.85)
ax.xaxis.grid(True); ax.set_axisbelow(True)
for bar in bars:
    w = bar.get_width()
    ax.text(w + 3, bar.get_y() + bar.get_height() / 2, str(int(w)), va="center", fontsize=8)
ax.set_xlabel("Number of Companies")
ax.set_title("Top 15 Industries")
fig.tight_layout()
save(fig, "3_top_industries.png")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Industry share over time (stacked area, top 8)
# ══════════════════════════════════════════════════════════════════════════════
print("[4] Industry trends over time …")
top8 = df["industry"].value_counts().drop("Unknown", errors="ignore").head(8).index.tolist()

pivot = (df[df["industry"].isin(top8)]
         .groupby(["batch_label", "industry"])
         .size()
         .unstack(fill_value=0)
         .reindex(index=batch_order, columns=top8, fill_value=0))
pivot_smooth = pivot.rolling(window=3, min_periods=1, center=True).mean()

area_colors = [COLORS["primary"], COLORS["blue"], COLORS["green"], COLORS["purple"],
               "#F0883E", "#79C0FF", "#56D364", "#FF7B72"]

fig, ax = plt.subplots(figsize=(18, 6))
ax.stackplot(range(len(batch_order)),
             [pivot_smooth[ind].values for ind in top8],
             labels=top8, colors=area_colors, alpha=0.85)
batch_x(ax, batch_order, step=2)
ax.set_ylabel("Companies (3-batch rolling avg)")
ax.set_title("Industry Composition Over Time (Top 8 Industries, stacked)")
ax.legend(loc="upper left", fontsize=8, framealpha=0.3,
          facecolor=COLORS["panel"], edgecolor=COLORS["grid"])
ax.yaxis.grid(True); ax.set_axisbelow(True)
fig.tight_layout()
save(fig, "4_industry_trends.png")


# ══════════════════════════════════════════════════════════════════════════════
# 5. Top 30 tags
# ══════════════════════════════════════════════════════════════════════════════
print("[5] Top tags …")
all_tags = Counter()
for tags in df["tags"].dropna():
    if isinstance(tags, list):
        all_tags.update(tags)
top_tags = pd.Series(dict(all_tags.most_common(30)))

fig, ax = plt.subplots(figsize=(12, 8))
bars = ax.barh(top_tags.index[::-1], top_tags.values[::-1], color=COLORS["blue"], alpha=0.85)
ax.xaxis.grid(True); ax.set_axisbelow(True)
for bar in bars:
    w = bar.get_width()
    ax.text(w + 2, bar.get_y() + bar.get_height() / 2, str(int(w)), va="center", fontsize=8)
ax.set_xlabel("Number of Companies")
ax.set_title("Top 30 Tags Across All YC Companies")
fig.tight_layout()
save(fig, "5_top_tags.png")


# ══════════════════════════════════════════════════════════════════════════════
# 6. Team size per batch (median line)
# ══════════════════════════════════════════════════════════════════════════════
print("[6] Team size trends …")
ts = (df.groupby("batch_label")["team_size"]
        .agg(["median", "mean", "count"])
        .reindex(batch_order))
ts = ts[ts["count"] >= 5]
valid = ts.index.tolist()
valid_pos = [batch_order.index(b) for b in valid]

fig, ax = plt.subplots(figsize=(18, 5))
ax.plot(valid_pos, ts["median"].values, color=COLORS["primary"], linewidth=2.5, label="Median")
ax.plot(valid_pos, ts["mean"].values,   color=COLORS["blue"],    linewidth=1.5,
        linestyle="--", alpha=0.75, label="Mean")
ax.fill_between(valid_pos, ts["median"].values * 0.7, ts["median"].values * 1.3,
                alpha=0.12, color=COLORS["primary"])
ax.set_xlim(0, len(batch_order) - 1)
batch_x(ax, batch_order, step=2)
ax.set_ylabel("Employees")
ax.set_title("Team Size per Batch (Median & Mean)")
ax.legend(fontsize=9, framealpha=0.3)
ax.yaxis.grid(True); ax.set_axisbelow(True)
fig.tight_layout()
save(fig, "6_team_size_trends.png")


# ══════════════════════════════════════════════════════════════════════════════
# 7. Hiring rate per batch
# ══════════════════════════════════════════════════════════════════════════════
print("[7] Hiring rate …")
hr = (df.groupby("batch_label")["isHiring"]
        .agg(["sum", "count"])
        .reindex(batch_order))
hr["rate"] = hr["sum"] / hr["count"].replace(0, float("nan")) * 100
hr = hr[hr["count"] >= 5]
valid = hr.index.tolist()
valid_pos = [batch_order.index(b) for b in valid]

fig, ax = plt.subplots(figsize=(18, 4))
ax.bar(valid_pos, hr.loc[valid, "rate"].values, color=COLORS["green"], alpha=0.85, width=0.7)
avg = hr["rate"].mean()
ax.axhline(avg, color=COLORS["primary"], linewidth=1.5, linestyle="--",
           label=f"Overall avg {avg:.0f}%")
ax.set_xlim(-1, len(batch_order))
batch_x(ax, batch_order, step=2)
ax.set_ylabel("% Companies Hiring")
ax.set_title("Hiring Rate per Batch")
ax.legend(fontsize=9, framealpha=0.3)
ax.yaxis.grid(True); ax.set_axisbelow(True)
fig.tight_layout()
save(fig, "7_hiring_rate.png")


# ══════════════════════════════════════════════════════════════════════════════
# 8. YC "top company" rate per batch
# ══════════════════════════════════════════════════════════════════════════════
print("[8] Top-company rate per batch …")
tc = (df.groupby("batch_label")["top_company"]
        .agg(["sum", "count"])
        .reindex(batch_order))
tc["rate"] = tc["sum"] / tc["count"].replace(0, float("nan")) * 100
tc = tc[tc["count"] >= 5]
valid = tc.index.tolist()
valid_pos = [batch_order.index(b) for b in valid]

fig, ax = plt.subplots(figsize=(18, 4))
ax.bar(valid_pos, tc.loc[valid, "rate"].values, color=COLORS["purple"], alpha=0.85, width=0.7)
ax.set_xlim(-1, len(batch_order))
batch_x(ax, batch_order, step=2)
ax.set_ylabel("% Flagged as Top Company")
ax.set_title("YC 'Top Company' Flag Rate per Batch")
ax.yaxis.grid(True); ax.set_axisbelow(True)
fig.tight_layout()
save(fig, "8_top_company_rate.png")


# ══════════════════════════════════════════════════════════════════════════════
# 9. Top 20 countries
# ══════════════════════════════════════════════════════════════════════════════
print("[9] Geographic distribution …")
country_counts = (df["country"]
                  .replace("", "Unknown")
                  .value_counts()
                  .drop("Unknown", errors="ignore")
                  .head(20))

fig, ax = plt.subplots(figsize=(10, 7))
bars = ax.barh(country_counts.index[::-1], country_counts.values[::-1],
               color=COLORS["purple"], alpha=0.85)
ax.xaxis.grid(True); ax.set_axisbelow(True)
for bar in bars:
    w = bar.get_width()
    ax.text(w + 3, bar.get_y() + bar.get_height() / 2, str(int(w)), va="center", fontsize=8)
ax.set_xlabel("Number of Companies")
ax.set_title("Top 20 Countries by Company Count")
fig.tight_layout()
save(fig, "9_geographic_distribution.png")


# ══════════════════════════════════════════════════════════════════════════════
# 10. Industry × Status heatmap
# ══════════════════════════════════════════════════════════════════════════════
print("[10] Industry × Status heatmap …")
top10_ind = df["industry"].value_counts().drop("Unknown", errors="ignore").head(10).index.tolist()
key_statuses = ["Active", "Acquired", "Inactive", "Public"]

hm_df = df[df["industry"].isin(top10_ind) & df["status"].isin(key_statuses)]
hm = (hm_df.groupby(["industry", "status"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=key_statuses, fill_value=0))
hm_pct = hm.div(hm.sum(axis=1), axis=0) * 100

fig, ax = plt.subplots(figsize=(9, 7))
im = ax.imshow(hm_pct.values, aspect="auto", cmap="YlOrRd", vmin=0, vmax=100)
ax.set_xticks(range(len(key_statuses))); ax.set_xticklabels(key_statuses)
ax.set_yticks(range(len(hm_pct.index))); ax.set_yticklabels(hm_pct.index, fontsize=9)
for i in range(len(hm_pct.index)):
    for j in range(len(key_statuses)):
        v = hm_pct.values[i, j]
        ax.text(j, i, f"{v:.0f}%", ha="center", va="center",
                fontsize=9, color="black" if v > 50 else COLORS["text"])
cb = plt.colorbar(im, ax=ax, fraction=0.03)
cb.set_label("% of industry", color=COLORS["text"])
plt.setp(cb.ax.yaxis.get_ticklabels(), color=COLORS["muted"])
ax.set_title("Outcome by Industry (% of companies in each status)")
fig.tight_layout()
save(fig, "10_industry_status_heatmap.png")


# ══════════════════════════════════════════════════════════════════════════════
# 11. B2B vs B2C tag ratio over time
# ══════════════════════════════════════════════════════════════════════════════
print("[11] B2B vs B2C over time …")
def has_tag(tags_list, tag):
    if isinstance(tags_list, list):
        return any(t.lower() == tag.lower() for t in tags_list)
    return False

df["is_b2b"] = df["tags"].apply(lambda t: has_tag(t, "B2B"))
df["is_b2c"] = df["tags"].apply(lambda t: has_tag(t, "B2C"))

b2_pivot = (df.groupby("batch_label")[["is_b2b", "is_b2c"]]
              .sum()
              .reindex(batch_order, fill_value=0)
              .rolling(window=3, min_periods=1, center=True)
              .mean())

fig, ax = plt.subplots(figsize=(18, 5))
ax.plot(range(len(batch_order)), b2_pivot["is_b2b"].values,
        color=COLORS["blue"], linewidth=2, label="B2B")
ax.plot(range(len(batch_order)), b2_pivot["is_b2c"].values,
        color=COLORS["green"], linewidth=2, label="B2C")
ax.fill_between(range(len(batch_order)), b2_pivot["is_b2b"].values, alpha=0.15, color=COLORS["blue"])
ax.fill_between(range(len(batch_order)), b2_pivot["is_b2c"].values, alpha=0.15, color=COLORS["green"])
batch_x(ax, batch_order, step=2)
ax.set_ylabel("Companies (3-batch rolling avg)")
ax.set_title("B2B vs B2C Tag Count Over Time")
ax.legend(fontsize=10, framealpha=0.3)
ax.yaxis.grid(True); ax.set_axisbelow(True)
fig.tight_layout()
save(fig, "11_b2b_vs_b2c.png")


# ══════════════════════════════════════════════════════════════════════════════
# 12. Nonprofit rate per batch
# ══════════════════════════════════════════════════════════════════════════════
print("[12] Nonprofit rate …")
np_rate = (df.groupby("batch_label")["nonprofit"]
             .agg(["sum", "count"])
             .reindex(batch_order))
np_rate["rate"] = np_rate["sum"] / np_rate["count"].replace(0, float("nan")) * 100
np_rate = np_rate[np_rate["count"] >= 5]
valid = np_rate.index.tolist()
valid_pos = [batch_order.index(b) for b in valid]

fig, ax = plt.subplots(figsize=(18, 4))
ax.bar(valid_pos, np_rate.loc[valid, "rate"].values,
       color=COLORS["green"], alpha=0.85, width=0.7)
ax.set_xlim(-1, len(batch_order))
batch_x(ax, batch_order, step=2)
ax.set_ylabel("% Nonprofit")
ax.set_title("Nonprofit Rate per Batch")
ax.yaxis.grid(True); ax.set_axisbelow(True)
fig.tight_layout()
save(fig, "12_nonprofit_rate.png")


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
largest_batch = batch_counts.idxmax() if len(batch_counts) > 0 else "N/A"
largest_count = int(batch_counts.max()) if len(batch_counts) > 0 else 0

print("\n" + "═" * 60)
print("  YC COMPANIES — QUICK STATS")
print("═" * 60)
print(f"  Total companies:        {len(df):,}")
print(f"  Unique batches:         {len(batch_order)}")
print(f"  Oldest batch:           {batch_order[0] if batch_order else 'N/A'}")
print(f"  Newest batch:           {batch_order[-1] if batch_order else 'N/A'}")
print(f"  Largest batch:          {largest_batch} ({largest_count} cos)")
print(f"  Active:                 {(df['status']=='Active').sum():,}")
print(f"  Acquired:               {(df['status']=='Acquired').sum():,}")
print(f"  Public:                 {(df['status']=='Public').sum():,}")
print(f"  Inactive:               {(df['status']=='Inactive').sum():,}")
print(f"  Currently hiring:       {df['isHiring'].sum():,}")
print(f"  Top company:            {df['top_company'].sum():,}")
print(f"  Nonprofit:              {df['nonprofit'].sum():,}")
print(f"  Median team size:       {df['team_size'].median():.0f}")
print(f"  B2B companies:          {df['is_b2b'].sum():,}")
print(f"  B2C companies:          {df['is_b2c'].sum():,}")
print(f"  Top country:            {country_counts.idxmax()} ({country_counts.max():,})")
print("═" * 60)
print(f"\n  All {len(list(OUT_DIR.glob('*.png')))} charts saved to ./{OUT_DIR}/")
