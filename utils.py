"""
utils.py — Shared constants, helpers, and cached resources for YC Companies app.
"""

import json
import re
import sqlite3
import time
import threading
from pathlib import Path

import requests
import streamlit as st

# ── Model ─────────────────────────────────────────────────────────────────────
MODEL = "claude-sonnet-4-6"

# ── Paths (absolute, so pages/ subdirectory resolves correctly) ───────────────
_ROOT      = Path(__file__).parent
DB_PATH    = _ROOT / "yc_companies.db"
CHROMA_DIR = str(_ROOT / "chroma_db")

# ── Dark-theme palette ────────────────────────────────────────────────────────
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


# ── Live YC API fetch (shared by pages/analyze.py) ───────────────────────────
_YC_API_URL  = "https://yc-oss.github.io/api/companies/all.json"
_SEASON_MAP  = {"winter": ("W", 0), "spring": ("S", 1), "summer": ("S", 1), "fall": ("F", 2)}


def _parse_batch_label(raw: str):
    m = re.match(r"(\w+)\s+(\d{4})", str(raw).strip().lower())
    if not m:
        return None, (9999, 9)
    letter, order = _SEASON_MAP.get(m.group(1), ("?", 9))
    return f"{letter}{m.group(2)[2:]}", (int(m.group(2)), order)


def _extract_country(loc: str) -> str:
    if not isinstance(loc, str) or not loc.strip():
        return ""
    return loc.split(",")[-1].strip()


@st.cache_data(ttl=3600)
def fetch_yc_data() -> tuple[list[dict], list[str]]:
    """
    Fetch and normalise live data from the YC OSS API.
    Returns (companies, sorted_batch_labels).
    Tags are always a list[str]; is_hiring / top_company / nonprofit are bool.
    Cached for 1 hour.
    """
    resp = requests.get(_YC_API_URL, timeout=30)
    resp.raise_for_status()

    companies = []
    batch_sort: dict[str, tuple] = {}
    for c in resp.json():
        label, sort_key = _parse_batch_label(c.get("batch", ""))
        if not label or label.startswith("?"):
            continue
        tags = c.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        companies.append({
            "batch_label": label,
            "status":      c.get("status") or "Unknown",
            "industry":    c.get("industry") or "Unknown",
            "team_size":   c.get("team_size"),
            "is_hiring":   bool(c.get("isHiring") or c.get("is_hiring") or False),
            "top_company": bool(c.get("top_company") or False),
            "nonprofit":   bool(c.get("nonprofit") or False),
            "tags":        tags,
            "country":     _extract_country(c.get("all_locations", "")),
        })
        batch_sort[label] = sort_key

    sorted_batches = sorted(batch_sort, key=lambda b: batch_sort[b])
    return companies, sorted_batches


def apply_dark_theme():
    """Apply dark matplotlib rcParams using the COLORS palette."""
    import matplotlib.pyplot as plt
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


# ── Cached DB connection ──────────────────────────────────────────────────────
@st.cache_resource
def get_db_connection():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ── Conversations table ───────────────────────────────────────────────────────
def setup_conversations_table(conn):
    """Create conversations table and index if they don't exist (idempotent)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT    NOT NULL,
            role            TEXT    NOT NULL,
            content         TEXT    NOT NULL,
            tool_calls_json TEXT,
            created_at      INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_conv_session
            ON conversations(session_id, created_at);
    """)
    conn.commit()


def load_session_messages(conn, session_id: str) -> list:
    """Load stored messages for a session and return them in chat.py's format."""
    cur = conn.execute(
        "SELECT role, content, tool_calls_json FROM conversations "
        "WHERE session_id = ? ORDER BY created_at, id",
        (session_id,),
    )
    messages = []
    for row in cur.fetchall():
        role, content, tc_json = row[0], row[1], row[2]
        if role == "user":
            messages.append({"role": "user", "content": content})
        else:
            tool_calls = json.loads(tc_json) if tc_json else []
            messages.append({
                "role":       "assistant",
                "text":       content,
                "tool_calls": tool_calls,
            })
    return messages


def save_message(conn, session_id: str, role: str, content: str, tool_calls=None):
    """Persist a single message immediately (called on every turn)."""
    tc_json = json.dumps(tool_calls) if tool_calls else None
    conn.execute(
        "INSERT INTO conversations "
        "(session_id, role, content, tool_calls_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, tc_json, int(time.time())),
    )
    conn.commit()


def delete_session(conn, session_id: str):
    """Delete all messages for a session."""
    conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
    conn.commit()


# ── Research table ────────────────────────────────────────────────────────────
def setup_research_table(conn):
    """Create company_research table and indexes if they don't exist (idempotent)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS company_research (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id           TEXT    NOT NULL UNIQUE,
            company_id       INTEGER NOT NULL,
            company_name     TEXT    NOT NULL,
            triggered_at     INTEGER NOT NULL,
            completed_at     INTEGER,
            status           TEXT    NOT NULL DEFAULT 'running',
            news_result      TEXT,
            market_result    TEXT,
            funding_result   TEXT,
            community_result TEXT,
            synthesis_result TEXT,
            error_detail     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_research_company
            ON company_research(company_id, triggered_at);
        CREATE INDEX IF NOT EXISTS idx_research_run
            ON company_research(run_id);
    """)
    conn.commit()


def insert_research_run(conn, run_id: str, company_id: int, company_name: str) -> None:
    """Insert a new research run with status='running'."""
    conn.execute(
        "INSERT INTO company_research "
        "(run_id, company_id, company_name, triggered_at, status) "
        "VALUES (?, ?, ?, ?, 'running')",
        (run_id, company_id, company_name, int(time.time())),
    )
    conn.commit()


def get_research_run(conn, run_id: str) -> dict | None:
    """Fetch a single research run row as a dict."""
    cur = conn.execute(
        "SELECT * FROM company_research WHERE run_id = ?", (run_id,)
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def list_research_runs(conn, company_id: int, limit: int = 10) -> list:
    """Return the N most recent runs for a company."""
    cur = conn.execute(
        "SELECT run_id, company_name, triggered_at, completed_at, status, error_detail "
        "FROM company_research "
        "WHERE company_id = ? ORDER BY triggered_at DESC LIMIT ?",
        (company_id, limit),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Auto-refresh ──────────────────────────────────────────────────────────────
REFRESH_INTERVAL_DAYS = 7


def check_and_refresh_db() -> bool:
    """
    If yc_companies.db is older than REFRESH_INTERVAL_DAYS, launch a background
    thread to re-run ingest and return True. Otherwise return False.
    """
    if not DB_PATH.exists():
        return False

    age_days = (time.time() - DB_PATH.stat().st_mtime) / 86400
    if age_days < REFRESH_INTERVAL_DAYS:
        return False

    def _run():
        try:
            from ingest import run_ingest
            run_ingest(db_path=str(DB_PATH), chroma_dir=CHROMA_DIR)
            st.cache_resource.clear()
        except Exception as e:
            print(f"[auto-refresh] Error during background ingest: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return True
