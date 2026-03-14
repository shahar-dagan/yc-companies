"""
chat.py — Streamlit chat interface for YC companies dataset.
Powered by Claude claude-sonnet-4-6 with hybrid retrieval (ChromaDB + SQLite).

Usage:
    streamlit run chat.py
"""

import json
import os
import time
import uuid

from dotenv import load_dotenv
load_dotenv()

import anthropic
import chromadb
import streamlit as st
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from utils import (
    CHROMA_DIR,
    MODEL,
    check_and_refresh_db,
    delete_session,
    get_db_connection,
    load_session_messages,
    save_message,
    setup_conversations_table,
)

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
        similarity = max(0.0, 1.0 - dist)
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


# ── Tool dispatcher ───────────────────────────────────────────────────────────
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
def run_agent(
    messages: list,
    api_key: str,
    response_placeholder=None,
) -> tuple[str, list]:
    """
    Run the Claude agentic loop.
    Returns (final_text, tool_calls_log).

    If response_placeholder (a st.empty()) is provided, streams text into it
    progressively as Claude generates it. Retries up to 3 times on rate-limit
    errors with exponential backoff (1s, 2s, 4s).
    """
    client     = get_anthropic_client(api_key)
    tool_calls = []
    loop_msgs  = list(messages)
    all_text   = ""

    while True:
        # ── API call with rate-limit retry ────────────────────────────────────
        last_exc = None
        response = None

        for attempt in range(4):
            try:
                if response_placeholder is not None:
                    # Streaming mode
                    turn_text = ""
                    with client.messages.stream(
                        model=MODEL,
                        max_tokens=4096,
                        system=SYSTEM_PROMPT,
                        tools=TOOLS,
                        messages=loop_msgs,
                    ) as stream:
                        for chunk in stream.text_stream:
                            turn_text += chunk
                            response_placeholder.markdown(all_text + turn_text + "▌")
                        response = stream.get_final_message()
                    if turn_text:
                        all_text += turn_text
                        response_placeholder.markdown(all_text)
                else:
                    response = client.messages.create(
                        model=MODEL,
                        max_tokens=4096,
                        system=SYSTEM_PROMPT,
                        tools=TOOLS,
                        messages=loop_msgs,
                    )
                break  # success

            except anthropic.RateLimitError:
                if attempt < 3:
                    wait = 2 ** attempt  # 1, 2, 4 seconds
                    if response_placeholder is not None:
                        response_placeholder.markdown(
                            f"_Rate limited — retrying in {wait}s…_"
                        )
                    time.sleep(wait)

            except Exception as e:
                st.error(f"Claude API error: {e}")
                return all_text or "", tool_calls
        else:
            st.error("Rate limit exceeded after retries. Please try again shortly.")
            return all_text or "", tool_calls

        # ── Process response ──────────────────────────────────────────────────
        assistant_content = response.content
        loop_msgs.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason == "end_turn":
            if not all_text:
                # Non-streaming path: extract text from blocks
                final_text = " ".join(
                    block.text for block in assistant_content
                    if hasattr(block, "text")
                )
                if response_placeholder is not None:
                    response_placeholder.markdown(final_text)
                return final_text, tool_calls
            return all_text, tool_calls

        if response.stop_reason == "tool_use":
            if response_placeholder is not None and not all_text:
                response_placeholder.markdown("_Using tools…_")

            tool_results = []
            for block in assistant_content:
                if block.type != "tool_use":
                    continue
                result_str = execute_tool(block.name, block.input)
                preview = result_str[:500] + "…" if len(result_str) > 500 else result_str
                tool_calls.append({
                    "name":   block.name,
                    "inputs": block.input,
                    "result": preview,
                })
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result_str,
                })

            # Clear "Using tools…" so streamed text can take over on next turn
            if response_placeholder is not None:
                response_placeholder.markdown(all_text or "")

            loop_msgs.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason
        fallback = " ".join(
            block.text for block in assistant_content if hasattr(block, "text")
        )
        return all_text or fallback, tool_calls


# ── Streamlit UI ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="YC Companies Chat",
    page_icon="🚀",
    layout="wide",
)

# ── Session state init ────────────────────────────────────────────────────────
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    _conn = get_db_connection()
    setup_conversations_table(_conn)
    st.session_state.messages = load_session_messages(_conn, st.session_state.session_id)

if "pending_input" not in st.session_state:
    st.session_state.pending_input = None

if "refresh_checked" not in st.session_state:
    st.session_state.refresh_checked = True
    st.session_state.refresh_in_progress = check_and_refresh_db()

