#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
exec docker compose exec --user surgite app .venv/bin/surgite --bootstrap-admin "$@"
