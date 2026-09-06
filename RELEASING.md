# Releasing

How a version of surgite gets from the repository to PyPI and to the GitHub
Container Registry. Read it top to bottom the first time; after that the
checklist is the whole of it.

Commands use the `uv` forms; `python -m build` / `python -m twine check`
work the same in a plain pip virtualenv.

## Checklist

`main` is protected — pull requests are required, and the rule is enforced
for admins — so the version bump and the changelog move land as a branch and
a PR like any other work, and the tag is cut from `main` after that merges.
A direct push is rejected with `GH013`, after the commit already exists
locally; move it to a branch rather than trying to force it through.

1. `uv run pytest -q` — all green.
2. `uv run ruff check . && uv run ruff format --check . && uv run mypy surgite/`
   — clean.
3. `cd frontend && npm run check && npm run test && npm run build` — clean.
4. `task openapi-snapshot-check` — the OpenAPI baseline matches the code
   (regenerate with `task openapi-snapshot` if routes changed, and commit
   the result).
5. Bump `version` in `pyproject.toml`, then `uv lock` — the lockfile records
   the project version too, and `uv lock --check` must pass.
6. Bump `version` in `frontend/package.json` to match. `vite.config.ts` reads
   it at build time into `__APP_VERSION__`, which `StatusBar.svelte` renders,
   so skipping this ships a web UI that reports the previous version. Nothing
   fails if you forget — no gate checks the two against each other. Leave
   `frontend/package-lock.json` alone: its `version` field has read `0.2.0`
   since before the rename, `npm ci` does not check it, and every release so
   far has left it as-is.
7. Move the `[Unreleased]` entries in `CHANGELOG.md` under the new version,
   with a date.
8. For a minor or major bump, roll the supported-versions table in
   [`docs/security-support.md`](docs/security-support.md) forward: the new
   version becomes the latest minor, the one it replaces moves into the
   6-month window, and the one below that goes end-of-life.
9. If the release changes the install story (the first PyPI release, a
   rename, new badges): refresh the README — the `pip install surgite` line
   and the PyPI badges.
10. Open the PR, let CI go green, merge. Then tag from `main`:
   `git tag -a vX.Y.Z -m 'vX.Y.Z'`, and push the tag.
11. Cut a GitHub release from the tag (notes: a summary plus a CHANGELOG
    link). Publishing is automatic from there — see below.

## Version numbering

Semantic versioning. The HTTP API surface follows the stability contract in
[`docs/api-stability.md`](docs/api-stability.md): additive changes ride a
minor; anything the policy covers as breaking is a major. Configuration
(env vars, cookie name, CSRF header value) is covered by that policy's
deprecation procedure from 1.0.0 onward — 1.0.0 itself is the clean break
that renamed `STANDUP_*` → `SURGITE_*` and friends, so the policy clock
starts there.

## Publishing to PyPI

Set up once, at
[pypi.org/manage/account/publishing](https://pypi.org/manage/account/publishing/):
the publisher is project `surgite`, owner `nicoleman0`, repository `surgite`,
workflow `publish.yml`, environment `pypi`. A project that does not yet
exist on PyPI is registered as a *pending* publisher, and the first
successful upload creates it — that is how the `surgite` project itself gets
created at v1.0.0.

This is trusted publishing: PyPI accepts a short-lived OIDC token minted by
the workflow, so there is no API token to store in repository secrets or to
leak.

Then publishing is: push the tag, cut a GitHub release from it, and
`.github/workflows/publish.yml` does the rest. The workflow checks out the
release's tag, builds with `python -m build` (the `uv_build` backend is a
normal PyPI package, so any PEP 517 frontend works), checks the artifacts
with `twine`, and uploads them under the `pypi` environment.

The wheel ships the `surgite` package only — the CLI and the API code,
including the email templates. The web frontend and the alembic migrations
are not in it; the full self-hosted stack installs from the repo or the
Docker image (see [`docs/self-host.md`](docs/self-host.md)).

### Before you tag

```bash
uv build                                    # or `python -m build`
uvx twine check dist/*                      # or `python -m twine check dist/*`
uv run --no-project --with ./dist/surgite-*.whl surgite --help
```

Confirm the installed console script works, not just the checkout — the
entry point is the part that silently breaks. `uv run --with` builds a
throwaway environment for the check (and resolves the real dependency
metadata), which is why it is preferred over `pip install --force-reinstall`:
the latter leaves the release wheel installed in the working virtualenv,
where it shadows the checkout on the next run.

### After the release

The publish workflow finishing is not the same as the release being
installable. PyPI's simple index updates first; the JSON API and resolvers
such as `uv` and `pip` can lag it by a few minutes, so a `No solution found`
for the version you just published means the CDN has not caught up, not that
the upload failed. Confirm with the index, then install:

```bash
curl -s https://pypi.org/simple/surgite/ | grep surgite-X.Y.Z
uv run --no-project --with surgite==X.Y.Z surgite --help
```

**A version number on PyPI is permanent.** It cannot be re-uploaded or
overwritten, only yanked and superseded. Get the tag right before you cut
the release.

## Publishing the container image

The same release event runs the `image` job in the same workflow, which builds
`Dockerfile` and pushes `ghcr.io/nicoleman0/surgite` tagged `X.Y.Z`, `X.Y` and
`latest` (amd64 only). The credential is the workflow's own `GITHUB_TOKEN`, so
unlike PyPI there is nothing to configure — no secret, no environment, no
trusted-publisher setup.

**One-time, after the first release that runs this job:** the package is
created private. Open it from the repo's *Packages* sidebar → *Package
settings* → *Change visibility* → **Public**, or nobody else can pull it.

A container tag, unlike a PyPI version, can be overwritten: re-running the
release workflow republishes it. That makes a bad image far less costly than a
bad wheel — but it also means `latest` and the minor tag move under anyone
following them, which is why `docs/self-host.md` offers the exact version to
pin.
