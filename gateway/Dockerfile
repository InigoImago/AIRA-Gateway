# syntax=docker/dockerfile:1.7
#
# Gateway API (data plane) — build from the repository root:
#   docker build -f gateway/Dockerfile -t aira-gateway .
#
# The build context is the repo root because the gateway is a member of a uv workspace and
# shares `aira_common` with the management backend; the lockfile lives at the root and covers
# all members.
#
# The same image runs both gateway processes — the HTTP service (default command) and the Kafka
# config consumer (`python -m aira_gateway.consumer.worker`) — because they share a codebase and
# configuration. Migrations run from here too (`alembic upgrade head`).

# ---- build ---------------------------------------------------------------------------------
FROM python:3.14-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency metadata first: this layer only rebuilds when a dependency actually changes, not on
# every source edit.
COPY pyproject.toml uv.lock ./
COPY libs/pyproject.toml libs/
COPY gateway/pyproject.toml gateway/
COPY management/backend/pyproject.toml management/backend/
# `sharing=locked`: **a cache mount is shared by default**, and compose builds this image once per
# service that names it — four, since every service running a second process out of it now declares
# its build. Four `uv sync` processes writing one cache directory at the same time race, and the
# failure is a bare "process did not complete successfully" on a machine whose cache is cold enough
# for there to be real writing to race over. Locked serialises them; the wait is a few seconds and
# only on a cold cache.
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-install-workspace --package aira-gateway

COPY libs/ libs/
COPY gateway/ gateway/

# --no-editable installs the workspace packages into the venv itself, so the runtime image does
# not need the source tree on disk.
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable --package aira-gateway

# ---- runtime -------------------------------------------------------------------------------
FROM python:3.14-slim-bookworm AS runtime

RUN groupadd --system --gid 10001 aira \
    && useradd --system --uid 10001 --gid aira --home /app --no-create-home aira

WORKDIR /app

COPY --from=builder --chown=aira:aira /app/.venv /app/.venv
# Alembic's migration scripts are data, not part of the installed package.
COPY --from=builder --chown=aira:aira /app/gateway/alembic.ini /app/gateway/alembic.ini
COPY --from=builder --chown=aira:aira /app/gateway/migrations /app/gateway/migrations

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER aira
EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8001/healthz').read()"]

CMD ["uvicorn", "aira_gateway.main:app", "--host", "0.0.0.0", "--port", "8001"]
