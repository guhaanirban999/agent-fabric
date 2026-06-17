# Single fat image for the whole uv workspace.
# Every service/fixture runs from this image; docker-compose overrides `command`
# to select the entrypoint. Simple and cache-friendly for a single-host pilot.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Copy the full workspace. Members are small; one venv holds all deps.
COPY pyproject.toml ./
COPY libs/ ./libs/
COPY services/ ./services/
COPY fixtures/ ./fixtures/

# Resolve + install EVERY workspace member into /app/.venv (--all-packages),
# so registry/gateways/broker/fixtures are all importable from one image.
RUN uv sync --all-packages --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Default command is overridden per-service in docker-compose.yml
CMD ["python", "-c", "print('agent-fabric base image; set a command')"]
