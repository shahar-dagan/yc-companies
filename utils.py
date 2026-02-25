"""
utils.py — Shared constants, helpers, and cached resources for YC Companies app.
"""

import json
import sqlite3
import time
import threading
from pathlib import Path

import streamlit as st

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
