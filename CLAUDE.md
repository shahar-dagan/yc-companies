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

**API key:** Add `ANTHROPIC_API_KEY=sk-ant-...` to `.env` (loaded automatically via `python-dotenv`). The `.env` file is gitignored. If the key is missing, `chat.py` shows an error and calls `st.stop()`.

## Architecture

This project has three independent scripts sharing a data pipeline:

```
YC OSS API → ingest.py → yc_companies.db  (SQLite, structured queries)
                       → chroma_db/        (ChromaDB vectors, semantic search)

analyze.py → fetches API directly → output/*.png (12 matplotlib charts, headless)

chat.py → Claude claude-sonnet-4-6 (agentic loop)
            ├── search_companies tool → ChromaDB (semantic similarity)
            └── query_database tool  → SQLite    (structured SQL SELECT only)
```

## File-by-File Reference

### `ingest.py` (ETL pipeline, run once)

Fetches `~5,690` companies from `https://yc-oss.github.io/api/companies/all.json` and populates both stores. Run this before `chat.py`.

**Pipeline steps:**
1. HTTP GET the JSON API (single request, ~5k records)
2. Write SQLite (`yc_companies.db`) — drops and recreates the `companies` table
3. Write ChromaDB (`./chroma_db/`) — deletes and recreates the `yc_companies` collection

**Critical data-type helpers** (must be used when writing to either store):
- `safe_str(v, max_len=0)` — converts `None` → `""`, optionally truncates
- `safe_int(v, default=0)` — safe cast to int, returns `default` on failure
- `safe_bool_int(v)` — converts Python booleans/numbers → SQLite `0`/`1`

**ChromaDB metadata constraint:** All metadata values must be `str`, `int`, or `float`. Never pass `None` or `list`. This is enforced by always using the safe helpers and joining list fields with `", "`.

**Batch label parsing** (`parse_batch_label`):
```
SEASON_MAP = {"winter": "W", "spring": "S", "summer": "S", "fall": "F"}
"Winter 2022" → "W22"
"Summer 2021" → "S21"
"Fall 2025"   → "F25"
```
Spring and Summer both map to `"S"`. Batches range from S05 to S26.

**Country extraction** (`extract_country`): takes the last comma-separated segment of `all_locations`. Example: `"San Francisco, CA, USA"` → `"USA"`.

**API field name quirk:** The raw JSON uses `isHiring` (camelCase), but SQLite column and ChromaDB metadata use `is_hiring` (snake_case). `ingest.py` handles this mapping via `c.get("isHiring")`.

**ChromaDB document format** (the text embedded for vector search):
```
"<name> | <one_liner> | <long_description> | <industry> | <subindustry> | <tags> | <batch> | <all_locations> | <status>"
```
Fields are pipe-joined and empty strings are dropped.

**Indexes created on SQLite:** `idx_batch_label`, `idx_industry`, `idx_status`.

**Batch size for ChromaDB upserts:** 100 records per call.

---

### `chat.py` (Streamlit UI with agentic loop)

Implements multi-turn conversation with Claude using two retrieval tools.

**Configuration constants:**
- `DB_PATH = "yc_companies.db"`
- `CHROMA_DIR = "./chroma_db"`
- `MODEL = "claude-sonnet-4-6"`

**Cached resources** (via `@st.cache_resource`, initialized once per app lifetime):
- `get_chroma_collection()` — ChromaDB collection with `all-MiniLM-L6-v2` embedding function
- `get_db_connection()` — SQLite connection with `row_factory = sqlite3.Row`, `check_same_thread=False`
- `get_anthropic_client(api_key)` — Anthropic client, keyed by API key string

**Tool implementations:**
- `search_companies(query, n_results=10, where=None)` — ChromaDB `col.query()`. `n_results` capped at 30. Returns similarity as `1.0 - cosine_distance`, expressed as a percentage. `where` is passed directly to ChromaDB as a metadata filter dict.
- `query_database(sql)` — SQLite SELECT only. Safety check: first word of stripped SQL must be `"SELECT"` (case-insensitive); otherwise returns an error dict. Fetches at most 100 rows via `fetchmany(100)`.

**Agentic loop** (`run_agent`):
- Calls `client.messages.create()` in a `while True` loop
- Exits when `stop_reason == "end_turn"` — joins all `text` blocks as the final answer
- On `stop_reason == "tool_use"` — executes all tool-use blocks, appends `tool_result` messages, continues loop
- Any other stop reason exits returning whatever text was generated

**Session state format** (`st.session_state.messages`):
```python
# User turn
{"role": "user", "content": "<string>"}

# Assistant turn
{"role": "assistant", "text": "<final text>", "tool_calls": [...]}
# tool_calls entry: {"name": str, "inputs": dict, "result": str (preview, max 500 chars)}
```
Before sending to the API, the session history is reconstructed into plain `role/content` pairs. Assistant turns stored as `text` (not `content`) are converted back to `{"role": "assistant", "content": m["text"]}`. This means tool-call details from prior turns are **not** replayed to the API — only the final text is sent.

