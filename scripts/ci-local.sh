#!/usr/bin/env bash
# Shared by GitHub Actions and the local pre-push hook.
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_PYTHON=3.13
uv sync --locked --group dev
uv run --no-sync python -c 'import sys; assert sys.version_info[:2] == (3, 13)'
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest -q
bash .githooks/test-pre-push.sh
bash .githooks/test-env-isolation.sh
