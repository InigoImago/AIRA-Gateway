# syntax=docker/dockerfile:1.7
#
# Management SPA — build from the repository root, like the other two images:
#   docker build -f management/frontend/Dockerfile -t aira-frontend .
#
# The context is the repo root and not this directory, which is a correction rather than a
# preference. **Docker reads `.dockerignore` only from the context root**, so with this directory
# as the context the repository's own ignore file did not apply — and `COPY . .` then copied a
# developer's 287 MB `node_modules` into the builder *on top of* the tree `npm ci` had just
# installed. Proved with a marker file: the host's copy wins. The build therefore ran against
# whatever happened to be on that machine's disk — a different platform's native binaries for
# esbuild and rollup, a different Node version, or simply something stale — which is a generator
# of "it builds for me" and nothing else. One context root for all three images means one ignore
# file and one rule.
#
# Two stages: the Angular bundle is compiled with Node and then served by nginx, which also
# takes over the /api and /gw proxying that the dev server does in development.
#
# NOTE: the OIDC issuer and client id are compiled into the bundle
# (src/app/core/auth/auth.config.ts). Targeting a different Keycloak means editing that file and
# rebuilding this image — see docs/DEPLOYMENT.md §3.7.

# ---- build ---------------------------------------------------------------------------------
FROM node:26-alpine AS builder

WORKDIR /src

# Dependencies first, so a source change does not re-download the world.
COPY management/frontend/package.json management/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# Only this package's own sources. Named explicitly rather than `COPY . .`: with the repo root as
# the context, the latter would drag the whole repository into the image for no reason.
COPY management/frontend/ ./
RUN npm run build

# ---- runtime -------------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

ENV AIRA_MANAGEMENT_UPSTREAM=http://management:8002 \
    AIRA_GATEWAY_UPSTREAM=http://gateway:8001

COPY --from=builder /src/dist/frontend/browser /usr/share/nginx/html
COPY management/frontend/deploy/default.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -qO- http://127.0.0.1:8080/index.html > /dev/null || exit 1
