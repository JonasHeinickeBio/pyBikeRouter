# syntax=docker/dockerfile:1

###############################################################################
# Stage 1: build dependencies with Poetry into an isolated virtualenv
###############################################################################
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.3.2 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /build

RUN pip install "poetry==${POETRY_VERSION}"

COPY pyproject.toml poetry.lock README.md ./
COPY src ./src

# Runtime dependencies only; the project itself is installed as editable-free wheel.
RUN poetry install --only main --no-root --no-interaction --no-ansi \
    && poetry build --format wheel \
    && pip install --no-cache-dir dist/*.whl

###############################################################################
# Stage 2: minimal runtime image
###############################################################################
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    EXPORT_DIR=/app/exports

# curl is only used by the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --no-create-home appuser

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app
RUN mkdir -p /app/exports && chown -R appuser:appuser /app
VOLUME ["/app/exports"]

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${APP_PORT}/healthz" || exit 1

# Exec-form entrypoint with env-expanded args so uvicorn runs as PID 1
# and receives SIGTERM for graceful shutdown.
ENTRYPOINT ["sh", "-c", "exec uvicorn bike_routing_agent.api:app --host \"$APP_HOST\" --port \"$APP_PORT\" ${UVICORN_EXTRA_ARGS:-}"]
