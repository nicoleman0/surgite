# AGENTS.md

This file provides guidance to AI coding assistants (Claude Code, Cursor, Copilot, etc.) when working with code in this repository.

## Project principles

This project is deliberately minimalist and easy to maintain. When suggesting or writing code, hold the line on:

- **No dead code, no bloat, no "just in case" branches.** If a check, parameter, or abstraction has no real caller or scenario today, leave it out.
- **No duplicated logic.** If two places do the same thing, extract a shared helper rather than copy-pasting. Prefer one source of truth per behaviour.
- **Use dataclasses (or Pydantic models at boundaries) for structured data** rather than ad-hoc dicts/tuples — they document the shape and make refactors safe.
- **Fewer lines is better, all else equal.** Prefer the simplest version that handles real cases; expand only when reality demands it.

## Commands

```bash
# Install / sync dependencies (uses uv, no requirements.txt)
uv sync

# Print the installed version
uv run surgite --version

# Run the CLI
uv run surgite /path/to/repo --since 7.days.ago
uv run surgite /path/to/repo --since 2026-05-01 --summarize     # requires a provider API key
uv run surgite /path/to/repo --since 2026-05-01 --output out.txt
uv run surgite /path/to/repo --since 2026-05-01 --until 2026-05-10
uv run surgite /path/to/repo --since 7.days.ago --author "Alice"
uv run surgite /path/to/repo --since-commit abc1234               # range from a commit

# Install as a global tool (makes `surgite` available anywhere)
uv tool install .

# Start the Postgres dev database
docker compose up -d

# Run migrations
uv run alembic upgrade head

# Start the API
uv run uvicorn surgite.api:app --reload --host "${API_HOST:-127.0.0.1}" --port "${API_PORT:-8000}"

# Run tests
uv run pytest

# Lint and format (ruff). CI runs `ruff check .` and `ruff format --check .`
uv run ruff check .          # lint (add --fix to auto-fix)
uv run ruff format .         # format in place

# Regenerate the OpenAPI snapshot baseline (run after changing a route).
task openapi-snapshot
# Local equivalent of the CI snapshot gate (run before pushing).
task openapi-snapshot-check

# Frontend type-check and unit tests (CI runs these alongside the build)
cd frontend && npm run check
cd frontend && npm run test    # vitest
```

## Architecture

This is a Python CLI tool and FastAPI REST API for generating standup summaries from git history. The CLI is standalone (prints/writes formatted log, optionally AI-summarized). The API persists commits in Postgres and exposes them with filtering and summary endpoints.

**Data flow:**

```
CLI:  git.get_raw_log()  →  git.parse_log()  →  formatter.format_log()  →  stdout / file
                                                                        ↘  summarizer.summarize_commits()  →  LLM provider

API:  POST /repos  →  create repo + per-repo BackgroundTask ingest (clone/fetch + git log → CommitRow upsert)
      POST /repos/{id}/ingest → owner-scoped manual BackgroundTask ingest (409 if already active)
      (lifespan)  →  background asyncio task runs _ingest_all_repos() every INGEST_INTERVAL seconds
      GET /summary  →  SQLAlchemy query (pure read; freshness owned by the scheduler above)  →  JSON
      GET /summary?ai=true[&provider=]  →  formatter.format_log  →  summarizer (async, httpx)  →  LLM provider (rate-limited)
      GET /summary/stream  →  same, streamed token-by-token as SSE
      GET /commits, /commits/{hash}, /providers  →  SQLAlchemy query / provider registry  →  JSON
      POST/GET /summaries, GET /s/{slug}  →  shareable summary links
```

