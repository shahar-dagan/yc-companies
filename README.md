# YC Companies Explorer

An AI-powered platform to explore, analyze, and research Y Combinator companies. Built with Claude, Streamlit, SQLite, and ChromaDB.

![Python](https://img.shields.io/badge/python-3.10+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Streamlit](https://img.shields.io/badge/built%20with-Streamlit-red)

## Features

- **Chat UI** — Ask natural language questions about any of the ~5,700 YC companies. Claude uses hybrid retrieval (semantic search + SQL) to answer.
- **Research Agent** — Deep-dive any company with 4 parallel specialist agents (news, market, funding, community sentiment) that synthesize into an investment-grade report.
- **Market Dashboard** — 8 interactive charts showing industry momentum, hiring heat, survival rates, emerging tags, geographic distribution, and more.
- **Static Analysis** — Generate 12 matplotlib charts from live YC data with a single command.

## Architecture

```
YC OSS API → ingest.py → yc_companies.db (SQLite)
                       → chroma_db/      (ChromaDB vectors)

chat.py → Claude claude-sonnet-4-6 (agentic loop)
            ├── search_companies → ChromaDB (semantic search)
            └── query_database  → SQLite    (structured SQL)

pages/research.py → 4 parallel agents → synthesis
pages/dashboard.py → live SQLite queries → Plotly charts
```

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/your-username/yc-companies.git
cd yc-companies
pip install -r requirements.txt
```

### 2. Add your API key

```bash
cp .env.example .env
# Edit .env and add your Anthropic API key
```

Get an API key at [console.anthropic.com](https://console.anthropic.com/).

### 3. Ingest data (run once, ~2 minutes)

```bash
python3 ingest.py
```

This fetches ~5,700 companies from the [YC OSS API](https://github.com/yc-oss/api), populates a local SQLite database, and builds ChromaDB vector embeddings using `all-MiniLM-L6-v2`.

### 4. Launch the app

```bash
streamlit run chat.py
```

The app auto-refreshes data in the background when it's older than 7 days.

### Optional: Generate static charts

```bash
python3 analyze.py
# Saves 12 PNG charts to output/
```

## Usage

### Chat
Ask anything about YC companies in plain English:
- *"Which B2B SaaS companies from W23 are still actively hiring?"*
- *"How many YC companies are focused on climate tech?"*
- *"Compare team sizes between S21 and S24 cohorts"*

### Research
Select any company from the dropdown and click **Run Research**. Four specialist agents run in parallel and produce a report covering recent news, competitors, funding history, and community sentiment.

### Dashboard
Navigate to **Dashboard** in the sidebar for interactive market charts. Useful for spotting trends across industries, geographies, and cohorts.

## Data

- **Source**: [YC OSS API](https://yc-oss.github.io/api/companies/all.json) (public, no auth required)
- **Coverage**: ~5,700 companies, batches S05–S26
- **Update cadence**: Auto-refreshes in the background every 7 days

## Tech Stack

| Component | Technology |
|-----------|-----------|
| UI | Streamlit |
| AI | Claude claude-sonnet-4-6 (Anthropic) |
| Vector search | ChromaDB + `all-MiniLM-L6-v2` |
| Structured queries | SQLite |
| Charts | Plotly + Matplotlib |
| Data source | YC OSS API |

## Project Structure

```
├── chat.py              # Main Streamlit app (chat UI)
├── ingest.py            # One-time ETL: API → SQLite + ChromaDB
├── analyze.py           # Static chart generator
├── utils.py             # Shared helpers (DB, caching, refresh)
├── research_agents.py   # Parallel specialist research agents
├── pages/
│   ├── research.py      # Company deep-research page
│   └── dashboard.py     # Market opportunity dashboard
├── requirements.txt
├── .env.example
└── CLAUDE.md            # Architecture notes for Claude Code
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
