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
#   - the outbox relay        (`python manage.py relay --loop`, or once per run of a scheduler)

# ---- build ---------------------------------------------------------------------------------
FROM python:3.14-slim-bookworm AS builder

# **The installer is pinned to one release and to its digest.** It reads the lock and writes every
# package into the image, so a tag that can be moved is a way to change what ships — and a moving
# tag builds with whichever installer a machine happened to pull last. Raised by hand, in both
# Python images at once (`tools/tests/test_every_image_a_build_starts_from_is_pinned.py`).
COPY --from=ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

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
    uv sync --frozen --no-dev --no-install-workspace --package aira-management

COPY libs/ libs/
COPY management/backend/ management/backend/

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
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

# `AIRA_HEALTH_PORT`: where the healthcheck asks a process that keeps no heartbeat — the API's own
# `/healthz`.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=aira_management.config.settings \
    AIRA_HEALTH_PORT=8002

USER aira
EXPOSE 8002

# **One check for every process this image runs**, as in the gateway's: the relay's heartbeat when
# it keeps one, the API's `/healthz` otherwise.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD ["python", "-m", "aira_common.healthcheck"]

CMD ["uvicorn", "aira_management.config.asgi:application", "--host", "0.0.0.0", "--port", "8002"]
