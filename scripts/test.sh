#!/usr/bin/env bash
# Run the Agent Fabric test suite inside the compose network.
# Requires the stack to be up (docker compose up -d) and ANTHROPIC_API_KEY set for e2e.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose run --rm -v "$PWD/tests:/tests" --no-deps broker \
  bash -c "uv pip install -q --python /app/.venv/bin/python pytest pytest-asyncio && \
           cd /tests && /app/.venv/bin/python -m pytest -p no:cacheprovider \"\$@\"" -- "$@"
