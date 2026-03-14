# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# One-time setup: fetch data, populate SQLite + ChromaDB (~2 min for embeddings)
python3 ingest.py

# Static analysis: fetch live data and generate 12 charts to output/
python3 analyze.py

# Launch the chat UI
streamlit run chat.py
```

**Dependencies:** `pip install chromadb sentence-transformers requests pandas matplotlib anthropic streamlit python-dotenv`

**API key:** Add `ANTHROPIC_API_KEY=sk-ant-...` to `.env` (loaded automatically via `python-dotenv`). The `.env` file is gitignored.

## Architecture

This project has three independent scripts sharing a data pipeline:

```
YC API → ingest.py → yc_companies.db (SQLite)
                   → chroma_db/      (ChromaDB vectors)

chat.py → Claude claude-sonnet-4-6 (agentic loop)
            ├── search_companies tool → ChromaDB (semantic)
            └── query_database tool  → SQLite    (structured SQL)
```

### `ingest.py` (ETL, run once)
Fetches ~5,690 companies from the YC OSS API and populates:
- **SQLite** (`yc_companies.db`): structured rows with all fields
- **ChromaDB** (`chroma_db/`): vector embeddings using `all-MiniLM-L6-v2` (SentenceTransformer), batched in groups of 100

Critical constraints:
- SQLite stores booleans as integers (0/1) — columns `is_hiring`, `top_company`, `nonprofit`
- ChromaDB metadata values must be `str`, `int`, or `float` — never `None` or `list`. Use `safe_str`/`safe_int`/`safe_bool_int` helpers.
- `batch_label` is a derived field: `"Winter 2022"` → `"W22"`, `"Summer 2021"` → `"S21"`, `"Fall 2025"` → `"F25"`

### `chat.py` (Streamlit UI)
Implements a multi-turn agentic loop (`run_agent`) that calls Claude repeatedly until `stop_reason == "end_turn"`. On each `tool_use` stop, it dispatches to:
- `search_companies(query, n_results, where)` — ChromaDB vector search with optional metadata filter
- `query_database(sql)` — SQLite SELECT-only execution (first word checked for safety)

Resources are cached via `@st.cache_resource`: ChromaDB collection, SQLite connection, and Anthropic client (keyed by API key).

The session history stored in `st.session_state.messages` uses a custom format (`text` + `tool_calls` for assistant turns) that is reconstructed into plain `role/content` pairs before being sent to the API.

### `analyze.py` (standalone analysis)
Fetches live data, parses it with pandas, and saves 12 matplotlib charts to `output/`. Uses `matplotlib.use("Agg")` for headless rendering. Does not depend on the SQLite/ChromaDB stores.

## SQLite Schema Notes

```sql
-- Key columns and their quirks
batch_label  TEXT   -- W22, S21, F25 (not the raw batch string)
tags         TEXT   -- comma-separated string, not an array
is_hiring    INTEGER -- 0 or 1
top_company  INTEGER -- 0 or 1
nonprofit    INTEGER -- 0 or 1
country      TEXT   -- parsed from last segment of all_locations
```

Tag queries: `tags LIKE '%B2B%'`
Percentage queries: `CAST(SUM(is_hiring) AS FLOAT) / COUNT(*) * 100`

**Tags format discrepancy:** SQLite stores `tags` as a comma-separated `TEXT` string (from ingest.py). The live YC OSS API returns `tags` as a JSON array (`list[str]`). `fetch_yc_data()` in `utils.py` always normalises to `list[str]`. Never assume they match without converting.
