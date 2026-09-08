# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository responses now report the latest completed ingest attempt and a
  safe failure message. Each repository row also offers a manual sync action;
  `POST /repos/{repo_id}/ingest` queues owner-scoped work while the existing
  scheduler remains responsible for routine freshness.

## [1.4.0] - 2026-09-06

### Changed

- **Git TLS verification is now on by default in the shipped compose file.**
  *Action required on upgrade if you clone from a git host with a
  self-signed or private-CA certificate.*
  `docker-compose.yml` previously set `GIT_SSL_NO_VERIFY: "1"`, disabling
  certificate verification for every `git clone`/`fetch`/`ls-remote` the app
  performs. It is now `${GIT_SSL_NO_VERIFY:-0}` (verify by default).
  Deployments that relied on the old behaviour must set
  `GIT_SSL_NO_VERIFY=1` in `.env`; deployments with a private-CA git host
  should mount the CA bundle and set `GIT_SSL_CAINFO` instead. See
  [`docs/self-host.md`](docs/self-host.md). (#20)

### Fixed

- **The help overlay is keyboard accessible.** It is a native modal
  `<dialog>` now: focus moves into it on open, Tab and Shift+Tab stay
  inside it, the page behind goes inert, and focus returns to whatever
  opened it on close. The close control has an accessible name. Previously
  the overlay declared `aria-modal="true"` while doing none of that. (#31)

### Security

- **The container no longer runs as root.** The image creates an unprivileged
  `surgite` user; `entrypoint.sh` repairs the ownership of the two writable
  mounts (`/var/surgite/repos`, `/var/surgite/data`) when it starts as root,
  then drops to that user with `setpriv` before running migrations and the
  app. Existing deployments upgrade without manual `chown` — the entrypoint
  handles it on first start. See [`docs/self-host.md`](docs/self-host.md). (#21)

## [1.3.2] - 2026-09-04

### Fixed

- **The release workflow's image job now sets up buildx** before building, so
  it can export its layer cache. The runner's default `docker` driver cannot,
  and errors out rather than skipping the cache — which meant 1.3.1 published
  a wheel but no container image.

## [1.3.1] - 2026-09-04

### Added

- **Releases publish a container image to `ghcr.io/nicoleman0/surgite`.** The
  release workflow builds `Dockerfile` and pushes `X.Y.Z`, `X.Y` and `latest`
  (amd64). Self-hosters can skip the source build by setting
  `APP_IMAGE=ghcr.io/nicoleman0/surgite:latest` in `.env`, which is also what
  makes the documented `docker compose pull` upgrade path do anything.

## [1.3.0] - 2026-09-03

### Added

- **Summaries can run against a self-hosted model.** A `local` provider talks
  to any server speaking the OpenAI chat API — vLLM, Ollama, LM Studio,
  llama.cpp, a LiteLLM proxy — so a deployment can keep commit data on its own
  network. Set `LOCAL_BASE_URL` (the API root; there is no default) and
  `LOCAL_API_KEY`. `LOCAL_MODEL` is optional: left unset, the app asks the
  server what it serves via `GET /models` and caches the answer, so a box
  serving one model needs no model config at all. It raises rather than
  guessing when the server lists none or several.
- **`LLM_LOCAL_ONLY=1` drops the hosted providers from the registry** at
  startup, for networks that must not reach a third party. They can then not
  be selected, cannot have per-user keys stored against them, and are not
  contacted by `/health/deep`'s reachability probe.
- **Every provider's base URL is overridable** — `ANTHROPIC_BASE_URL`,
  `GROQ_BASE_URL`, `DEEPSEEK_BASE_URL`, `LOCAL_BASE_URL` — for a corporate
  proxy or an API gateway in front of a hosted provider.

### Changed

- `Provider.base_url` is now a call-time property reading `{PROVIDER}_BASE_URL`;
  the literal moved to a `default_base_url` field and a `url_env` field names
  the variable. Internal — `surgite/__init__.py` exports nothing and the HTTP
  surface is unchanged — but a fork that constructs `Provider(...)` or edits
  the `PROVIDERS` registry needs the two new field names.

### Removed

- The unused `surgite/scope.py`. Nothing imported it, and the org scoping it
  sketched is not a shipped feature. (#48)

## [1.2.0] - 2026-09-03

Released entirely from community pull requests. Thanks to
[@anfine](https://github.com/anfine), [@Alesia0411](https://github.com/Alesia0411)
and [@fwh888](https://github.com/fwh888), who between them contributed every
change below.

### Security

- **The per-IP rate limits no longer trust a client-supplied
  `X-Forwarded-For`.** Any client could mint a fresh bucket per request by
  varying the header, which made both per-IP limits advisory rather than
  enforced. The header is now honoured only when the direct peer is listed in
  the new `TRUSTED_PROXIES` setting (comma-separated IPs or CIDR ranges); by
  default it is ignored and the direct peer address is used. **Deployments
  behind a reverse proxy must set `TRUSTED_PROXIES`** — otherwise every client
  shares one bucket keyed on the proxy's own address. See
  [`docs/self-host.md`](docs/self-host.md). (#39, @fwh888)

### Added

- A PEP 561 `py.typed` marker, so downstream type checkers see the
  annotations that were already shipped. (#16, @fwh888)

### Fixed

- **Ingest no longer truncates the history of busy repos.** Cloning at
  `--depth 100` silently dropped commits for any repo with more than 100 in
  the query window, so `total_commits` was confidently wrong. Clones now use
  `--filter=blob:none`: the full commit history arrives and file contents are
  fetched only on demand, which is what ingest never needs. (#34, @fwh888)
- **A repo that renamed its default branch is picked up on the next ingest.**
  `git fetch` does not update `origin/HEAD`, so ingest kept reading the old
  branch (master after a rename to main) or failed with an unhelpful
  `CalledProcessError`. (#35, @fwh888)
- **`docker stop` is graceful again.** The container's `CMD` was shell form,
  so uvicorn ran under `/bin/sh -c` and never received `SIGTERM`: every stop
  burned the full 10-second grace period and killed in-flight SSE
  connections. An entrypoint script now `exec`s uvicorn as PID 1. (#36,
  @fwh888)
- `--output` writes UTF-8 explicitly. The platform default on Windows is
  cp1252, which raises on any non-Latin-1 character in a commit message,
  an author name, or an LLM-generated summary. (#19, @fwh888)
- `--since 7.days.ago` works with `--registered`. Git's relative date syntax
  is what `--help` advertises, but it was passed straight to the API, which
  filters by ISO date; the mismatch surfaced as an unhandled `httpx`
  traceback. (#29, @fwh888)
- `Taskfile.yml` picks the interpreter by platform, so the snapshot tasks run
  for Windows contributors. (#33, @fwh888)

### Removed

- The unused legacy per-IP summary rate limiter, along with the
  `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW_SECONDS` environment
  variables. The function had not been called since the slice 2
  consolidation, so the two variables configured nothing; the active
  per-user limit and per-IP outer backstop are unchanged. (#30, @Alesia0411)

### Changed

- The Docker build installs dependencies from `pyproject.toml`/`uv.lock` and
  the frontend's from `package.json`/`package-lock.json` before copying
  source, so editing a Python or Svelte file no longer reinstalls every
  dependency on the next `docker compose up --build`. (#32, @anfine)

## [1.1.0] - 2026-08-31

### Added

- `surgite --version` prints the installed version and exits. The version is
  read from package metadata, so it stays in the one place `pyproject.toml`
  already keeps it and needs no bump at release time. (#15)

### Fixed

- The demo GIF is now referenced by absolute URL, so it renders on PyPI as
  well as GitHub. A relative path only resolves against the repository, and
  PyPI has no repository to resolve it against — the same reason the shields
  badges (absolute URLs) always worked there. PyPI freezes a release's
  description at upload, so 1.1.0 is the first whose project page renders it.

## [1.0.2] - 2026-08-31

### Fixed

- **Compose deployments silently destroyed every stored provider key on
  redeploy.** `SECRETS_ENCRYPTION_KEY` was not passed through in
  `docker-compose.yml`, so the app fell back to generating a Fernet master
  key on first start — into `/app/.secrets_key`, an image layer with no
  volume behind it. Two starts of the same image produced two different
  keys, so every `provider_keys` row encrypted under the old one became
  permanently undecryptable the next time the container was replaced.
  Compose now passes `SECRETS_ENCRYPTION_KEY` through and points the new
  `SECRETS_KEY_FILE` at the `surgite-data` volume, so the generated fallback
  survives a restart. **Existing compose users: read the upgrade note in
  [`docs/self-host.md`](docs/self-host.md) before pulling this** — rescue
  your running key first or you will lose the stored keys yourself.
- **`.dockerignore` let `.secrets_key` into the build context.** The
  `Dockerfile` does `COPY . .`, so anyone building an image from a checkout
  where the app had run locally baked their own Fernet master key into it,
  and pushed it to whatever registry the image went to. Also excludes
  `node_modules/`, build output and tool caches, which cuts the build
  context from ~135 MB to ~1.4 MB.

### Added

- `SECRETS_KEY_FILE` overrides where the generated fallback master key is
  written, for deployments where the project root is not durable storage.
  Defaults to `.secrets_key` next to the project root, so existing installs
  are unaffected. `scripts/rotate-secrets.sh` honours it too.

## [1.0.1] - 2026-08-31

A same-day patch on 1.0.0: API keys issued by that release could be born
unusable. Keys that already verify are unaffected and keep working — the
parsing is unchanged — but a key that 401'd on first use was never valid and
has to be reissued.

### Fixed

- **API keys: ~8% of issued keys were unusable.** The key prefix was drawn
  from `secrets.token_urlsafe`, whose alphabet contains `_` — and a `_` in
  the prefix broke verification's `split("_", 2)` prefix reassembly, so the
  affected keys 401'd on first use. Prefixes are now hex (`sk_` + 8 hex
  chars; the parsing is unchanged, so every previously working key keeps
  working). Surfaced as a flaky-looking CI failure on the 1.0.0 merge and
  measured at 8.1% of 2,000 sampled keys.

## [1.0.0] - 2026-08-31

The first `surgite` release — and the first on PyPI. 1.0.0 carries the rename
from `standup-gen` (the PyPI name belongs to an unrelated project, so the
distribution, package, CLI, and configuration surface all move to `surgite`)
and the orgs data-model foundation: every user gains a personal org, and the
per-user tables are org-scoped underneath an unchanged API surface — every
endpoint resolves to the caller's personal org, and the 0.6.0 OpenAPI
snapshot holds. The orgs API and web UI land in the next cycle, on top of
this foundation.

### Added

- **Org data model.** New `orgs` and `org_members` tables, a
  `personal_org_id` on every user, and a nullable `org_id` on the nine per-user
  tables. Every existing user gets a personal org they own; all their data is
  backfilled to it. Existing endpoints are unchanged — they resolve to the
  caller's personal org. See
  [`docs/migrations/0.6.0-to-1.0.0.md`](docs/migrations/0.6.0-to-1.0.0.md).

### Changed

- **Renamed to `surgite`.** The Python package is `surgite/` (was `backend/`),
  the CLI command is `surgite` (was `standup`), and the project is published to
  PyPI as `surgite`. The canonical repository moves to GitHub
  (<https://github.com/nicoleman0/surgite>) with read-only pull mirrors on
  Forgejo and Codeberg. Environment variables rename `STANDUP_*` →
  `SURGITE_*`, the session cookie is `__Host-surgite_session`, the CSRF header
  value is `surgite-web` (SPA and API flip together — invisible to users), the
  keyring service is `surgite` with automatic forward-migration from the old
  `standup-gen` entry, the CLI config dir is `~/.config/surgite/` (the old
  `~/.config/standup/session` file migrates too), and infra defaults rename
  (Postgres db/user `surgite`, cache volume `surgite-repos`,
  `/var/surgite/repos`, image `surgite-app`, backup pattern
  `surgite-*.sql.gz`). One-time effects: browser sessions are invalidated
  (everyone logs in again once); CLI sessions migrate automatically or
  re-login. Upgrade steps for existing deployments:
  [`docs/self-host.md`](docs/self-host.md#upgrading-from-standup-gen-pre-rename).

### Removed

- The deprecated `STANDUP_API_TOKEN` env var (an alias for `STANDUP_API_KEY`
  since 0.5.0), per the [`docs/api-stability.md`](docs/api-stability.md)
  deprecation procedure — subsumed by the `SURGITE_*` rename above.

## [0.6.0] - 2026-06-30

The API-stability release. 0.6.0 makes the contract with downstream users
explicit: a snapshot-tested OpenAPI surface, a written stability policy, a
deprecation procedure, and a security-support commitment.

### Added

- A canonical, snapshot-tested OpenAPI document (`docs/openapi.json`),
  regenerated from the app and gated in CI
  (`.forgejo/workflows/openapi-snapshot.yml`). Every route now carries an
  explicit `operationId`, a summary, and a tag; the stable
  `{"detail": "..."}` error envelope is declared as the `ErrorResponse`
  schema.
- `docs/api-stability.md` — the public API stability policy: what we
  promise not to break in a minor, what we reserve the right to change,
  and how the deprecation cycle works.
- `docs/security-support.md` — supported versions, the
  vulnerability-response SLA, and the advisory process.
- **CLI keyring storage.** `standup --login` / `--redeem-invite` now store
  the session cookie in the OS keyring (macOS Keychain, Linux Secret
  Service, Windows Credential Manager) instead of a 0600 file. Falls back
  to the 0600 file when no keyring backend is available (headless / no
  D-Bus); `--keyring-file` forces the file. An existing 0600 session is
  migrated into the keyring on first read, then shredded. New dependency:
  `keyring`.
- **Self-serve, email-delivered password reset.** `POST /auth/password-reset`
  (public) emails a one-time, 15-minute reset link; it always returns 204,
  so it can't be used to enumerate accounts. A new `backend/mail.py`
  provides an SMTP mailer (`SMTP_*` env vars, STARTTLS / implicit TLS) and a
  logging mailer that writes the email to the log stream — the default when
  `SMTP_HOST` is unset, so reset works before mail is configured. The
  admin-mediated `POST /admin/users/{id}/reset-password` stays.

### Changed

- Password-reset token ids are now generated as hex (`token_hex`) rather
  than `token_urlsafe`. The previous ids could contain `_`/`-`, which made
  a fraction of tokens fail to redeem because the `pr_<id>_<secret>` format
  couldn't be split back apart reliably. Existing un-redeemed tokens from a
  pre-upgrade process are unaffected by a normal deploy (they're short-lived
  and re-issued on demand).

### Deprecated

> Routes and fields marked deprecated in a release are removed no earlier
> than the second minor release after it (something deprecated in 0.6.0
> goes no sooner than 0.8.0). See `docs/api-stability.md` for the full
> policy.

- The `STANDUP_API_TOKEN` environment variable is deprecated in favour of
  `STANDUP_API_KEY` (it has been an alias since 0.5.0, and the CLI already
  logs a one-time deprecation warning when it's used). Scheduled for
  removal in **0.8.0**. This is the first use of the new deprecation
  procedure.

## [0.5.0] - 2026-06-20

The auth + multi-user release. After 0.5.0 the project is safe to expose to
people who aren't the operator: first-party sessions, per-user data isolation,
admin-mediated invites and password reset, Fernet-encrypted per-user provider
keys, and a complete self-hosting guide. The data-isolation plumbing landed
in slice 1, the security controls in slice 2, and the user-visible UX in
slice 3. The remaining work for 1.0.0 is multi-tenant features (orgs,
billing, SSO) and the API stability promise — both well-scoped, neither
requiring an auth rewrite.

### Added

- **Auth foundation (0.5.0 slice 1).** An `AUTH_MODE` setting with three modes:
  `off` (default; anonymous, indistinguishable from 0.4.0), `single_user`, and
  `multi_user` (email + password with first-party session cookies).
- New `users`, `sessions`, and `invites` tables. `email` is `CITEXT` on
  Postgres for case-insensitive, index-supported lookups.
- A non-nullable `owner_id` on `repos`, `commits`, `prompt_settings`, and
  `shared_summaries`; every query is now scoped to the current user.
- `backend/auth.py`: argon2id password hashing, server-side sessions with
  sliding refresh, the `get_current_user` dependency, and a hardened
  `__Host-`-prefixed `Secure`/`HttpOnly`/`SameSite=Lax` session cookie.
- `/auth/login`, `/auth/logout`, `/auth/redeem-invite`, `/auth/me` (multi_user
  only). On first run with no admin, an admin invite is minted and logged for
  `BOOTSTRAP_OWNER_EMAIL`.
- CLI: `standup --login`, `standup --logout`, `standup --redeem-invite <token>`,
  storing a 0600 session at `$XDG_CONFIG_HOME/standup/session`.
- `scripts/upgrade-from-0.4.sh` and `docs/migrations/0.4.0-to-0.5.0.md` for the
  single-operator upgrade path.
- **Security slice (0.5.0 slice 2).**
  - Per-user API keys (`POST/GET/DELETE /auth/api-keys`) with argon2id
    hashes and Bearer-token auth on `get_current_user`.
  - CSRF defence in depth: `X-Requested-With: standup-web` required on
    non-safe requests in multi_user mode.
  - Per-user rate limit (5/60s) on `/summary?ai=true` and
    `/summary/stream`, with a 100/60s per-IP outer backstop.
  - Account lockout (10 fails / 15 min → 15 min lockout per user) with
    `POST /admin/users/{id}/unlock` for recovery.
  - Per-user provider keys (Fernet-encrypted at rest, master key from
    `SECRETS_ENCRYPTION_KEY` or auto-generated `.secrets_key`).
  - Admin-only `/admin/invites` (issue, redeem out of band) and
    `/admin/audit` (paginated audit log).
  - User-scoped `GET /summaries/{slug}` (404 not 403 cross-user).
  - `REPO_ADD_GLOBAL_ONLY=true` gates `POST /repos` to admins.
  - `docs/security.md` (threat model, rate-limit table, ops checklist).
  - `scripts/rotate-secrets.sh` (in-place re-encryption under a new
    master).
- **UX slice (0.5.0 slice 3).**
  - `/signup`, `/login`, `/password-reset` pages and `/admin/users` admin
    page (issues #75, #76, #79). Terminal-aesthetic SPA pages, the
    `X-Requested-With: standup-web` CSRF header on every non-safe call, and
    a client-side auth guard that redirects unauthenticated users to
    `/login`.
  - Per-user password change and admin-mediated reset (issue #77):
    `PUT /auth/password` (self-service), `POST /admin/users/{id}/reset-password`
    (admin mints a one-time token), and `POST /auth/password-reset/confirm`
    (user redeems it). All other sessions for the affected user are
    revoked on change/reset.
  - Admin user management (issue #76): list, search, unlock, and
    activate/deactivate users; issue invites and display the redeem URL
    out of band.
  - Per-user UX polish (issue #78): a logged-in indicator in the top
    bar with a logout button, and a `/summaries` page that lists the
    caller's saved shares.
  - CLI login/logout (issue #80): `standup --login`, `standup --logout`,
    `standup --redeem-invite` (CLI session cookie persisted at
    `$XDG_CONFIG_HOME/standup/session` with 0600 perms). Test coverage
    expanded.
  - `docs/self-host.md` (issue #81): end-to-end self-hosting guide —
    sizing, quickstart, TLS, backup, runbook.
  - README rewrite (issue #82): now leads with the self-host story.

### Changed

- `/health/deep` returns a redacted `{status, components}` shape and scopes its
  git probe to the caller's own repos (no cross-user repo enumeration).
- `/providers` is admin-only in `multi_user` mode.
- The CLI env var `STANDUP_API_TOKEN` is renamed to `STANDUP_API_KEY`; the old
  name still works as a deprecated alias with a one-time warning.

## [0.4.0] - 2026-06-13

Operational hardening plus a round of real features: a background ingest
scheduler, structured logging, rate limiting, backup/restore, streaming AI
summaries, shareable links, per-repo prompt overrides, a CLI↔API bridge, and
a deep health check.

### Added

- A background ingest scheduler (asyncio task started in the FastAPI lifespan)
  that runs `_ingest_all_repos` every `INGEST_INTERVAL` seconds (default 300,
  set to `0` to disable). The DB now stays fresh without anyone hitting
  `/summary`, and the endpoint is a pure read.
- Structured logging: `LOG_LEVEL` (default `INFO`) and `LOG_FORMAT=json`
  controls. In JSON mode, `LogRecord` extras are flattened to top-level keys
  for log-shipping pipelines.
- Per-IP rate limit on `/summary?ai=true` (hand-rolled token bucket; default
  5 requests / 60 s, configurable via `RATE_LIMIT_REQUESTS` and
  `RATE_LIMIT_WINDOW_SECONDS`). Trusts the first `X-Forwarded-For` entry.
- `scripts/backup.sh` and `scripts/restore.sh` for off-host `pg_dump` /
  `pg_restore`. Default mode runs `pg_dump` inside the `db` container via
  `docker compose exec`; `BACKUP_MODE=local` runs against a host-side
  Postgres. `backup.sh` keeps the most recent `BACKUP_KEEP` dumps
  (default 14) and prunes older ones.
- Streaming AI summaries over SSE (`GET /summary/stream`). The web UI fills
  each repo card in token-by-token as the provider streams, instead of
  blocking on a spinner for the whole fan-out; cancellation still works
  mid-stream.
- Shareable summary links. "share link" copies a `/s/{slug}` URL that
  re-runs the saved query in a read-only view; "copy markdown" puts the whole
  summary on the clipboard for pasting into Slack/Jira. Slugs live in a
  `shared_summaries` table and expire after `SHARE_TTL_DAYS` (default 7),
  swept by the scheduler and rejected on read once expired.
- Per-repo prompt settings. `prompt_settings` gains a `repo_id` so a repo can
  carry its own tone/identity/format, falling back to the global default;
  `GET`/`PUT /settings/prompt?repo_id=` and a scope selector in the config
  panel drive it.
- `standup --registered <name>` pulls a repo registered in a running API
  (`STANDUP_API_URL`, optional bearer `STANDUP_API_TOKEN`) instead of a local
  clone, so the CLI can reuse what the web app already ingested.
- `GET /health/deep` — DB connectivity, a `git ls-remote` probe against one
  registered repo, and provider reachability, returning 503 with the failing
  component. A meatier target for an uptime check than `/health`.

### Changed

- `/summary` no longer triggers a git fetch. Freshness is owned by the
  scheduler; the per-repo ingest on `POST /repos` still runs as a FastAPI
  `BackgroundTask` so newly added repos show up immediately.
- The `INGEST_TTL` / `_INGEST_CACHE` band-aid and the `repo=` filter on
  `_ingest_all_repos` are gone. A user opening the app now gets fresh data
  on the first click without paying the fetch cost, and a re-rendered
  summary no longer hits the network.
- The summarizer is now async: provider calls go through `httpx.AsyncClient`
  and the per-repo fan-out runs on `asyncio.gather` instead of a
  `ThreadPoolExecutor`, so a burst of concurrent `/summary?ai=true` requests
  no longer exhausts FastAPI's request threadpool. `requests` is dropped from
  the runtime dependencies in favour of `httpx`.
- `/summary` omits the full `commits` list by default (it was hundreds of KB
  the UI never rendered). Pass `commits=true` to include it, or use
  `/commits`.

### Fixed

- Dead link rot: `AGENTS.md` and `README.md` no longer reference
  `docs/production-readiness-plan.md` (deleted during 0.2.0 cleanup).
  The current roadmap is `docs/0.4.0-plan.md`, and
  `docs/performance-ui-audit.md` carries a closed-out banner.
- The `repo_id` backfill migration (`b2c3d4e5f6a7`) aborted with a
  `NotNullViolation` on databases holding commits orphaned by the pre-0.3.0
  delete bug (a repo's commits weren't removed with it). Those orphans are now
  dropped before the `NOT NULL` is enforced, unblocking the upgrade.

## [0.3.0] - 2026-06-11

UI polish, configurable AI prompts, and a sweep of performance and correctness
fixes ahead of 1.0.0.

### Added

- A summary stats block above the generated cards: total commits, repos touched,
  and active days, a per-repo commit-count bar chart, and a daily-activity
  sparkline (with quiet days filled in) over the selected period.
- User-configurable AI prompt settings — a dedicated `prompt_settings` table and
  UI panel for customising the system prompt and identity fields used during
  summarization.
- Loading skeletons for the repo list and prompt settings panel while data is
  being fetched.
- Markdown rendering enhancements in summaries: ordered lists, links, and
  fenced code blocks now render properly.
- A help overlay (F1) and a per-commit "view raw log" export option.
- A standalone performance & UI/UX audit document
  (`docs/performance-ui-audit.md`) tracking remaining work toward 1.0.0.

### Changed

- The `commits.date` column has been migrated from `String` to `Date` with
  supporting indexes, replacing string-comparison date filtering in queries.
- The theme system uses a single source of truth: the hardcoded theme list has
  been removed and the DOM `data-theme` attribute is now normalised after
  hydration to prevent flash-of-wrong-theme on first paint.
- The summary panel validates the custom date range and shows user feedback
  when `since` is after `until`.
- The version string surfaced in the status bar is now driven from a single
  source instead of being duplicated across components.

### Fixed

- Deleting a repo now removes all of its commits from the `commits` table
  (previously only the `RepoRow` was deleted, leaving orphaned commits).
- The `repos.name` column has a unique constraint, and `repos` → `commits` now
  cascade on delete to keep referential integrity intact.
- The author filter escapes SQL `LIKE` wildcards (`%`, `_`) in user input, and
  the repo filter now does an exact match instead of a partial `ILIKE`.
- Ingest runs as a background task, so a slow ingest no longer blocks the
  HTTP request that triggered it.
- The summary-generation request now respects `AbortController` cancellation
  when the user navigates away or hits a control mid-request.

### Performance

- Ingest skips repos that are already up to date and bulk-upserts commits in
  batches, cutting wall-clock time on large repos.
- Per-repo AI summaries are generated in parallel; a combined cross-repo
  summary is now opt-in rather than the default, removing the cost when it's
  not wanted.
- A single SQLAlchemy session is injected per request via FastAPI `Depends`,
  removing per-call session churn.

## [0.2.0] - 2026-06-08

UI/UX improvements, additional summary controls, and a terminal-inspired visual
rework on the way to a 1.0.0 release.

### Added

- Toast notifications for repo add / delete actions and clipboard errors,
  so successful actions now give clear feedback instead of failing silently.
- Empty-state hint and a "No commits in this period" state in the summary panel.
- Summary controls: a custom date range (in addition to the 7/14/30-day presets)
  and an optional author filter.
- Export a repo's summary to a file — Markdown for AI summaries, text for raw logs.
- An app icon / logo mark and a descriptive page title, replacing the default
  SvelteKit scaffolding favicon.

### Changed

- **Terminal aesthetic rework:** the web UI is now a pseudo-terminal — monospace
  (JetBrains Mono), terminal-window chrome, prompt-style section headers
  (`~/repos ❯`, `~/summary ❯`), command-style buttons (`❯ generate`,
  `❯ add-repo`), and a CLI command echo above each generated summary.
- Commits are now fetched automatically when a summary is generated; the separate
  manual ingest step (endpoint and button) has been removed.
- **Multi-theme colorschemes:** replaced the light/dark toggle with six
  colorschemes switched by a `data-theme` attribute: GitHub Dark (default),
  Light, Nord, Catppuccin Mocha, Solarized Dark, and a classic green/amber
  Terminal scheme. All components use semantic CSS tokens (`bg-surface`,
  `text-fg`, `border-border`, etc.) — no more `dark:` variants.
- **Personality:** log-line toasts (`[ ok ]` / `[fail]`), a vim/tmux-style
  status bar (`[standup] scheme: nord │ v0.2.0`), braille spinners (`⣾⣽⣻…`)
  for loading states, typewriter fade-in for AI summaries, a help overlay
  (F1), and a Konami-code CRT scanline easter egg. All animations respect
  `prefers-reduced-motion`.
- AI summaries are now grouped into thematic Markdown sections (`## Theme` + bullets)
  instead of one flat "Accomplishments" list.
- Accessibility and responsive polish: visible keyboard-focus rings on all controls,
  a focus action in place of the `autofocus` attribute, a labelled icon instead of
  the decorative globe emoji, and small-screen layout tweaks.

## [0.1.0] - 2026-06-07

The initial foundation — a working CLI, REST API, and self-hostable web UI, with CI,
tests, and contributor docs in place.

### Added

- CLI (`standup`) to generate a formatted git log or an AI-written prose summary.
- REST API (FastAPI + Postgres) for ingesting commits and serving filtered reads,
  per-repo summaries, and repo management (`/repos` CRUD + ingest).
- Model-agnostic AI summaries with Anthropic (default), Groq, and DeepSeek providers.
- SvelteKit web UI for managing repos and generating summaries, with a light/dark theme.
- One-command self-hosting via `docker compose up --build`.
- `GET /health` liveness + database-readiness endpoint, wired into the container healthcheck.
- CI: ruff lint/format, mypy, pip-audit, pytest, plus a frontend type-check and vitest suite.
- Contributor docs: CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, and issue/PR templates.

### Changed

- `docker-compose.yml` builds from source by default; a pre-built registry image is
  opt-in via `APP_IMAGE`.
- `.env.example` defaults provider keys to empty, so the app runs the non-AI paths
  without any key configured.
