# Security

This document lists security considerations for running and contributing to YC Companies.

## Secrets and API keys

- **Never commit secrets.** All API keys (e.g. Anthropic) must be provided via environment variables or a local `.env` file.
- **`.env` is gitignored.** Copy `.env.example` to `.env` and fill in values only on your machine. Do not add `.env` to version control.
- The app reads `ANTHROPIC_API_KEY` from the environment only. Keys are never logged, displayed in the UI, or written to the database.
- Research agents use the [Orthogonal](https://orthogonal.io) CLI (`orth run`) for Exa/Nyne; credentials are managed by the Orthogonal CLI, not stored in this repo.

## Things to review

- **SQL execution:** The chat `query_database` tool allows only `SELECT` statements (enforced by checking the first token). Avoid adding support for raw user-supplied SQL beyond read-only queries.
- **Input handling:** User and assistant messages are persisted to SQLite and sent to the Claude API. No sanitization beyond Streamlit’s default escaping; consider limits on message size or content if exposing the app publicly.
- **CORS and deployment:** For public deployments, enable XSRF protection and restrict CORS in Streamlit server config (see `.streamlit/config.toml`).
- **Dependencies:** Keep `requirements.txt` and virtual env up to date; run `pip audit` or similar periodically.

## Reporting issues

If you find a security issue, please report it privately (e.g. via GitHub Security Advisories or maintainer contact) rather than opening a public issue.