**Sidebar buttons** use `st.session_state.pending_input` to inject example questions into the chat input on the next render cycle.

---

### `analyze.py` (standalone charting, no DB dependency)

Fetches live data directly from the API and generates 12 PNG charts saved to `output/`. Uses `matplotlib.use("Agg")` for headless/server rendering. Does **not** depend on `yc_companies.db` or `chroma_db/`.

**Charts generated:**

| File | Description |
|------|-------------|
| `1_companies_per_batch.png` | Bar chart — companies per batch |
| `2_status_breakdown.png` | Donut chart — Active/Acquired/Inactive/Public |
| `3_top_industries.png` | Horizontal bar — top 15 industries |
| `4_industry_trends.png` | Stacked area — top 8 industries over time (3-batch rolling avg) |
| `5_top_tags.png` | Horizontal bar — top 30 tags |
| `6_team_size_trends.png` | Line — median & mean team size per batch |
| `7_hiring_rate.png` | Bar — % companies hiring per batch |
| `8_top_company_rate.png` | Bar — % flagged as "top company" per batch |
| `9_geographic_distribution.png` | Horizontal bar — top 20 countries |
| `10_industry_status_heatmap.png` | Heatmap — outcome (status %) by industry |
| `11_b2b_vs_b2c.png` | Line — B2B vs B2C tag count over time |
| `12_nonprofit_rate.png` | Bar — nonprofit % per batch |

**Color palette** (dark theme, YC-orange primary):
- `primary`: `#FF6600` (YC orange), `bg`: `#0D1117`, `panel`: `#161B22`
- `text`: `#E6EDF3`, `muted`: `#8B949E`, `grid`: `#21262D`
- `green`: `#3FB950`, `red`: `#F85149`, `blue`: `#58A6FF`, `purple`: `#D2A8FF`

**`analyze.py` field name note:** Uses raw `isHiring` (camelCase from JSON), unlike SQLite which stores it as `is_hiring`. The `parse_batch` function in `analyze.py` also returns a `batch_sort` tuple `(year, season_order)` used for chronological ordering — distinct from `ingest.py`'s simpler `parse_batch_label`.

---

## SQLite Schema

```sql
CREATE TABLE companies (
    id              INTEGER PRIMARY KEY,
    name            TEXT,
    slug            TEXT,
    batch           TEXT,        -- raw: "Winter 2022"
    batch_label     TEXT,        -- derived: "W22"
    industry        TEXT,
    subindustry     TEXT,
    status          TEXT,        -- Active | Acquired | Inactive | Public
    team_size       INTEGER,
    one_liner       TEXT,
    long_description TEXT,
    all_locations   TEXT,        -- "San Francisco, CA, USA"
    country         TEXT,        -- last segment of all_locations
    is_hiring       INTEGER,     -- 0 or 1
    top_company     INTEGER,     -- 0 or 1
    nonprofit       INTEGER,     -- 0 or 1
    stage           TEXT,
    tags            TEXT,        -- comma-separated: "B2B, SaaS, Healthcare"
    regions         TEXT,        -- comma-separated
    launched_at     INTEGER,     -- Unix timestamp
    website         TEXT,
    url             TEXT         -- YC profile URL
);
```

**SQL query conventions:**
- Tag matching: `tags LIKE '%B2B%'` (tags is comma-separated text, not an array)
- Boolean columns: `is_hiring = 1`, `top_company = 1`, `nonprofit = 1`
- Percentages: `CAST(SUM(is_hiring) AS FLOAT) / COUNT(*) * 100`
- Always use `LIMIT` (max 100 for display in the UI)
- `query_database` only permits SELECT statements — first word is checked

---

## Gitignored Artifacts

The following are generated at runtime and never committed:
- `chroma_db/` — ChromaDB vector store
- `yc_companies.db` — SQLite database
- `output/` — generated PNG charts
- `.env` — API keys
- `__pycache__/`, `*.pyc`, `*.pyo`, `.venv/`, `venv/`

---

## Key Conventions

1. **Run order:** `ingest.py` must complete before `chat.py` can serve queries. `analyze.py` is fully independent.
2. **No None in ChromaDB metadata:** Always use `safe_str`, `safe_int`, `safe_bool_int`. Lists must be joined to strings before storage.
3. **Booleans in SQLite are integers:** `0`/`1` — never `True`/`False`.
4. **Batch labels are short codes:** `W22`, `S21`, `F25`. Never use the raw `"Winter 2022"` form in SQL queries against `batch_label`.
5. **The `isHiring` / `is_hiring` rename:** The raw API uses `isHiring`; after ingestion, SQLite and ChromaDB both use `is_hiring`.
6. **`tags` and `regions` are stored as comma-separated strings** in both SQLite and ChromaDB (not arrays).
7. **SELECT-only database access:** `query_database` blocks any SQL that doesn't start with `SELECT`.
8. **Streamlit caching:** Resources are cached for the app lifetime. Re-running `ingest.py` while the Streamlit app is running will not be reflected until the app is restarted.
