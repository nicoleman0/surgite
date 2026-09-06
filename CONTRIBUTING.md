# Contributing to surgite

Thanks for looking. surgite is a small, deliberately boring codebase: ~15
Python source files, a SvelteKit SPA, and a test suite that never touches the
network. Contributions of all sizes are welcome — bug reports, docs, tests,
and features.

The canonical repository is on GitHub:
<https://github.com/nicoleman0/surgite>. The Forgejo and Codeberg copies are
read-only mirrors; pull requests opened there will not be seen.

## Ground rules

**Fewer lines is better, all else equal.** The whole point of the project is
that it reads top-to-bottom. No SPA framework beyond SvelteKit, no slowapi, no
Celery, no Redis — the rate limiters are hand-rolled token buckets in
`surgite/rate_limit.py` and the ingest scheduler is one `asyncio` task in the
FastAPI lifespan. A change that adds surface area should earn it.

**No new runtime dependency without a reason nobody can argue with.** Every
package added is a supply-chain surface, a pip-audit finding waiting to
happen, and a reason for a self-hoster not to run this. If a change seems to
need one, it almost certainly needs a smaller change instead. Dev tooling
lives in the `dev` dependency *group*, which `pip install surgite` can never
pull in.

**No dead code, no "just in case" branches.** If a check, parameter, or
abstraction has no real caller today, leave it out.

**No duplicated logic.** Two places doing the same thing means one shared
helper. One source of truth per behaviour.

**Structured data gets a type.** Dataclasses internally (`Commit`,
`Provider`), Pydantic models at the HTTP boundary (`surgite/schemas.py`) —
not ad-hoc dicts and tuples.

**Tests never touch the network.** The suite must pass on a machine with no
route out. Provider calls go through `httpx.MockTransport` — see
`_mock_client()` in `tests/test_summarizer.py`. `tests/conftest.py` forces
every provider key to the empty string so a developer's real `.env` cannot
leak into a test run. A test that makes a real request is a flaky test and
will be rejected.

**Secrets are a correctness domain, not a feature area.** Per-user provider
keys are Fernet-encrypted at rest and never returned by the API. If a change
touches `surgite/secrets.py`, `surgite/auth.py`, or a route that handles a
key, say so explicitly in the PR description — 1.0.2 shipped because a
misplaced master key silently destroyed every stored key on redeploy.

When in doubt, open an issue to discuss before building something large.
[AGENTS.md](AGENTS.md) is the architecture overview and the canonical command
list; skim it before writing code.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — manages Python 3.14+ and dependencies
- [Task](https://taskfile.dev) — the `task` binary, for the OpenAPI snapshot
  recipes. `brew install go-task` (macOS),
  `go install github.com/go-task/task/v3/cmd/task@latest` (Go), or
  `uv tool install go-task-bin` (anywhere uv runs). See
  [Taskfile.yml](Taskfile.yml) for the full list. Preferred over a Makefile:
  declarative YAML, no shell-escaping gotchas, and `sources:` skips tasks that
  are already up to date.
- Node.js 22+ — for the frontend
- Docker + Docker Compose — for Postgres and the full-app path

## Development setup

### Backend / CLI

```bash
uv sync --group dev               # install deps incl. dev tools
docker compose up -d db           # start Postgres
uv run alembic upgrade head       # run migrations
uv run uvicorn surgite.api:app --reload   # API at http://127.0.0.1:8000
```

Run the CLI without any of the above — it is standalone and needs no database:

```bash
uv run surgite /path/to/repo --since 7.days.ago
```

### Frontend

```bash
cd frontend
npm install
npm run dev                       # Vite dev server on :5173, talks to the API on :8000
```

### Full app in one command

```bash
docker compose up --build         # http://localhost:8000
```

`.env.example` documents every configuration variable with its default.

## Running the tests

```bash
uv run pytest                     # backend
cd frontend && npm run test       # vitest
```

`tests/conftest.py` points `DATABASE_URL` at a temp SQLite file and clears the
tables between tests, so the backend suite needs neither Postgres nor a
running API. It sets those env vars *before* importing anything under
`surgite.`, because `surgite.config` reads the environment and `surgite.db`
builds the engine at module-load time — a new fixture that imports earlier
will break in ways that look unrelated.

`asyncio_mode = "auto"`, so async summarizer tests are plain `async def` with
no marker. The API tests stay synchronous; `TestClient` drives the async
endpoints for you.

If you add behaviour, add a test for it. If you fix a bug, add the test that
would have caught it.

## Code style

```bash
uv run ruff check .               # add --fix to auto-fix
uv run ruff format .
uv run mypy surgite/
```

All three are CI gates and all three must be clean. Unlike the linters,
`ruff format` *is* used here — run it rather than hand-aligning, and don't
send a PR whose diff is mostly reflow.

`mypy` covers `surgite/` only. Tests and scripts are not type-checked.

## The CI gates

Every pull request, and every push to `main`, runs six jobs, all of which
must be green
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)). They are split by
concern so that one failure still leaves you the other five results:

