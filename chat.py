"""
chat.py — Streamlit chat interface for YC companies dataset.
Powered by Claude claude-sonnet-4-6 with hybrid retrieval (ChromaDB + SQLite).

Usage:
    streamlit run chat.py
"""

import json
import os
import sqlite3
from pathlib import Path

import anthropic
import chromadb
import streamlit as st
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# ── Config ───────────────────────────────────────────────────────────────────
DB_PATH    = "yc_companies.db"
CHROMA_DIR = "./chroma_db"
MODEL      = "claude-sonnet-4-6"

EXAMPLE_QUESTIONS = [
    "How many healthcare companies are active?",
    "Find companies doing AI for drug discovery",
    "Which batch had the most companies?",
    "Show me YC unicorns (top companies) from W21",
    "What are the most common tags across all companies?",
    "Find fintech companies currently hiring",
    "How many companies are from India?",
    "Compare active vs inactive rates by industry",
]

SYSTEM_PROMPT = """You are a knowledgeable assistant with access to a comprehensive database of Y Combinator (YC) companies — 5,689 companies from batches S05 through S26.

## Available Tools

You have two tools to retrieve information:

### 1. `search_companies` (Semantic / Vector Search)
Use for concept-based queries: "find companies doing X", "companies working on Y", natural-language descriptions.
- Returns ranked results with similarity scores
- Best for: discovering companies by what they do, finding similar companies

### 2. `query_database` (SQLite Analytics)
Use for structured queries: counts, aggregations, filtering, comparisons, rankings.
- Best for: "how many", "which batch", GROUP BY, top-N lists, filtering by status/country/industry

## Database Schema

Table: `companies`

| Column           | Type    | Description |
|------------------|---------|-------------|
| id               | INTEGER | Unique company ID |
| name             | TEXT    | Company name |
| slug             | TEXT    | URL slug |
| batch            | TEXT    | Raw batch string, e.g. "Winter 2022" |
| batch_label      | TEXT    | Short format: W22, S21, F25 (W=Winter, S=Summer/Spring, F=Fall) |
| industry         | TEXT    | Primary industry |
| subindustry      | TEXT    | Sub-industry |
| status           | TEXT    | Active, Acquired, Inactive, Public |
| team_size        | INTEGER | Number of employees |
| one_liner        | TEXT    | Short description |
| long_description | TEXT    | Full description |
| all_locations    | TEXT    | Location string, e.g. "San Francisco, CA, USA" |
| country          | TEXT    | Parsed from last part of all_locations |
| is_hiring        | INTEGER | 1 if currently hiring, 0 otherwise |
| top_company      | INTEGER | 1 if flagged as YC top company |
| nonprofit        | INTEGER | 1 if nonprofit |
| stage            | TEXT    | Funding stage |
| tags             | TEXT    | Comma-separated tags (B2B, B2C, SaaS, Healthcare, etc.) |
| regions          | TEXT    | Comma-separated regions |
| launched_at      | INTEGER | Unix timestamp of launch |
| website          | TEXT    | Company website URL |
| url              | TEXT    | YC profile URL |

## Batch Label Format
- W = Winter, S = Summer (also Spring), F = Fall
- Two-digit year: W22 = Winter 2022, S21 = Summer 2021, F25 = Fall 2025
- Batches range from S05 to S26

## SQL Tips
- Tag matching: `tags LIKE '%B2B%'` (tags is comma-separated text)
- Boolean columns (is_hiring, top_company, nonprofit): use `= 1` or `= 0`
- Always use LIMIT (max 100 rows for display)
- For % calculations: `CAST(SUM(is_hiring) AS FLOAT) / COUNT(*) * 100`

## Strategy
- For "find companies doing X" → use `search_companies`
- For "how many X" / counts / aggregations → use `query_database`
- You can call both tools if needed (e.g., semantic search then verify with SQL)
- Always provide clear, well-formatted answers with relevant numbers and company names
- When listing companies, include their batch, status, and website when relevant
"""

