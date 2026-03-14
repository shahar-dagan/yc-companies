"""
pages/research.py — Company Deep Research page.
Runs 4 parallel specialist agents + synthesis via Claude claude-sonnet-4-6.
Uses Orthogonal APIs: Exa (search), Nyne (funding), Scrapegraph (website).
"""

import json
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from utils import (
    DB_PATH,
    get_db_connection,
    setup_research_table,
    insert_research_run,
    get_research_run,
    list_research_runs,
)
from research_agents import run_research, DONE_SENTINEL

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="YC Company Research",
    page_icon="🔬",
    layout="wide",
)

_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
conn = get_db_connection()
setup_research_table(conn)

# ── Page header ────────────────────────────────────────────────────────────────
st.title("🔬 Company Deep Research")
st.caption(
    "Runs 4 parallel AI agents (news · market · funding · community) "
    "then synthesizes an investment-style verdict. Powered by Exa + Nyne via Orthogonal."
)

if not _api_key:
    st.error(
        "**ANTHROPIC_API_KEY** not set. Copy `.env.example` to `.env` and add your key. "
        "Get one at [console.anthropic.com](https://console.anthropic.com/)."
    )
    st.stop()


# ── Company picker ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def _load_companies() -> list:
    cur = get_db_connection().execute(
        "SELECT id, name, batch_label, industry, status, website, "
        "       one_liner, tags, stage "
        "FROM companies ORDER BY name ASC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


companies       = _load_companies()
company_labels  = [f"{c['name']}  ({c['batch_label']})" for c in companies]
company_by_label = {f"{c['name']}  ({c['batch_label']})": c for c in companies}

selected_label = st.selectbox(
    "Select a company",
    options=company_labels,
    index=None,
    placeholder="Type to search…",
)

if not selected_label:
    st.stop()

company = company_by_label[selected_label]

# ── Company info strip ─────────────────────────────────────────────────────────
info_col, run_col = st.columns([5, 1])
with info_col:
    status_color = {"Active": "green", "Acquired": "blue", "Public": "violet", "Inactive": "gray"}.get(
        company.get("status", ""), "gray"
    )
    st.markdown(
        f"**{company['name']}** &nbsp;·&nbsp; "
        f":{status_color}[{company.get('status','')}] &nbsp;·&nbsp; "
        f"{company.get('industry','')} &nbsp;·&nbsp; "
        f"`{company.get('batch_label','')}`"
    )
    if company.get("one_liner"):
        st.caption(company["one_liner"])
    if company.get("website"):
        st.caption(f"🌐 {company['website']}")
with run_col:
    run_btn = st.button("▶ Run Research", type="primary", use_container_width=True)

st.divider()

# ── Launch research ────────────────────────────────────────────────────────────
if run_btn:
    run_id = str(uuid.uuid4())
    insert_research_run(conn, run_id, company["id"], company["name"])
    st.session_state["active_run_id"]    = run_id
    st.session_state["active_run_queue"] = queue.Queue()
    st.session_state["active_company"]   = company
    st.session_state.pop("completed_agents", None)
    st.session_state.pop("display_run_id", None)

    threading.Thread(
        target=run_research,
        kwargs=dict(
            run_id=run_id,
            company=company,
            api_key=_api_key,
            db_path=str(DB_PATH),
            progress_queue=st.session_state["active_run_queue"],
        ),
        daemon=True,
    ).start()
    st.rerun()

# ── Progress polling ───────────────────────────────────────────────────────────
AGENT_LABELS = {
    "news":      "📰 News",
    "market":    "📊 Market",
    "funding":   "💰 Funding",
    "community": "💬 Community",
    "synthesis": "🧠 Synthesis",
}

if "active_run_id" in st.session_state:
    pq: queue.Queue = st.session_state.get("active_run_queue")
    completed = set(st.session_state.get("completed_agents", []))
    synthesis_running = st.session_state.get("synthesis_running", False)
    still_running = True

    if pq:
        while True:
            try:
                msg = pq.get_nowait()
            except queue.Empty:
                break

            if msg is DONE_SENTINEL:
                still_running = False
                run_id_done = st.session_state.pop("active_run_id")
                st.session_state.pop("active_run_queue", None)
                st.session_state.pop("completed_agents", None)
                st.session_state.pop("synthesis_running", None)
                st.session_state["display_run_id"] = run_id_done
                break
            elif isinstance(msg, str):
                parts = msg.split(":", 2)
                if parts[0] == "done":
                    completed.add(parts[1])
                elif parts[0] == "running" and len(parts) > 1:
                    if parts[1] == "synthesis":
                        synthesis_running = True
                elif parts[0] == "error":
                    agent = parts[1] if len(parts) > 1 else "unknown"
                    detail = parts[2] if len(parts) > 2 else ""
                    st.warning(f"Agent **{agent}** encountered an error: {detail}")

        st.session_state["completed_agents"]  = list(completed)
        st.session_state["synthesis_running"] = synthesis_running

    if still_running and "active_run_id" in st.session_state:
        specialists_done = len([a for a in completed if a != "synthesis"])
        total_specialists = 4
        synthesis_done = "synthesis" in completed

        # Progress: specialists count for 80%, synthesis for 20%
        pct = (specialists_done / total_specialists * 0.8 + (0.2 if synthesis_done else 0))

        st.progress(pct, text=f"Running… {specialists_done}/4 specialist agents done")

        cols = st.columns(5)
        for i, (name, label) in enumerate(AGENT_LABELS.items()):
            with cols[i]:
                if name in completed:
                    st.success(label)
                elif name == "synthesis" and synthesis_running:
                    st.info(label + " ⏳")
                elif name != "synthesis" and specialists_done < total_specialists:
                    st.info(label + " ⏳")
                else:
                    st.empty()

        time.sleep(1)
        st.rerun()

# ── Results display ────────────────────────────────────────────────────────────
display_run_id = st.session_state.get("display_run_id")

# If no explicit run selected, show the most recent completed run for this company
if not display_run_id:
    past = list_research_runs(conn, company["id"], limit=1)
    if past and past[0]["status"] == "done":
        display_run_id = past[0]["run_id"]

if display_run_id:
    run = get_research_run(conn, display_run_id)

    if run and run["status"] == "done":
        def _load(field: str) -> dict:
            raw = run.get(field)
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except Exception:
                return {"error": "Could not parse result"}

        news_data      = _load("news_result")
        market_data    = _load("market_result")
        funding_data   = _load("funding_result")
        community_data = _load("community_result")
        synthesis_data = _load("synthesis_result")

        # Synthesis verdict banner
        verdict = synthesis_data.get("verdict", "")
        if verdict:
            verdict_color = {
                "strong_buy": "green", "buy": "green",
                "hold": "orange", "avoid": "red",
            }.get(verdict, "gray")
            col_v, col_c, col_r = st.columns([1, 1, 4])
            col_v.metric("Verdict", verdict.replace("_", " ").upper())
            col_c.metric("Confidence", synthesis_data.get("confidence", "").upper())
            col_r.info(synthesis_data.get("rationale", ""))

        # Build and offer download report
        def _build_report_md(company, synthesis_data, news_data, market_data, funding_data, community_data):
            lines = [f"# Research Report: {company['name']}", ""]
            lines += [f"**Batch:** {company.get('batch_label','')}  |  **Industry:** {company.get('industry','')}  |  **Status:** {company.get('status','')}", ""]

            lines += ["## Synthesis", ""]
            if synthesis_data.get("error"):
                lines.append(f"> Agent failed: {synthesis_data['error']}")
            else:
                lines.append(synthesis_data.get("executive_summary", ""))
                lines += ["", f"**Verdict:** {synthesis_data.get('verdict','').replace('_',' ').upper()}  |  **Confidence:** {synthesis_data.get('confidence','').upper()}", ""]
                lines.append(synthesis_data.get("rationale", ""))
                if synthesis_data.get("opportunities"):
                    lines += ["", "**Opportunities**"]
                    lines += [f"- {o}" for o in synthesis_data["opportunities"]]
                if synthesis_data.get("risks"):
                    lines += ["", "**Risks**"]
                    lines += [f"- {r}" for r in synthesis_data["risks"]]

            lines += ["", "## News", ""]
            if news_data.get("error"):
                lines.append(f"> Agent failed: {news_data['error']}")
            else:
                lines.append(news_data.get("summary", ""))
                for src in news_data.get("sources", []):
                    date_str = f" · {src.get('date','')}" if src.get("date") else ""
                    lines.append(f"- [{src.get('title','Untitled')}]({src.get('url','#')}){date_str}  \n  {src.get('snippet','')}")

            lines += ["", "## Market & Competition", ""]
            if market_data.get("error"):
                lines.append(f"> Agent failed: {market_data['error']}")
            else:
                lines.append(market_data.get("summary", ""))
                if market_data.get("market_size"):
                    lines.append(f"\n**Market Size:** {market_data['market_size']}")
                if market_data.get("competitors"):
                    lines += ["", "**Competitors**"]
                    for c in market_data["competitors"]:
                        lines.append(f"- **[{c.get('name','')}]({c.get('url','#')})** — {c.get('differentiation','')}")
                if market_data.get("trends"):
                    lines += ["", "**Trends**"]
                    lines += [f"- {t}" for t in market_data["trends"]]

            lines += ["", "## Funding", ""]
            if funding_data.get("error"):
                lines.append(f"> Agent failed: {funding_data['error']}")
            else:
                lines.append(funding_data.get("summary", ""))
                lines.append(f"\n**Total Raised:** {funding_data.get('total_raised','Unknown')}  |  **Stage:** {funding_data.get('stage','Unknown')}")
                if funding_data.get("rounds"):
                    lines += ["", "**Rounds**"]
                    for r in funding_data["rounds"]:
                        parts = [r.get("round_type",""), r.get("date",""), r.get("amount","")]
                        line = " · ".join(p for p in parts if p)
                        if r.get("lead_investor"):
                            line += f"  (Lead: {r['lead_investor']})"
                        lines.append(f"- {line}")
                if funding_data.get("investors"):
                    lines.append("\n**Investors:** " + ", ".join(funding_data["investors"]))

            lines += ["", "## Community Sentiment", ""]
            if community_data.get("error"):
                lines.append(f"> Agent failed: {community_data['error']}")
            else:
                lines.append(community_data.get("summary", ""))
                lines.append(f"\n**Overall Sentiment:** {community_data.get('overall_sentiment','neutral').upper()}")
                for p in community_data.get("posts", []):
                    icon = {"positive": "✅", "negative": "❌", "neutral": "◻️"}.get(p.get("sentiment","neutral"), "◻️")
                    lines.append(f"{icon} **{p.get('source','')}** — [{p.get('text','')[:160]}]({p.get('url','#')})")

            return "\n".join(lines)

        report_md = _build_report_md(company, synthesis_data, news_data, market_data, funding_data, community_data)
        st.download_button(
            label="⬇ Download report",
            data=report_md,
            file_name=f"{company['name'].replace(' ', '_')}_research.md",
            mime="text/markdown",
        )

        tab_syn, tab_news, tab_market, tab_funding, tab_community = st.tabs(
            ["🧠 Synthesis", "📰 News", "📊 Market", "💰 Funding", "💬 Community"]
        )

        with tab_syn:
            st.subheader("Executive Summary")
            failed_agents = [
                name for name, data in [
                    ("news", news_data), ("market", market_data),
                    ("funding", funding_data), ("community", community_data),
                ]
                if data.get("error")
            ]
            if failed_agents:
                st.warning(f"Note: {', '.join(failed_agents)} agent(s) failed — synthesis may be incomplete.")
            st.write(synthesis_data.get("executive_summary", "No synthesis available."))
            c1, c2 = st.columns(2)
            with c1:
                if synthesis_data.get("opportunities"):
                    st.markdown("**Opportunities**")
                    for o in synthesis_data["opportunities"]:
                        st.markdown(f"- {o}")
            with c2:
                if synthesis_data.get("risks"):
                    st.markdown("**Risks**")
                    for r in synthesis_data["risks"]:
                        st.markdown(f"- {r}")

        with tab_news:
            st.subheader("Recent News")
            if news_data.get("error"):
                st.warning(f"News agent error: {news_data['error']}")
            else:
                st.write(news_data.get("summary", ""))
                for src in news_data.get("sources", []):
                    title   = src.get("title", "Untitled")
                    url     = src.get("url", "#")
                    date    = src.get("date", "")
                    snippet = src.get("snippet", "")
                    date_str = f" · {date}" if date else ""
                    st.markdown(f"- [{title}]({url}){date_str}  \n  {snippet}")

        with tab_market:
            st.subheader("Market & Competition")
            if market_data.get("error"):
                st.warning(f"Market agent error: {market_data['error']}")
            else:
                st.write(market_data.get("summary", ""))
                if market_data.get("market_size"):
                    st.metric("Estimated Market Size", market_data["market_size"])
                if market_data.get("competitors"):
                    st.markdown("**Competitors**")
                    for c in market_data["competitors"]:
                        name = c.get("name", "")
                        url  = c.get("url", "#")
                        diff = c.get("differentiation", "")
                        st.markdown(f"- **[{name}]({url})** — {diff}")
                if market_data.get("trends"):
                    st.markdown("**Trends**")
                    for t in market_data["trends"]:
                        st.markdown(f"- {t}")

        with tab_funding:
            st.subheader("Funding History")
            if funding_data.get("error"):
                st.warning(f"Funding agent error: {funding_data['error']}")
            else:
                st.write(funding_data.get("summary", ""))
                m1, m2 = st.columns(2)
                m1.metric("Total Raised",   funding_data.get("total_raised", "Unknown"))
                m2.metric("Current Stage",  funding_data.get("stage", "Unknown"))
                if funding_data.get("rounds"):
                    st.markdown("**Funding Rounds**")
                    for r in funding_data["rounds"]:
                        parts = [
                            r.get("round_type", ""),
                            r.get("date", ""),
                            r.get("amount", ""),
                        ]
                        lead = r.get("lead_investor", "")
                        line = " · ".join(p for p in parts if p)
                        if lead:
                            line += f"  (Lead: {lead})"
                        st.markdown(f"- {line}")
                if funding_data.get("investors"):
                    st.markdown("**Investors:** " + ", ".join(funding_data["investors"]))

        with tab_community:
            st.subheader("Community Sentiment")
            if community_data.get("error"):
                st.warning(f"Community agent error: {community_data['error']}")
            else:
                st.write(community_data.get("summary", ""))
                sentiment = community_data.get("overall_sentiment", "neutral")
                color = {
                    "positive": "green", "negative": "red",
                    "mixed": "orange",
                }.get(sentiment, "gray")
                st.markdown(f"Overall: :{color}[**{sentiment.upper()}**]")
                for p in community_data.get("posts", []):
                    source = p.get("source", "")
                    url    = p.get("url", "#")
                    text   = p.get("text", "")[:160]
                    sent   = p.get("sentiment", "neutral")
                    icon   = {"positive": "✅", "negative": "❌", "neutral": "◻️"}.get(sent, "◻️")
                    st.markdown(f"{icon} **{source}** — [{text}]({url})")

    elif run and run["status"] == "error":
        st.error(f"Research failed: {run.get('error_detail', 'Unknown error')[:300]}")
    elif run and run["status"] == "running":
        st.info("Research is still running… refresh to check progress.")

# ── History ────────────────────────────────────────────────────────────────────
st.divider()
st.subheader("Past Research Runs")
past_runs = list_research_runs(conn, company["id"], limit=10)

if not past_runs:
    st.caption("No research runs yet for this company. Click **▶ Run Research** to start.")
else:
    for pr in past_runs:
        triggered = time.strftime("%Y-%m-%d %H:%M", time.localtime(pr["triggered_at"]))
        icon = {"done": "✅", "running": "⏳", "error": "❌"}.get(pr["status"], "?")
        is_selected = pr["run_id"] == display_run_id
        label = f"{icon} {triggered}  —  {pr['status']}" + (" ← viewing" if is_selected else "")
        if st.button(label, key=f"hist_{pr['run_id']}", use_container_width=False):
            st.session_state["display_run_id"] = pr["run_id"]
            st.rerun()
        if pr["status"] == "error" and pr.get("error_detail"):
            st.caption(f"Error: {pr['error_detail'][:120]}")