| Gate | What it runs | Described in |
| --- | --- | --- |
| `lint` | `uv lock --check`, `ruff check`, `ruff format --check`, `mypy surgite/` | [Code style](#code-style) |
| `test` | `pytest -q` | [Running the tests](#running-the-tests) |
| `audit` | `pip-audit` | below |
| `frontend` | `npm run check` (svelte-check: types + a11y), `npm run test`, `npm run build` | [Frontend](#frontend) |
| `snapshot` | `task openapi-snapshot-check` | [The OpenAPI snapshot](#the-openapi-snapshot) |
| `image` | `docker build`, then boots the compose stack and checks the container runs unprivileged | below |

`uv lock --check` is the one that surprises people: if you touch
`pyproject.toml` at all — including the version — run `uv lock` and commit the
result, because the lockfile records the project version too.

`pip-audit` runs against the exported *runtime* dependency set, so a
vulnerable dev tool will not fail the build but a vulnerable runtime package
will. Dependabot ([`.github/dependabot.yml`](.github/dependabot.yml)) opens
the bumps.

## The OpenAPI snapshot

The committed `docs/openapi.json` is the canonical baseline for the public API
surface. The `snapshot` CI job regenerates the dump on every CI run and fails if
the committed baseline has drifted. This is the API-stability promise in
[`docs/api-stability.md`](docs/api-stability.md): a route in the baseline
keeps existing, byte-for-byte, in the dump.

If you change a route, a response model, or anything else that reaches
`app.openapi()`:

1. Run `task openapi-snapshot` to regenerate `docs/openapi.json`.
2. Commit it alongside the route change.
3. Push — the gate re-runs and passes.

The failure log prints these steps inline, so a red `snapshot` job tells you
what to do without coming back here. What it cannot tell you is whether the
drift was *intended*: a removed route or a renamed field is a breaking change
under the stability policy and needs a major version, not a regenerated
baseline. Regenerating to silence the gate is exactly the mistake the gate
exists to catch.

## Database migrations

Schema changes are Alembic migrations in `alembic/versions/`, one revision per
change, and the ORM models in `surgite/db.py` are the source they describe.
Autogenerate the revision, then read it before committing — Alembic is good at
tables and columns and unreliable about constraints, index names, and server
defaults:

```bash
uv run alembic revision --autogenerate -m "short description"
uv run alembic upgrade head
```

Check that `downgrade()` is real, not a stub. Self-hosters roll back.

The backend test suite runs against SQLite while production is Postgres, so a
migration that leans on a Postgres-only DDL feature can pass CI and fail on a
real deployment. Keep the DDL boring, and if you cannot, exercise the
migration against the compose Postgres before sending the PR.

## Adding a provider

Summarization is model-agnostic: a frozen `Provider` dataclass plus the
`PROVIDERS` registry in `surgite/summarizer.py`, with every call made over
plain HTTP with `httpx` — no provider SDK, which is why adding one is a few
lines and not a dependency. A new provider is a `Provider(...)` entry naming
its `kind` (`"openai"` for a chat-completions-compatible API, `"anthropic"`
for the Messages API), base URL, key env var, model env var, and default
model. Adding a genuinely new wire format means a new `kind` branch, which is
a bigger change — open an issue first.

Add tests against `httpx.MockTransport`, document the env vars in
`.env.example`, `README.md`, and the AGENTS.md table, and check that
`GET /providers` reports the new entry correctly for both the per-user key
path and the env-var fallback.

## Pull requests

Work from a branch, not from your fork's `main`. A PR opened from `main`
cannot be updated without moving your fork's default branch, and it tangles
the next change you send with this one. Name it for what it does:
`feat/short-description`, `fix/short-description`, `docs/short-description`.

`main` is protected — pull requests are required and the rule is enforced for
admins, so a direct push is rejected with `GH013` *after* the commit already
exists locally. Move it to a branch rather than trying to force it through.
This applies to release commits too; see [RELEASING.md](RELEASING.md).

Commits follow [Conventional Commits](https://www.conventionalcommits.org/):

```
fix(secrets): persist the generated master key outside the image layer
feat(api): add per-repo prompt settings
docs(readme): reference the demo GIF by absolute URL
ci: coalesce concurrent mirror runs
```

Pull requests are merged with a merge commit, not squashed, so every commit
message on the branch lands in `main`'s history verbatim and each one has to
stand on its own. `fix:` and `feat:` are the two that carry weight — they
decide whether the next release is a patch or a minor. `docs:`, `test:`,
`refactor:`, `build:`, `chore:` and `ci:` cover changes no changelog entry
needs to mention.

Then:

1. Keep the PR focused — one logical change is easier to review and easier to
   revert.
2. Update the docs the change invalidates: `README.md` for users, `AGENTS.md`
   for architecture, `.env.example` for a new setting, `docs/self-host.md` for
   anything an operator has to do by hand.
3. Add a `CHANGELOG.md` entry under `[Unreleased]` for anything a user or an
   operator would notice. Say what broke and what it means for someone
   already running it, not just what you changed.
4. Open it against `main` and get CI green.

## Reporting bugs and requesting features

There are no issue templates — write prose. The most useful bug report is the
exact command or request, what happened, what you expected, and the version
(`surgite --version`, or the image tag). For the API, the status code and the
response body; for a self-hosted deployment, whether you are on compose,
plain Docker, or a local uv install, since the three differ in exactly the
places bugs hide.

**Do not open a public issue for anything security-sensitive** — see
[SECURITY.md](SECURITY.md) for the private reporting path, and
[`docs/security-support.md`](docs/security-support.md) for supported versions
and the response SLA.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions will be licensed under the
project's [MIT License](LICENSE).
