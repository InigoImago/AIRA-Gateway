# ADR-0031 — Running a published release is two overlays, not a second stack

- **Status:** Accepted
- **Date:** 2026-10-11
- **Deciders:** AIRA
- **Extends:** `ADR-0029` and `ADR-0030`, which decided how a version is *published*. This one is
  about running what they published.

## Context

`FRD-137` put three images in GitHub Container Registry at `vX.Y.Z`, and `v0.1.0` is there. Nothing
could run them. Every `image:` in `docker-compose.apps.yml` is paired with a `build:` — deliberately,
because the unqualified name `aira-gateway` is on no registry and a service with an image and no
build is one Compose tries to pull, which fails on any machine that has never built it
(`test_compose_images_are_buildable.py` exists for that). So the registry had images and the
repository had no way to ask for them:

- every deployment still **built from a checkout**, which means a toolchain, a build, and whatever
  that machine had on disk;
- *"which AIRA is running?"* still had no answer, which is the question `FRD-137` was built for;
- and the **integrated** shape — somebody else's Postgres, Keycloak, Kafka and Vault — had a
  document (`docs/deployment/integrated.md`) and no artefact. The page said *"AIRA supplies two
  application images"* and the files supplied builds.

That last gap is the sharper one. `docker-compose.apps.yml` defaults every address to the container
name of the stack it ships beside: `postgres`, `kafka:9092`,
`keycloak:8080/realms/aira`. Those are right in the bundled stack and wrong in a deployment, and
wrong in the way that costs an afternoon — `up` reports success, and the failure is a connection
error several screens into a container log, minutes later.

## Options considered

- **Put the registry in `docker-compose.apps.yml`, behind variables.** One file, no overlay:
  `image: ${AIRA_IMAGE_REGISTRY:-}aira-gateway:${AIRA_IMAGE_TAG:-dev}`. It does not work, and the
  reason is instructive: the `build:` has to stay for the ordinary path, and a `build:` that stays
  **is a build**. `docker compose up` builds an image that is not local instead of pulling it, and
  `--build` rebuilds it even when a pull succeeded. The deployment would run this checkout's code
  under a version number it took from the registry — the one confusion a version number exists to
  prevent, arrived at silently.
- **A second, standalone Compose file for deployments.** Honest about its purpose, and a copy of
  the four hundred-odd lines of settings in `docker-compose.apps.yml`, with their defaults. Two
  places for every value is this repository's oldest defect shape; the one that produced
  `tools/compose_files.py`, `tools/stack_addresses.py` and `ADR-0029`'s refusal to write a version
  into three files. The copy would drift on the first
  setting added to one and not the other, and drift silently.
- **Kubernetes manifests or a Helm chart.** What a real deployment of this eventually wants, and
  not what is being asked for here. Compose is what exists, it is what the documentation describes,
  and a chart is a larger piece of work that this decision does not block.
- **Two overlays, each about one thing.** Chosen.

## Decision

**A published release is run by layering overlays onto `docker-compose.apps.yml`, one per
question.** Neither repeats a setting; each changes the few properties it is about.

`docker-compose.release.yml` — **where the three images come from.** It re-points them at
`${AIRA_IMAGE_REGISTRY:-ghcr.io/inigoimago}/aira-<name>:${AIRA_VERSION}` and **removes** their
`build:` with `!reset`, which is the difference between an overlay and a wish: removing the key is
what makes building impossible rather than merely unasked-for.

`docker-compose.external.yml` — **where the infrastructure is.** It starts none of it, turns every
address into a required variable, and removes the dependencies on containers that no longer exist.

Four files, four compositions, and each is a deployment somebody wants:

| Composition | Is |
| --- | --- |
| `yml` + `apps` | build from this checkout — unchanged, still the default |
| `yml` + `apps` + `release` | the published release on one machine, nothing to build |
| `apps` + `external` + `release` | the published release on your infrastructure |
| `yml` + `apps` + `showcase` + `release` | the demo, on published images |

