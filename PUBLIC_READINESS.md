# Public repository readiness checklist

Use this before making the repo public.

## Secrets

- [x] **`.env` is in `.gitignore`** — Confirmed; local `.env` is never tracked.
- [x] **No real API keys in code** — `ANTHROPIC_API_KEY` is read only from `os.environ.get(...)`; never hardcoded or logged.
- [x] **`.env.example`** — Contains only the placeholder `ANTHROPIC_API_KEY=sk-ant-...`; no real key.
- [x] **No long `sk-ant-...` strings** — Grep for real-looking keys found no matches in tracked files.
- [x] **CI (`.github/workflows/ci.yml`)** — No secrets; only ruff + mypy, no API keys.
- [x] **Docker** — `docker-compose.yml` references `env_file: .env` for local runs; the file itself is not in the repo.

## Documentation

- [x] **README** — Setup tells users to copy `.env.example` to `.env` and add their key; Security section points to SECURITY.md.
- [x] **SECURITY.md** — Explains no committing secrets, SQL/input considerations, and how to report issues.
- [x] **CONTRIBUTING.md** — Already states "No secrets in code; all API keys go in `.env`."

## Optional before going public

- [ ] Run `git log -p` (or GitHub secret scanning) to ensure `.env` or real keys were never committed in history. If they were, use `git filter-repo` or BFG to remove and rotate the key.
- [ ] On GitHub: enable "Push protection" for secrets in repo settings if available.
- [ ] Consider adding a one-line note in README: "This repo is open source; you must add your own API key to use the chat and research features."

---

**Verdict:** Safe to make public as-is. No secrets are in tracked files; all keys are loaded from the environment at runtime.
