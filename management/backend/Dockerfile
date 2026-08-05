# syntax=docker/dockerfile:1.7
#
# Management API (control plane) — build from the repository root:
#   docker build -f management/backend/Dockerfile -t aira-management .
#
# The build context is the repo root: the backend is a uv workspace member and shares
# `aira_common` with the gateway.
#
# One image serves three roles, all sharing this codebase and configuration:
#   - the HTTP API            (default command, uvicorn on the ASGI app)
#   - schema migrations       (`python manage.py migrate`)
#   - the outbox relay        (`python manage.py relay`, run periodically)

# ---- build ---------------------------------------------------------------------------------
FROM python:3.14-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY libs/pyproject.toml libs/
COPY gateway/pyproject.toml gateway/
COPY management/backend/pyproject.toml management/backend/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package aira-management

COPY libs/ libs/
COPY management/backend/ management/backend/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package aira-management

# ---- runtime -------------------------------------------------------------------------------
FROM python:3.14-slim-bookworm AS runtime

RUN groupadd --system --gid 10001 aira \
    && useradd --system --uid 10001 --gid aira --home /app --no-create-home aira

WORKDIR /app

COPY --from=builder --chown=aira:aira /app/.venv /app/.venv
# `manage.py` is the entry point for migrations, the relay and the seed command; it is a script,
# not part of the installed package.
COPY --from=builder --chown=aira:aira /app/management/backend/manage.py /app/manage.py

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=aira_management.config.settings

USER aira
EXPOSE 8002

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8002/healthz').read()"]

CMD ["uvicorn", "aira_management.config.asgi:application", "--host", "0.0.0.0", "--port", "8002"]