The details that are decisions rather than mechanics:

- **The version is required and `latest` is not the default.** `${AIRA_VERSION:?…}` refuses the
  composition with a message naming the variable. A default would be the wrong kindness: `latest`
  moves under a restart, so a stack that was pinned yesterday is not pinned today, and nothing says
  so. `latest` stays in the registry for a reader trying AIRA out — *typing* it is then a
  deliberate act.
- **The overlay and the `--build` flag are one switch.** `RELEASE=vX.Y.Z` on any `up*` target adds
  the file *and* drops the `--build` that `up-apps` and `showcase` pass by default. Either half
  alone is a contradiction, so neither is available alone.
- **A missing address is a refusal, not a default.** `${VAR:?message}` for the four that cannot be
  guessed — the database host, the broker list, the issuer, the JWKS URL. Compose reports them one
  at a time rather than all at once, which is weaker than this project's own rule for startup
  checks and is as far as interpolation goes.
- **Two of them may be answered with nothing**, and are therefore `${VAR?…}` without the colon:
  `AIRA_REDIS_URL` (empty means per-process rate limits and the older budget path — a state
  `/readyz` reports as `degraded: true`, which must be *chosen* because N replicas then allow N
  times each limit) and `VAULT_ADDR` (empty means every credential comes from the environment
  instead, which the loader does silently). Required-and-non-empty would make a lie the only way to
  start.
- **Creating the Kafka topics becomes opt-in** behind a profile. Against a broker reached over SASL
  or TLS the one-shot cannot work — it would need a client properties file it has no way to
  assemble — and two services wait on `service_completed_successfully` and would never start. Most
  platform teams would rather create eight topics themselves anyway.
- **The `aira` network is defined a second time, identically, and a test holds the two together.**
  `docker-compose.apps.yml` names that network for every service and defines nothing; the
  infrastructure file owns it, and the integrated composition does not include that file. A second
  *expression* for the name would give the applications a bridge of their own, on which every
  address they were handed resolves somewhere else or nowhere.

## Consequences

- Positive: a deployment can run a version it did not build, which is what a registry is for, and
  `FRD-137` §6's *"nothing consumes the images yet"* closes.
- Positive: the integrated shape is an artefact rather than a page of instructions, and the
  addresses it cannot guess are refusals instead of defaults that resolve to nothing.
- Positive: the demo runs on published images too (`make showcase RELEASE=vX.Y.Z`), so *"try AIRA
  without a toolchain"* is one command and the published images are exercised by the project's own
  demo rather than only by a deployment.
- Negative: **the overlay repeats a list — which services exist.** Add a ninth application service
  and the release overlay does not know about it; the stack then runs eight published images and
  one built here, which no reader would notice. Guarded by a test that derives the list from
  `docker-compose.apps.yml` rather than by review.
- Negative: `extends:` **does not see an overlay.** The demo's seed extends `management-migrate`
  out of `docker-compose.apps.yml` *on disk*, not out of the merged `-f` chain, so it needs its own
  entry in the release overlay — confined to the demo profile, or naming it would declare a service
  in every composition that does not layer the showcase on. A trap, written down because the next
  `extends:` will hit it again.
- Negative: the images are **`linux/amd64` only** (`FRD-137`'s non-goal), so this path does not work
  on an arm64 host without emulation. Nothing in the overlay can say so; the registry rejects the
  pull and a reader has to know why.
- Negative: a **private** package needs `docker login ghcr.io` before any of this, and the error
  when it is missing is a 401 that reads like a typo in the image name. The packages are public
  today; the overlay cannot tell the difference.
- Follow-ups: still no Kubernetes manifests; the published bytes are still a rebuild of the tested
  commit rather than the tested bytes (`ADR-0029`); and nothing verifies an image's digest against
  the release it claims to be.
