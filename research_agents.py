"""
research_agents.py — Parallel specialist research agents for YC companies.

Architecture:
  ThreadPoolExecutor runs 4 specialist agents in parallel:
    - news_agent     : recent news via Exa neural search
    - market_agent   : competitors + market trends via Exa + Fiber
    - funding_agent  : funding history via Nyne
    - community_agent: Reddit/HN sentiment via Exa

  After all 4 finish, a synthesis agent (no tools) combines results.
  Progress is communicated via a queue.Queue.
  Results are persisted to SQLite as each agent completes.

Tools use the Orthogonal CLI (`orth run`) for premium API access.
"""

import json
import sqlite3
import subprocess
import time
import traceback
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from utils import MODEL

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_TOKENS = 2048
ORTH_TIMEOUT = 45   # seconds per orth CLI call

# Sentinel pushed to progress_queue when the entire run is finished
DONE_SENTINEL = object()


# ── Orthogonal CLI wrapper ────────────────────────────────────────────────────
def _extract_json(text: str) -> dict | list:
    """
    Extract the first valid JSON object/array from text.
    Handles: orth status lines ("- Calling..."), markdown fences,
    trailing prose after JSON, and { / [ appearing inside URLs before the real JSON.
    Tries every candidate { / [ position in order until one parses cleanly.
    """
    stripped = text.strip()

    # Strip markdown fences: ```json ... ``` or ``` ... ```
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        inner = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        stripped = inner.strip()

    # Sanitise common Claude-generated JSON issues
    stripped = stripped.replace(": undefined", ": null").replace(":undefined", ":null")

    # Try every { or [ position — stops at the first one that fully parses
    decoder = json.JSONDecoder()
    for i, c in enumerate(stripped):
        if c in "{[":
            try:
                obj, _ = decoder.raw_decode(stripped, i)
                return obj
            except json.JSONDecodeError:
                continue

    raise ValueError(f"No valid JSON found in output: {text[:300]}")


def _orth(args: list, timeout: int = ORTH_TIMEOUT) -> dict:
    """
    Run `orth run <args>` and return parsed JSON.
    Never raises — returns {"error": "..."} on failure.
    """
    cmd = ["orth", "run", "--raw"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            msg = (result.stderr or result.stdout).strip()[:600]
            return {"error": msg}
        out = result.stdout.strip()
        if not out:
            return {"error": "empty response from orth"}
        return _extract_json(out)
    except subprocess.TimeoutExpired:
        return {"error": f"orth command timed out after {timeout}s"}
    except (json.JSONDecodeError, ValueError) as e:
        return {"error": f"JSON parse error: {e}", "raw": result.stdout[:400]}
    except Exception as e:
        return {"error": str(e)}


# ── Tool implementations ──────────────────────────────────────────────────────
def _tool_exa_search(query: str, num_results: int = 6) -> dict:
    """Neural web search using Exa."""
    return _orth([
        "exa", "/search",
        "-b", json.dumps({
            "query":      query,
            "numResults": min(num_results, 10),
            "type":       "auto",
            "contents":   {"text": {"maxCharacters": 600}},
        }),
    ])


def _tool_nyne_funding(company_name: str) -> dict:
    """
    Get funding history via Nyne (async: POST → poll GET).
    Falls back gracefully if polling times out.
    """
    start = _orth([
        "-X", "POST", "nyne", "/company/funding",
        "-b", json.dumps({"company_name": company_name}),
    ], timeout=10)
    if "error" in start:
        return start

    # Check if response is already complete (some APIs return synchronously)
    if start.get("status") not in ("processing", "pending", "queued"):
        return start

    job_id = start.get("id") or start.get("job_id") or start.get("taskId")
    if not job_id:
        return start   # treat as immediate result

    # Poll up to ~45s
    for _ in range(15):
        time.sleep(3)
        result = _orth(["nyne", f"/company/funding/{job_id}"], timeout=8)
        status = result.get("status", "")
        if status in ("completed", "done", "success") or "rounds" in result or "funding" in result:
            return result
        if "error" in result:
            return result

    return {"error": "Nyne funding lookup timed out", "partial": start}


def _tool_scrapegraph(url: str, prompt: str) -> dict:
    """AI-powered page extraction via Scrapegraph."""
    return _orth([
        "scrapegraph", "/v1/smartscraper",
        "-b", json.dumps({"website_url": url, "user_prompt": prompt}),
    ], timeout=60)


# ── Tool schemas ──────────────────────────────────────────────────────────────
_EXA_TOOL = {
    "name": "exa_search",
    "description": (
        "Neural web search using Exa. Returns semantically ranked results with "
        "snippets. Use for finding news, articles, discussions, and web content."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query":       {"type": "string", "description": "Search query."},
            "num_results": {"type": "integer", "description": "Results to return (default 6, max 10).", "default": 6},
        },
        "required": ["query"],
    },
}

