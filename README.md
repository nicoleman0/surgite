# surgite

Standup summaries from Git history, in a browser or terminal.

[![CI](https://github.com/nicoleman0/surgite/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nicoleman0/surgite/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/surgite)](https://pypi.org/project/surgite/)
[![Python versions](https://img.shields.io/pypi/pyversions/surgite)](https://pypi.org/project/surgite/)
[![Container image](https://img.shields.io/badge/ghcr.io-surgite-blue?logo=docker&logoColor=white)](https://github.com/nicoleman0/surgite/pkgs/container/surgite)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

![surgite dashboard](https://raw.githubusercontent.com/nicoleman0/surgite/main/docs/demo.gif)

## Quickstart

**Local CLI** — no server or database:

```bash
pip install surgite
surgite /path/to/repo --since 7.days.ago
```

Add `--summarize` for an AI summary after configuring a provider key.

**Web app** — clone the repository and start Compose:

```bash
git clone https://github.com/nicoleman0/surgite.git
cd surgite
cp .env.example .env
# For multi-user access, set AUTH_MODE=multi_user and BOOTSTRAP_OWNER_EMAIL.
docker compose up -d
```

In multi-user mode, first start logs an owner invite. Redeem it in the browser
or with `surgite --redeem-invite <token> --email you@example.com`. The full
deployment guide is [docs/self-host.md](docs/self-host.md).

## Features

- Per-user repositories, prompt settings, summaries, and shared links
- Local CLI or a self-hosted FastAPI/Postgres/SvelteKit application
- Anthropic, Groq, DeepSeek, or OpenAI-compatible local models
- Email/password auth, API keys, encrypted per-user provider keys, Git connections, and audit logs

## Private repositories

Add an HTTPS Git connection in **Settings** with a credential-free origin, username, and
read-only access token. Surgite encrypts it at rest and only supplies it to Git through a
non-interactive askpass helper. Never paste credentials into a repository URL.

## Documentation

- [Self-hosting](docs/self-host.md)
- [Security model](docs/security.md) and [security policy](SECURITY.md)
- [API stability policy](docs/api-stability.md) and [OpenAPI contract](docs/openapi.json)
- [Contributing](CONTRIBUTING.md), [release history](CHANGELOG.md), and [upgrade guides](docs/migrations)

## Development

```bash
uv sync --group dev
docker compose up -d db
uv run alembic upgrade head
uv run pytest
cd frontend && npm install && npm run check && npm run test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow and
[AGENTS.md](AGENTS.md) for architecture and project conventions.

## License

[MIT](LICENSE) © 2026 Nick Coleman