# ── Tool definitions ──────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "search_companies",
        "description": (
            "Semantic vector search over YC companies. Returns companies ranked by "
            "relevance to the query. Use this for concept-based lookups like "
            "'companies doing AI for healthcare' or 'find climate tech startups'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query describing what kind of companies to find.",
                },
                "n_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 10, max 30).",
                    "default": 10,
                },
                "where": {
                    "type": "object",
                    "description": (
                        "Optional ChromaDB metadata filter dict. "
                        "Example: {\"status\": \"Active\"} or {\"is_hiring\": 1}. "
                        "Supported fields: status, industry, batch_label, is_hiring, top_company, country."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_database",
        "description": (
            "Execute a SELECT SQL query against the SQLite companies database. "
            "Use this for aggregations, counts, filtering, and structured analytics. "
            "Only SELECT statements are allowed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A valid SQLite SELECT statement. Always include LIMIT (max 100).",
                },
            },
            "required": ["sql"],
        },
    },
]


# ── Cached resources ──────────────────────────────────────────────────────────
@st.cache_resource
def get_chroma_collection():
    ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_collection("yc_companies", embedding_function=ef)


@st.cache_resource
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_resource
def get_anthropic_client(api_key: str):
    return anthropic.Anthropic(api_key=api_key)


# ── Tool implementations ──────────────────────────────────────────────────────
def search_companies(query: str, n_results: int = 10, where: dict = None) -> dict:
    col = get_chroma_collection()
    n_results = min(int(n_results), 30)

    kwargs = {"query_texts": [query], "n_results": n_results}
    if where:
        kwargs["where"] = where

    results = col.query(**kwargs)

    companies = []
    ids       = results["ids"][0]
    distances = results["distances"][0]
    metadatas = results["metadatas"][0]
    documents = results["documents"][0]

    for cid, dist, meta, doc in zip(ids, distances, metadatas, documents):
        similarity = max(0.0, 1.0 - dist)  # cosine distance → similarity
        # Build description snippet from document
        snippet = doc[:300] + "…" if len(doc) > 300 else doc
        companies.append({
            "name":        meta.get("name", ""),
            "batch":       meta.get("batch_label", ""),
            "industry":    meta.get("industry", ""),
            "status":      meta.get("status", ""),
            "is_hiring":   bool(meta.get("is_hiring", 0)),
            "top_company": bool(meta.get("top_company", 0)),
            "tags":        meta.get("tags", ""),
            "location":    meta.get("all_locations", ""),
            "website":     meta.get("website", ""),
            "similarity":  round(similarity * 100, 1),
            "snippet":     snippet,
        })

    return {
        "query":   query,
        "count":   len(companies),
        "results": companies,
    }


def query_database(sql: str) -> dict:
    sql_stripped = sql.strip()

    # Safety: only allow SELECT
    first_word = sql_stripped.split()[0].upper() if sql_stripped else ""
    if first_word != "SELECT":
        return {"error": "Only SELECT statements are allowed.", "sql": sql}

    conn = get_db_connection()
    try:
        cur = conn.execute(sql_stripped)
        rows = cur.fetchmany(100)
        columns = [d[0] for d in cur.description] if cur.description else []
        data = [dict(zip(columns, row)) for row in rows]
        return {
            "sql":     sql,
            "columns": columns,
            "rows":    data,
            "count":   len(data),
        }
    except Exception as e:
        return {"error": str(e), "sql": sql}


# ── Tool dispatcher ──────────────────────────────────────────────────────────
def execute_tool(name: str, inputs: dict) -> str:
    if name == "search_companies":
        result = search_companies(
            query=inputs["query"],
            n_results=inputs.get("n_results", 10),
            where=inputs.get("where"),
        )
    elif name == "query_database":
        result = query_database(sql=inputs["sql"])
    else:
        result = {"error": f"Unknown tool: {name}"}

    return json.dumps(result, ensure_ascii=False)