# ── Resolve API key ───────────────────────────────────────────────────────────
_api_key = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🚀 YC Companies Chat")
    st.markdown(
        "Query **5,689 YC companies** using natural language. "
        "Powered by Claude + hybrid search (vector + SQL)."
    )
    st.divider()

    st.subheader("Example questions")
    for q in EXAMPLE_QUESTIONS:
        if st.button(q, key=f"btn_{q}", use_container_width=True):
            st.session_state.pending_input = q

    st.divider()

    clear_btn = st.button("🗑 Clear conversation", use_container_width=True)
    if clear_btn:
        _conn = get_db_connection()
        delete_session(_conn, st.session_state.session_id)
        st.session_state.messages = []
        st.session_state.pending_input = None
        st.rerun()

    new_btn = st.button("✨ New session", use_container_width=True)
    if new_btn:
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.session_state.pending_input = None
        st.rerun()

    if st.session_state.get("messages"):
        lines = []
        for m in st.session_state.messages:
            if m["role"] == "user":
                content = m.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        b["text"] for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                lines.append(f"**You:** {content}")
            else:
                lines.append(f"**Assistant:** {m.get('text', '')}")
        md = "\n\n".join(lines)
        st.download_button(
            label="⬇ Download chat",
            data=md,
            file_name="conversation.md",
            mime="text/markdown",
            use_container_width=True,
        )

    if st.session_state.get("refresh_in_progress"):
        st.info("Database refresh running in background…")

    st.divider()
    st.caption(
        "Data: [yc-oss/api](https://github.com/yc-oss/api) · "
        "Batches S05–S26"
    )

# ── Main chat area ────────────────────────────────────────────────────────────
# Constrain width for readability (wide layout still allows sidebar + content)
st.markdown(
    """
    <style>
    .block-container { max-width: 52rem; padding-top: 1.5rem; padding-bottom: 2rem; }
    [data-testid="stChatMessage"] { padding: 1rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)
st.header("YC Companies Explorer")
st.caption("Ask about companies, batches, industries, or trends. I’ll use search and SQL as needed.")

# Render history
for msg in st.session_state.messages:
    role = msg["role"]
    with st.chat_message(role):
        if role == "assistant":
            text = msg.get("text") or ""
            if text:
                st.markdown(text)
            if msg.get("tool_calls"):
                with st.expander(f"🔧 Tool calls ({len(msg['tool_calls'])})", expanded=False):
                    for tc in msg["tool_calls"]:
                        st.markdown(f"**`{tc['name']}`**")
                        st.json(tc["inputs"])
                        result_preview = (tc.get("result") or "")[:1500]
                        if len((tc.get("result") or "")) > 1500:
                            result_preview += "…"
                        st.text(result_preview)
        else:
            content = msg.get("content", "")
            if isinstance(content, str):
                st.markdown(content or "_Message_")
            else:
                for block in (content or []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        st.markdown(block.get("text") or "_Message_")

# Determine user input
user_input = st.chat_input("Ask anything about YC companies…")

if st.session_state.pending_input and not user_input:
    user_input = st.session_state.pending_input
    st.session_state.pending_input = None

if not _api_key:
    st.error(
        "**ANTHROPIC_API_KEY** not set. Copy `.env.example` to `.env` and add your key. "
        "Get one at [console.anthropic.com](https://console.anthropic.com/)."
    )
    st.stop()

if user_input:
    # Show user message
    with st.chat_message("user"):
        st.markdown(user_input)

    # Build messages for API (role/content pairs only)
    api_messages = []
    for m in st.session_state.messages:
        if m["role"] == "user":
            api_messages.append({"role": "user", "content": m["content"]})
        else:
            api_messages.append({"role": "assistant", "content": m["text"]})

    api_messages.append({"role": "user", "content": user_input})

    # Store and persist user turn
    st.session_state.messages.append({
        "role":    "user",
        "content": user_input,
    })
    _conn = get_db_connection()
    save_message(_conn, st.session_state.session_id, "user", user_input)

    # Run agent with streaming
    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        final_text, tool_calls = run_agent(api_messages, _api_key, response_placeholder)
        if tool_calls:
            with st.expander(f"🔧 Tool calls ({len(tool_calls)})", expanded=False):
                for tc in tool_calls:
                    st.markdown(f"**`{tc['name']}`**")
                    st.json(tc["inputs"])
                    result_preview = (tc.get("result") or "")[:1500]
                    if len((tc.get("result") or "")) > 1500:
                        result_preview += "…"
                    st.text(result_preview)

    # Store and persist assistant turn
    st.session_state.messages.append({
        "role":       "assistant",
        "text":       final_text,
        "tool_calls": tool_calls,
    })
    _conn = get_db_connection()
    save_message(_conn, st.session_state.session_id, "assistant", final_text, tool_calls)
