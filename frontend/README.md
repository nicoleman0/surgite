# surgite frontend

SvelteKit SPA (Svelte 5 + Tailwind v4) for the surgite web app: register repos,
trigger ingests, and generate standup summaries from the browser. Built to static
assets via `@sveltejs/adapter-static` and served same-origin by the FastAPI backend
in production.

For the full project (backend, CLI, Docker quickstart), see the
[root README](../README.md).

## Development

```bash
npm install
npm run dev      # Vite dev server on :5173, calls the API on :8000 cross-origin
```

Run the backend (`uv run uvicorn surgite.api:app --reload`) alongside it. To point at
a non-default backend, set `VITE_API_BASE` — see [.env.example](.env.example).

## Build & check

```bash
npm run build    # production build → ./build (served by the backend)
npm run preview  # preview the production build locally
npm run check    # svelte-check (type + a11y diagnostics)
```

## Invite signup browser test

`npm run test:e2e:auth` tests the built SPA against FastAPI and PostgreSQL over
HTTPS, with `DEBUG=false`. It creates invites in the admin UI, opens the displayed
links in separate browser sessions, signs up, reloads, logs out, and logs back in.
It also checks that used invites and non-admin access to admin actions are rejected.
The regular `test:e2e` suite uses mocked API responses for layout checks.

Prerequisites: `uv sync` at the repository root, frontend dependencies,
`npx playwright install chromium`, OpenSSL, and a test PostgreSQL server whose role
has `CREATEDB`. For example, start a disposable database:

```bash
docker run --rm -d --name surgite-auth-e2e -p 127.0.0.1:55439:5432 \
  -e POSTGRES_USER=surgite -e POSTGRES_PASSWORD=auth-e2e-only \
  -e POSTGRES_DB=surgite_auth_e2e postgres:16
docker exec surgite-auth-e2e pg_isready -U surgite -d surgite_auth_e2e
```

Once PostgreSQL is ready, run from `frontend`:

```bash
npm run build
E2E_DATABASE_URL=postgresql://surgite:auth-e2e-only@127.0.0.1:55439/surgite_auth_e2e \
  npm run test:e2e:auth
docker stop surgite-auth-e2e
```

The test server creates a uniquely named database, runs all migrations, bootstraps
a test admin, and removes that database on exit. It ignores `.env` and uses a
temporary self-signed certificate on port 8443; certificate verification is disabled
only in the test browser. CI runs this test in the Frontend build job.