_FUNDING_TOOL = {
    "name": "nyne_funding",
    "description": (
        "Look up a company's funding history, investors, and total raised "
        "using the Nyne database."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "Company name to look up."},
        },
        "required": ["company_name"],
    },
}

_SCRAPE_TOOL = {
    "name": "scrapegraph",
    "description": (
        "AI-powered extraction from any URL. Provide a plain-English prompt "
        "describing what to extract."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url":    {"type": "string", "description": "URL to scrape."},
            "prompt": {"type": "string", "description": "What to extract from the page."},
        },
        "required": ["url", "prompt"],
    },
}


# ── Tool dispatcher ───────────────────────────────────────────────────────────
def _dispatch(name: str, inputs: dict) -> str:
    if name == "exa_search":
        result = _tool_exa_search(inputs["query"], inputs.get("num_results", 6))
    elif name == "nyne_funding":
        result = _tool_nyne_funding(inputs["company_name"])
    elif name == "scrapegraph":
        result = _tool_scrapegraph(inputs["url"], inputs.get("prompt", "Extract key information"))
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result, ensure_ascii=False)


# ── Retry helper ─────────────────────────────────────────────────────────────
def _create_with_retry(client, **kwargs):
    """Call client.messages.create() with exponential backoff on rate-limit errors."""
    last_exc = None
    for attempt in range(4):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            last_exc = e
            if attempt < 3:
                time.sleep(2 ** attempt)  # 1, 2, 4 seconds
    raise last_exc


