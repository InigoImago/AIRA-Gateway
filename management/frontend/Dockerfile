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
# NOTE: the OIDC issuer and client id are **deployment** configuration, not build configuration:
# `public/runtime-config.js` ships beside the bundle and is loaded before the app, so a deployment
# replaces that one file (a volume mount, a ConfigMap, a `sed` in an entrypoint) and never rebuilds
# this image. The note here used to say the opposite, describing the state before 2026-08-08 — and
# a build instruction that names a file nobody edits any more sends the next person to rebuild an
# image for a change that needs no build. Set `AIRA_CSP_CONNECT_SRC` to the same issuer origin, or
# the content policy refuses the token request the console makes to it.

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

# `AIRA_CSP_CONNECT_SRC` is deliberately **not** given a value here. The entrypoint derives it
# from `AIRA_OIDC_ISSUER`, and a baked default would win over that derivation silently: a probe
# with the issuer moved to `:8090` still served `connect-src 'self' http://localhost:8080`, because
# `${VAR:-default}` never fires for a variable the image has already set. At the default port the
# wrong value happens to be the right one, which is how this would have shipped.
ENV AIRA_MANAGEMENT_UPSTREAM=http://management:8002 \
    AIRA_GATEWAY_UPSTREAM=http://gateway:8001 \
    AIRA_DNS_RESOLVER=127.0.0.11

COPY --from=builder /src/dist/frontend/browser /usr/share/nginx/html
COPY management/frontend/deploy/default.conf.template /etc/nginx/templates/default.conf.template
# Named `.inc`, not `.conf`, and that is load-bearing. The official image renders every template
# in `templates/` into `/etc/nginx/conf.d/`, and nginx's own configuration ends with
# `include /etc/nginx/conf.d/*.conf` at the **http** level — so a `.conf` here would be loaded
# twice: once http-wide, where it would attach a content policy to the JSON API responses too,
# and once by the `include` in each HTML location. `.inc` is rendered and not auto-loaded.
COPY management/frontend/deploy/aira-headers.inc.template /etc/nginx/templates/aira-headers.inc.template

# Sourced before the templates above are rendered, so the CSP it derives is the one they see.
# `.envsh` because the entrypoint *sources* those and *executes* `.sh` in a subshell, where an
# export dies; `10-` puts it ahead of the image's own `20-envsubst-on-templates.sh`.
COPY management/frontend/deploy/10-runtime-config.envsh /docker-entrypoint.d/10-aira-runtime-config.envsh

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -qO- http://127.0.0.1:8080/index.html > /dev/null || exit 1
