"""
ingest.py — One-time setup: populate SQLite + ChromaDB from YC companies API.

Usage:
    pip install chromadb sentence-transformers requests
    python ingest.py
"""

import re
import sqlite3
from pathlib import Path

import requests
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# ── Config ────────────────────────────────────────────────────────────────────
API_URL    = "https://yc-oss.github.io/api/companies/all.json"
DB_PATH    = str(Path(__file__).parent / "yc_companies.db")
CHROMA_DIR = str(Path(__file__).parent / "chroma_db")
BATCH_SIZE = 100

SEASON_MAP = {"winter": "W", "spring": "S", "summer": "S", "fall": "F"}


# ── Helper functions (module-level) ───────────────────────────────────────────
def parse_batch_label(raw: str) -> str:
    """'Winter 2022' → 'W22'"""
    raw = str(raw).strip().lower()
    m = re.match(r"(\w+)\s+(\d{4})", raw)
    if not m:
        return ""
    letter = SEASON_MAP.get(m.group(1), "?")
    year_short = m.group(2)[2:]  # last 2 digits
    return f"{letter}{year_short}"


def extract_country(loc: str) -> str:
    if not isinstance(loc, str) or not loc.strip():
        return ""
    return loc.split(",")[-1].strip()


def safe_str(v, max_len: int = 0) -> str:
    if v is None:
        return ""
    s = str(v)
    if max_len and len(s) > max_len:
        return s[:max_len]
    return s


def safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def safe_bool_int(v) -> int:
    """Store booleans as 0/1 for SQLite."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(bool(v))
    return 0


def build_document(c: dict) -> str:
    parts = [
        safe_str(c.get("name")),
        safe_str(c.get("one_liner")),
        safe_str(c.get("long_description")),
        safe_str(c.get("industry")),
        safe_str(c.get("subindustry")),
    ]
    tags_raw = c.get("tags") or []
    if isinstance(tags_raw, list):
        parts.append(", ".join(tags_raw))
    parts.append(safe_str(c.get("batch")))
    parts.append(safe_str(c.get("all_locations")))
    parts.append(safe_str(c.get("status")))
    return " | ".join(p for p in parts if p)


def build_metadata(c: dict) -> dict:
    """All values must be str, int, or float — no None, no list."""
    tags_raw = c.get("tags") or []
    tags_str = ", ".join(tags_raw) if isinstance(tags_raw, list) else safe_str(tags_raw)

    return {
        "id":            safe_int(c.get("id")),
        "name":          safe_str(c.get("name")),
        "batch":         safe_str(c.get("batch")),
        "batch_label":   parse_batch_label(safe_str(c.get("batch"))),
        "industry":      safe_str(c.get("industry")),
        "status":        safe_str(c.get("status")),
        "is_hiring":     safe_bool_int(c.get("isHiring")),
        "top_company":   safe_bool_int(c.get("top_company")),
        "team_size":     safe_int(c.get("team_size")),
        "country":       extract_country(safe_str(c.get("all_locations"))),
        "all_locations": safe_str(c.get("all_locations"), max_len=500),
        "tags":          tags_str,
        "website":       safe_str(c.get("website")),
    }


# ── Main ingest function ──────────────────────────────────────────────────────
def run_ingest(db_path: str = DB_PATH, chroma_dir: str = CHROMA_DIR):
    import chromadb

    # ── 1. Fetch data ─────────────────────────────────────────────────────────
    print("Fetching data from API …")
    resp = requests.get(API_URL, timeout=60)
    resp.raise_for_status()
    companies = resp.json()
    print(f"  {len(companies):,} companies loaded")

    # ── 2. SQLite ─────────────────────────────────────────────────────────────
    print("\nBuilding SQLite database …")
    db = sqlite3.connect(db_path)
    cur = db.cursor()

    cur.executescript("""
DROP TABLE IF EXISTS companies;

CREATE TABLE companies (
    id              INTEGER PRIMARY KEY,
    name            TEXT,
    slug            TEXT,
    batch           TEXT,
    batch_label     TEXT,
    industry        TEXT,
    subindustry     TEXT,
    status          TEXT,
    team_size       INTEGER,
    one_liner       TEXT,
    long_description TEXT,
    all_locations   TEXT,
    country         TEXT,
    is_hiring       INTEGER,
    top_company     INTEGER,
    nonprofit       INTEGER,
    stage           TEXT,
    tags            TEXT,
    regions         TEXT,
    launched_at     INTEGER,
    website         TEXT,
    url             TEXT
);

CREATE INDEX IF NOT EXISTS idx_batch_label ON companies(batch_label);
CREATE INDEX IF NOT EXISTS idx_industry    ON companies(industry);
CREATE INDEX IF NOT EXISTS idx_status      ON companies(status);
""")

    rows = []
    for c in companies:
        tags_raw    = c.get("tags") or []
        tags_str    = ", ".join(tags_raw) if isinstance(tags_raw, list) else safe_str(tags_raw)
        regions_raw = c.get("regions") or []
        regions_str = ", ".join(regions_raw) if isinstance(regions_raw, list) else safe_str(regions_raw)

        rows.append((
            safe_int(c.get("id")),
            safe_str(c.get("name")),
            safe_str(c.get("slug")),
            safe_str(c.get("batch")),
            parse_batch_label(safe_str(c.get("batch"))),
            safe_str(c.get("industry")),
            safe_str(c.get("subindustry")),
            safe_str(c.get("status")),
            safe_int(c.get("team_size")),
            safe_str(c.get("one_liner")),
            safe_str(c.get("long_description")),
            safe_str(c.get("all_locations")),
            extract_country(safe_str(c.get("all_locations"))),
            safe_bool_int(c.get("isHiring")),
            safe_bool_int(c.get("top_company")),
            safe_bool_int(c.get("nonprofit")),
            safe_str(c.get("stage")),
            tags_str,
            regions_str,
            safe_int(c.get("launched_at")),
            safe_str(c.get("website")),
            safe_str(c.get("url")),
        ))

    cur.executemany("""
INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
""", rows)
    db.commit()
    db.close()
    print(f"  SQLite: {len(rows):,} rows written → {db_path}")

    # ── 3. ChromaDB ───────────────────────────────────────────────────────────
    print("\nBuilding ChromaDB vector store …")
    client = chromadb.PersistentClient(path=chroma_dir)

    try:
        client.delete_collection("yc_companies")
        print("  Deleted existing 'yc_companies' collection")
    except Exception:
        pass

    ef  = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    col = client.create_collection("yc_companies", embedding_function=ef)

    total    = len(companies)
    inserted = 0

    for start in range(0, total, BATCH_SIZE):
        batch = companies[start : start + BATCH_SIZE]

        ids       = []
        documents = []
        metadatas = []

        for c in batch:
            cid = str(safe_int(c.get("id")) or hash(safe_str(c.get("slug"))))
            ids.append(cid)
            documents.append(build_document(c))
            metadatas.append(build_metadata(c))

        col.upsert(ids=ids, documents=documents, metadatas=metadatas)
        inserted += len(batch)
        pct = inserted / total * 100
        print(f"  ChromaDB: {inserted:,}/{total:,} ({pct:.0f}%)", end="\r", flush=True)

    print(f"\n  ChromaDB: {inserted:,} documents upserted → {chroma_dir}")
    print("\nDone! Run:  streamlit run chat.py")


if __name__ == "__main__":
    run_ingest()