# ── Generic agentic loop ──────────────────────────────────────────────────────
def _run_agent(system: str, user: str, tools: list, api_key: str) -> str:
    """
    Standard multi-turn agentic loop.
    Returns the final text response (expected to be JSON from specialist agents).
    """
    client   = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": user}]

    while True:
        response = _create_with_retry(
            client,
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            return " ".join(
                b.text for b in response.content if hasattr(b, "text")
            )

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     _dispatch(block.name, block.input),
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason — return whatever text exists
        return " ".join(b.text for b in response.content if hasattr(b, "text"))


# ── Specialist system prompts ─────────────────────────────────────────────────
_NEWS_SYSTEM = """You are a news research specialist. Find recent news, press coverage,
and notable events for the given company in the past 12 months.

Use exa_search to search for recent articles. If you find a very relevant page,
use scrapegraph to extract more detail from it.

Return ONLY a JSON object with this exact structure (no markdown fences):
{
  "summary": "<2-3 sentence summary of recent news>",
  "sources": [
    {"title": "...", "url": "...", "date": "...", "snippet": "..."}
  ]
}"""

_MARKET_SYSTEM = """You are a market research specialist. Analyze the competitive
landscape and market position for the given company.

Use exa_search to find: (1) direct competitors, (2) market size and trends,
(3) the company's differentiation, (4) recent industry developments.

Return ONLY a JSON object with this exact structure (no markdown fences):
{
  "summary": "<2-3 sentence market overview>",
  "competitors": [{"name": "...", "url": "...", "differentiation": "..."}],
  "market_size": "<estimate or null>",
  "trends": ["<trend1>", "<trend2>"]
}"""

_FUNDING_SYSTEM = """You are a funding research specialist. Find the fundraising
history and investor information for the given company.

Use nyne_funding first for structured data. Supplement with exa_search for
any recent rounds or announcements not yet in the database.

Return ONLY a JSON object with this exact structure (no markdown fences):
{
  "summary": "<2-3 sentence funding summary>",
  "total_raised": "<amount or null>",
  "stage": "<current stage or null>",
  "rounds": [
    {"date": "...", "amount": "...", "round_type": "...", "lead_investor": "..."}
  ],
  "investors": ["<investor name>"]
}"""

_COMMUNITY_SYSTEM = """You are a community sentiment specialist. Find how people
discuss the given company on Reddit, Hacker News, and tech forums.

Use exa_search with queries like:
  - "site:reddit.com <company name>"
  - "site:news.ycombinator.com <company name>"
  - "<company name> review users experience"

Return ONLY a JSON object with this exact structure (no markdown fences):
{
  "summary": "<2-3 sentence sentiment summary>",
  "overall_sentiment": "positive | mixed | negative | neutral",
  "posts": [
    {"source": "reddit|hn|other", "url": "...", "text": "...", "sentiment": "positive|negative|neutral"}
  ]
}"""

_SYNTHESIS_SYSTEM = """You are a senior investment analyst. You receive structured
research from four specialist agents (news, market, funding, community) and produce
a concise synthesis to inform an investment or partnership decision.

Return ONLY a JSON object with this exact structure (no markdown fences):
{
  "executive_summary": "<3-5 sentences covering the company's current position>",
  "opportunities": ["<opportunity1>", "<opportunity2>"],
  "risks": ["<risk1>", "<risk2>"],
  "verdict": "strong_buy | buy | hold | avoid",
  "confidence": "high | medium | low",
  "rationale": "<1-2 sentences explaining the verdict>"
}"""


# ── Specialist runners ────────────────────────────────────────────────────────
def _safe_agent(fn, *args) -> dict:
    """Run an agent function, returning {"error": ...} if JSON extraction fails."""
    try:
        return fn(*args)
    except (ValueError, json.JSONDecodeError) as e:
        return {"error": str(e)[:300]}


def _news_agent(company: dict, api_key: str) -> dict:
    prompt = (
        f"Research recent news for: {company['name']}\n"
        f"Website: {company.get('website', 'N/A')}\n"
        f"One-liner: {company.get('one_liner', 'N/A')}\n"
        f"Industry: {company.get('industry', 'N/A')}\n"
        "Find news from the past 12 months."
    )
    raw = _run_agent(_NEWS_SYSTEM, prompt, [_EXA_TOOL, _SCRAPE_TOOL], api_key)
    return _extract_json(raw)


def _market_agent(company: dict, api_key: str) -> dict:
    prompt = (
        f"Research the competitive landscape for: {company['name']}\n"
        f"Website: {company.get('website', 'N/A')}\n"
        f"One-liner: {company.get('one_liner', 'N/A')}\n"
        f"Industry: {company.get('industry', 'N/A')}\n"
        f"Tags: {company.get('tags', 'N/A')}"
    )
    raw = _run_agent(_MARKET_SYSTEM, prompt, [_EXA_TOOL, _SCRAPE_TOOL], api_key)
    return _extract_json(raw)


def _funding_agent(company: dict, api_key: str) -> dict:
    prompt = (
        f"Find funding history for: {company['name']}\n"
        f"Website: {company.get('website', 'N/A')}\n"
        f"YC batch: {company.get('batch_label', 'N/A')}\n"
        f"Current stage: {company.get('stage', 'N/A')}"
    )
    raw = _run_agent(_FUNDING_SYSTEM, prompt, [_FUNDING_TOOL, _EXA_TOOL], api_key)
    return _extract_json(raw)


def _community_agent(company: dict, api_key: str) -> dict:
    prompt = (
        f"Find community discussions about: {company['name']}\n"
        f"Website: {company.get('website', 'N/A')}\n"
        f"One-liner: {company.get('one_liner', 'N/A')}"
    )
    raw = _run_agent(_COMMUNITY_SYSTEM, prompt, [_EXA_TOOL], api_key)
    return _extract_json(raw)


def _synthesis_agent(
    company: dict,
    news: dict,
    market: dict,
    funding: dict,
    community: dict,
    api_key: str,
) -> dict:
    context = json.dumps({
        "company":   company,
        "news":      news,
        "market":    market,
        "funding":   funding,
        "community": community,
    }, indent=2)
    client = anthropic.Anthropic(api_key=api_key)
    response = _create_with_retry(
        client,
        model=MODEL,
        max_tokens=1024,
        system=_SYNTHESIS_SYSTEM,
        messages=[{"role": "user", "content": context}],
    )
    return _extract_json(response.content[0].text)


# ── Public orchestrator ───────────────────────────────────────────────────────
def run_research(
    run_id: str,
    company: dict,
    api_key: str,
    db_path: str,
    progress_queue: queue.Queue,
) -> None:
    """
    Entrypoint for the background research thread.
    Runs 4 specialist agents in parallel, then synthesis.
    Writes partial results to SQLite as each agent finishes.
    Pushes progress strings and DONE_SENTINEL to progress_queue.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)

    def _save(field: str, value):
        conn.execute(
            f"UPDATE company_research SET {field} = ? WHERE run_id = ?",
            (json.dumps(value), run_id),
        )
        conn.commit()

    agents = {
        "news":      _news_agent,
        "market":    _market_agent,
        "funding":   _funding_agent,
        "community": _community_agent,
    }
    results = {}

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_safe_agent, fn, company, api_key): name
                for name, fn in agents.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    results[name] = result
                    _save(f"{name}_result", result)
                    progress_queue.put(f"done:{name}")
                except Exception as e:
                    err = {"error": str(e)}
                    results[name] = err
                    _save(f"{name}_result", err)
                    progress_queue.put(f"error:{name}:{str(e)[:120]}")

        # Synthesis runs after all 4 specialists
        progress_queue.put("running:synthesis")
        synthesis = _synthesis_agent(
            company=company,
            news=results.get("news", {}),
            market=results.get("market", {}),
            funding=results.get("funding", {}),
            community=results.get("community", {}),
            api_key=api_key,
        )
        _save("synthesis_result", synthesis)
        conn.execute(
            "UPDATE company_research SET status = 'done', completed_at = ? WHERE run_id = ?",
            (int(time.time()), run_id),
        )
        conn.commit()
        progress_queue.put("done:synthesis")

    except Exception as e:
        conn.execute(
            "UPDATE company_research SET status = 'error', error_detail = ? WHERE run_id = ?",
            (traceback.format_exc()[:2000], run_id),
        )
        conn.commit()
        progress_queue.put(f"error:orchestrator:{str(e)[:120]}")

    finally:
        conn.close()
        progress_queue.put(DONE_SENTINEL)
