# syntax=docker/dockerfile:1.7
#
# Management SPA — build from this directory:
#   docker build -t aira-frontend management/frontend
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
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

# ---- runtime -------------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

ENV AIRA_MANAGEMENT_UPSTREAM=http://management:8002 \
    AIRA_GATEWAY_UPSTREAM=http://gateway:8001

COPY --from=builder /src/dist/frontend/browser /usr/share/nginx/html
COPY deploy/default.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -qO- http://127.0.0.1:8080/index.html > /dev/null || exit 1
