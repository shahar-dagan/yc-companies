# Contributing

Thanks for your interest in contributing! This is a small, focused project — contributions that keep it simple and useful are most welcome.

## Getting Started

1. Fork the repo and clone your fork
2. Follow the setup steps in [README.md](README.md)
3. Create a branch: `git checkout -b your-feature-name`

## What to Work On

Good areas for contributions:

- **New dashboard charts** — additional views into the YC dataset
- **Chat improvements** — better tool definitions, prompt tuning, example questions
- **Research agent depth** — additional specialist agents or better synthesis prompts
- **Data quality** — better parsing of batch labels, locations, tags
- **Performance** — faster ingest, smarter caching

If you're unsure whether something is in scope, open an issue first.

## Development Guidelines

- **Keep it simple.** This project has no build system, no test suite, no CI. Don't introduce unnecessary complexity.
- **One script, one job.** `ingest.py`, `analyze.py`, and `chat.py` are intentionally independent. Avoid coupling them further.
- **No secrets in code.** All API keys go in `.env` (gitignored). Never hardcode credentials.
- **ChromaDB constraints.** Metadata values must be `str`, `int`, or `float` — never `None` or `list`. Use `safe_str`/`safe_int` helpers from `ingest.py`.
- **SQLite booleans.** Store as `0`/`1` integers, not Python bools.

## Submitting a PR

1. Make sure the app runs end-to-end (`ingest.py` → `streamlit run chat.py`)
2. Test any changed pages manually
3. Keep the diff focused — one thing per PR
4. Write a clear PR description explaining what changed and why

## Reporting Issues

Open a GitHub issue with:
- What you expected
- What happened instead
- Steps to reproduce
- Python version and OS

## Questions

Open an issue with the `question` label.