# ── Agentic loop ──────────────────────────────────────────────────────────────
def run_agent(messages: list, api_key: str) -> tuple[str, list]:
    """
    Run the Claude agentic loop.
    Returns (final_text, tool_calls_log).
    tool_calls_log: list of {name, inputs, result_preview}
    """
    client      = get_anthropic_client(api_key)
    tool_calls  = []
    loop_msgs   = list(messages)  # copy so we don't mutate session state yet

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=loop_msgs,
        )

        # Collect text + tool use blocks
        assistant_content = response.content
        loop_msgs.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason == "end_turn":
            # Extract final text
            final_text = " ".join(
                block.text for block in assistant_content
                if hasattr(block, "text")
            )
            return final_text, tool_calls

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in assistant_content:
                if block.type != "tool_use":
                    continue

                result_str = execute_tool(block.name, block.input)
                result_obj = json.loads(result_str)

                # Log for UI display
                preview = result_str[:500] + "…" if len(result_str) > 500 else result_str
                tool_calls.append({
                    "name":    block.name,
                    "inputs":  block.input,
                    "result":  preview,
                })

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result_str,
                })

            loop_msgs.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason
        final_text = " ".join(
            block.text for block in assistant_content
            if hasattr(block, "text")
        )
        return final_text, tool_calls


# ── Streamlit UI ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="YC Companies Chat",
    page_icon="🚀",
    layout="wide",
)

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_input" not in st.session_state:
    st.session_state.pending_input = None

# ── Resolve API key ───────────────────────────────────────────────────────────
_env_key = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🚀 YC Companies Chat")
    st.markdown(
        "Query **5,689 YC companies** using natural language. "
        "Powered by Claude + hybrid search (vector + SQL)."
    )
    st.divider()

    # API key input (shown only when env var is absent)
    if _env_key:
        _api_key = _env_key
    else:
        _api_key = st.text_input(
            "Anthropic API key",
            type="password",
            placeholder="sk-ant-...",
            help="Enter your Anthropic API key. Set ANTHROPIC_API_KEY env var to skip this.",
        )
        if not _api_key:
            st.warning("Enter an API key above to start chatting.")
        st.divider()

    st.subheader("Example questions")
    for q in EXAMPLE_QUESTIONS:
        if st.button(q, key=f"btn_{q}", use_container_width=True):
            st.session_state.pending_input = q

    st.divider()

    if st.button("Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.pending_input = None
        st.rerun()

    st.divider()
    st.caption(
        "Data: [yc-oss/api](https://github.com/yc-oss/api) · "
        "Batches S05–S26"
    )

# ── Main chat area ────────────────────────────────────────────────────────────
st.header("YC Companies Explorer")

# Render history
for msg in st.session_state.messages:
    role = msg["role"]
    with st.chat_message(role):
        if role == "assistant":
            st.markdown(msg["text"])
            if msg.get("tool_calls"):
                with st.expander(f"Tool calls ({len(msg['tool_calls'])})"):
                    for tc in msg["tool_calls"]:
                        st.markdown(f"**`{tc['name']}`**")
                        st.json(tc["inputs"])
                        st.text(tc["result"])
        else:
            # user
            content = msg.get("content", "")
            if isinstance(content, str):
                st.markdown(content)
            else:
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        st.markdown(block["text"])

# Determine user input: chat box or sidebar button
user_input = st.chat_input("Ask anything about YC companies…")

if st.session_state.pending_input and not user_input:
    user_input = st.session_state.pending_input
    st.session_state.pending_input = None

if user_input and not _api_key:
    st.warning("Please enter your Anthropic API key in the sidebar first.")
    user_input = None

if user_input:
    # Show user message
    with st.chat_message("user"):
        st.markdown(user_input)

    # Build messages for API (only role/content pairs)
    api_messages = []
    for m in st.session_state.messages:
        if m["role"] == "user":
            api_messages.append({"role": "user", "content": m["content"]})
        else:
            # assistant messages stored as text; reconstruct simple content
            api_messages.append({"role": "assistant", "content": m["text"]})

    api_messages.append({"role": "user", "content": user_input})

    # Store user turn
    st.session_state.messages.append({
        "role":    "user",
        "content": user_input,
    })

    # Run agent with spinner
    with st.spinner("Thinking…"):
        final_text, tool_calls = run_agent(api_messages, _api_key)

    # Show assistant response
    with st.chat_message("assistant"):
        st.markdown(final_text)
        if tool_calls:
            with st.expander(f"Tool calls ({len(tool_calls)})"):
                for tc in tool_calls:
                    st.markdown(f"**`{tc['name']}`**")
                    st.json(tc["inputs"])
                    st.text(tc["result"])

    # Store assistant turn
    st.session_state.messages.append({
        "role":       "assistant",
        "text":       final_text,
        "tool_calls": tool_calls,
    })