- `surgite/models.py` — `Commit` dataclass (`hash`, `date`, `author`, `message`, optional `repo`, `ingested_at`)
- `surgite/git.py` — runs `git log` via subprocess; `get_raw_log()` supports `since`, `until`, `author`, and `since_commit` (auto-detects whether `until` is a git ref); `parse_log()` returns `list[Commit]`
- `surgite/formatter.py` — formats `Commit` objects to `[date] message (author) <short_hash>` strings
- `surgite/summarizer.py` — model-agnostic summarization. A `Provider` dataclass + `PROVIDERS` registry support **anthropic** (Messages API, default), **groq**, **deepseek**, and **local** (a self-hosted OpenAI-compatible server; `LOCAL_BASE_URL` is required, there's no default). `local` also ships no default model: `resolve_model()` asks the server via `GET /models` when `LOCAL_MODEL` is unset, caching per base_url in `_discovered_models`, and raises rather than guessing when the server lists zero or several. `LLM_LOCAL_ONLY=1` filters the registry down to `local` at import via `_visible()`, which every consumer inherits (`/providers`, key validation, `/health/deep`). Every provider's `base_url` is an env-overridable property, same call-time pattern as `api_key`/`model()`; all calls go over plain HTTP via `httpx.AsyncClient` (no provider SDK). It's **async**: `generate_summary()` is a coroutine returning `{summary, provider, model}`; `generate_summary_per_repo()` fans out concurrently over `asyncio.gather` with a bounded semaphore (and accepts `settings_by_repo` for per-repo prompt overrides); `stream_summary()` is an async generator of text deltas for SSE; `summarize_commits()` is the CLI's sync text-only wrapper (drives the coroutine via `asyncio.run`); `provider_status()` powers `GET /providers`. Provider is chosen by `LLM_PROVIDER` or per call; identity injected via `SURGITE_USER`/`SURGITE_ROLE`
- `surgite/cli.py` — argparse CLI entry point; registered as the `surgite` console script in `pyproject.toml`. A local `repo_path` runs offline; `--registered <name>` instead pulls a repo from a running API (`SURGITE_API_URL` / `SURGITE_API_TOKEN`)
- `surgite/config.py` — loads `DATABASE_URL` (required), `API_HOST`, `API_PORT`, `SHARE_TTL_DAYS` from env via `python-dotenv` (provider keys are read in `summarizer` at call time)
- `surgite/db.py` — SQLAlchemy engine, `Base`, ORM models: `CommitRow`, `RepoRow`, `PromptSettingsRow` (now with a nullable `repo_id` FK — one row per repo plus a global `NULL` row), `SharedSummaryRow` (slug → params JSON + expiry), and the `get_session()` factory
- `surgite/schemas.py` — Pydantic request models (`RepoCreate`, `PromptSettingsUpdate`, `ShareCreate`)
- `surgite/api.py` — FastAPI app with routes:
  - `POST /repos` — creates a repo and immediately ingests it as a `BackgroundTask` (clone + git log → CommitRow upsert); ingest failures are logged but don't block creation. A lifespan-managed scheduler task also runs `_ingest_all_repos` every `INGEST_INTERVAL` seconds so the DB stays fresh without anyone hitting `/summary`. Completed outcomes populate `last_ingest_attempt_at` and `last_ingest_error`; successful completion also updates `last_ingested_at`. `POST /repos/{repo_id}/ingest` queues an owner-scoped manual sync, with a process-local reservation preventing duplicate work.
  - `GET /commits` — paginated list with `since`/`until`/`author`/`repo`/`limit`/`offset` filters
  - `GET /commits/{hash}` — lookup by full or prefix hash; 400 for invalid hex, 404 for not found, 409 for ambiguous prefix
  - `GET /summary` — async, pure read against the DB; aggregates by repo and day. `ai=true` runs the summarizer (capped at `AI_SUMMARY_MAX_COMMITS = 500` to bound token cost) behind per-user and per-IP-outer rate limits; optional `provider=` overrides the default; per-repo summaries use that repo's prompt settings, falling back to the global default. The raw `commits` list is omitted unless `commits=true`. Unknown provider or missing key → 400; provider HTTP failure → 502; rate limit exceeded → 429
  - `GET /summary/stream` — the AI summary as Server-Sent Events: a `meta` frame (stats), per-repo `delta` token frames, a `repo_done`/`repo_error` per repo, then `done`. Same preconditions as `/summary?ai=true`
  - `GET /providers` — lists providers, their default model, and whether each has a key configured (for a UI/CLI to offer a choice)
  - `GET`/`PUT /settings/prompt` — read/update prompt settings; `?repo_id=` scopes to one repo (GET falls back to the global row; PUT 404s on an unknown repo)
  - `POST /summaries` — persist the current summary params behind a slug; `GET /summaries/{slug}` resolves it (404 once expired); `GET /s/{slug}` serves the SPA shell for the read-only share view
  - `GET /health/deep` — DB + `git ls-remote` against one registered repo + provider reachability; 503 names the failing component (`no_repos`/`missing_key` aren't failures)
  - `SQLAlchemyError` is mapped to a 503 globally
- `surgite/logging_config.py` — `configure_logging()` sets the root logger from `LOG_LEVEL` (default INFO) and `LOG_FORMAT` (default human-readable; set to `json` for log-shipping-friendly output). Extras on a `LogRecord` are flattened into top-level JSON keys.
- `surgite/rate_limit.py` — hand-rolled per-user token bucket for AI summaries (default 5 req / 60 s) plus a per-IP outer backstop (default 100 req / 60 s). `X-Forwarded-For` is only trusted when the direct peer is listed in `TRUSTED_PROXIES` — otherwise a client can mint a fresh bucket per request by varying the header. Set it to your reverse proxy's address; the project sits behind Traefik in production.
- `scripts/backup.sh` / `scripts/restore.sh` — `pg_dump` / `psql` over `docker compose exec db` by default; `BACKUP_MODE=local` for a host-side Postgres. `backup.sh` rotates `BACKUP_KEEP` (default 14) dated dumps.
- `alembic/` — migrations; `e5e311c2e5f0_create_commits_table.py` is the initial schema
- `tests/` — pytest suite covering the API; `conftest.py` swaps in a temp SQLite DB and clears tables between tests

- `frontend/` — SvelteKit SPA (Svelte 5 + Tailwind v4) for managing repos and generating summaries; built to static assets and served same-origin by FastAPI via the `StaticFiles` mount at the end of `surgite/api.py`

## Roadmap

- Release history lives in [CHANGELOG.md](CHANGELOG.md). The release process (version bump, tag, PyPI publish) is in [RELEASING.md](RELEASING.md). No open roadmap doc — file an issue for new work.

## Environment Variables

| Variable | Notes |
|---|---|
| `DATABASE_URL` | **Required.** `postgresql://surgite:surgite@localhost:5432/surgite` for local dev; the test suite overrides this with a temp SQLite file in `tests/conftest.py` |
| `LLM_PROVIDER` | Summary provider: `anthropic` (default), `groq`, `deepseek`, or `local` |
| `ANTHROPIC_API_KEY` / `GROQ_API_KEY` / `DEEPSEEK_API_KEY` / `LOCAL_API_KEY` | Key for the chosen provider; required for `--summarize` and `GET /summary?ai=true` |
| `ANTHROPIC_MODEL` / `GROQ_MODEL` / `DEEPSEEK_MODEL` / `LOCAL_MODEL` | Optional per-provider model override (defaults: `claude-haiku-4-5-20251001`, `llama-3.1-8b-instant`, `deepseek-chat`; `local` has none — it asks the server) |
| `LOCAL_BASE_URL` | **Required for `local`.** API root of a self-hosted OpenAI-compatible server, e.g. `http://llm.internal:8000/v1` — the code appends `/chat/completions`. The hosted providers take `ANTHROPIC_BASE_URL` / `GROQ_BASE_URL` / `DEEPSEEK_BASE_URL` the same way |
| `LLM_LOCAL_ONLY` | `1` leaves only `local` in the registry, so nothing can reach a host outside the network. Read once at import |
| `API_HOST` | Defaults to `127.0.0.1` |
| `API_PORT` | Defaults to `8000` |
| `INGEST_INTERVAL` | Seconds between automatic background ingests of all registered repos. Set to `0` to disable the scheduler. |
| `LOG_LEVEL` | Root logger level (default `INFO`). |
| `LOG_FORMAT` | Set to `json` for structured logs (Loki / vector / fluentbit); default is human-readable. |
| `SUMMARY_RATE_LIMIT_REQUESTS` / `SUMMARY_RATE_LIMIT_WINDOW_SECONDS` | Per-user AI-summary guard (default `5` / `60`). |
| `IP_OUTER_RATE_LIMIT_REQUESTS` / `IP_OUTER_RATE_LIMIT_WINDOW_SECONDS` | Per-IP AI-summary backstop (default `100` / `60`). |
| `TRUSTED_PROXIES` | Comma-separated IPs / CIDRs whose `X-Forwarded-For` is trusted for rate-limit bucketing (default empty: header ignored, direct peer used). Set this when running behind a reverse proxy. |
| `SHARE_TTL_DAYS` | Lifetime of a shared-summary `/s/<slug>` link (default `7`). |
| `SECRETS_ENCRYPTION_KEY` | Fernet master for at-rest provider-key encryption. Generated on first run if unset. |
| `SECRETS_KEY_FILE` | Where the generated fallback master key is written (default: `.secrets_key` at the project root). Must be durable storage — compose points it at the `surgite-data` volume, because the default path is inside the container's image layer. |
| `SURGITE_API_URL` / `SURGITE_API_TOKEN` | Target API + optional bearer for the CLI's `--registered` mode (default URL `http://localhost:8000`). |
| `SURGITE_USER` | Name injected into the summarizer prompt (e.g. `Alice`); defaults to `the developer` |
| `SURGITE_ROLE` | Optional role description (e.g. `backend engineer at Acme`) appended to the identity in the prompt |

The CLI loads `.env` via `python-dotenv` on startup (and `surgite.config` does the same for the API).
