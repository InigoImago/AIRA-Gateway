# FRD-138 — Running a published release

> Phase: 7 · Status: **Built** · Owner: AIRA
> Related: `ADR-0031` (the decision), `FRD-137` (publishing the images),
> `ADR-0029`/`ADR-0030` (what a version is), `FRD-127` (several gateway instances),
> `docs/deployment/integrated.md` §5a, `docs/DEPLOYMENT.md` §7

## 1. Summary

`FRD-137` publishes three images to GHCR at `vX.Y.Z` on every release. **Nothing could run them.**
Every `image:` in `docker-compose.apps.yml` is paired with a `build:`, so every way of starting
AIRA in containers built from the checkout it was started in, and *"which AIRA is running?"* — the
question a version number exists to answer — still had none.

Two overlays close that, and neither repeats a setting (`ADR-0031`):

- **`docker-compose.release.yml`** re-points the three images at the registry and **removes** their
  `build:`. `RELEASE=vX.Y.Z` on any `up*` target is the whole interface.
- **`docker-compose.external.yml`** starts no infrastructure at all, and turns every address the
  deployment must supply into a **required** variable rather than a default naming a container that
  is not running.

Together they are the shape the integrated deployment was always described as having and never had:
*you* supply the database, the realm, the broker and the Vault; AIRA supplies three images at a
version you can name.

## 2. Goals & Non-Goals

**Goals**

- Run a published version without a toolchain, a build, or a source checkout beyond the Compose
  files.
- Make it **impossible** for a release composition to build instead of pull. Not unlikely: the
  failure is silent, and the stack that comes up healthy is the wrong code under the right number.
- Make every address an integrated deployment must supply a refusal when it is missing, before a
  container starts.
- One place per fact. The overlays change images and dependencies; `docker-compose.apps.yml` stays
  the only description of what the product is configured with.
- The same four deployment shapes as before, each gaining a published-image variant.

**Non-Goals**

- A second, standalone Compose file for deployments. It would be a copy of the four hundred-odd
  lines of settings in `docker-compose.apps.yml`, with their defaults — the defect shape `ADR-0029`
  and `tools/compose_files.py` both exist to stop (`ADR-0031`).
- Kubernetes manifests or a Helm chart. Still absent, still named in `docs/DEPLOYMENT.md` §7, and
  not blocked by anything decided here.
- Running the **infrastructure** from pinned versions as part of this. Postgres, Keycloak, Kafka,
  Redis and Vault are already pinned to minor versions in `docker-compose.yml`; a deployment that
  supplies its own is out of this project's hands by definition.
- Verifying an image's digest against the release it claims to be. The tag is trusted here; a
  signature or an attestation is `FRD-137` §6's open item, not this one's.
- Multi-architecture images. `linux/amd64` only (`FRD-137`), so an arm64 host needs emulation —
  and the overlay cannot say so, because the registry is what refuses.

## 3. User Stories

- As whoever **operates** an installation, I want to run `aira-gateway:v0.1.1` on my own Postgres,
  Keycloak and Kafka, so that a restart cannot quietly bring up different code and an upgrade is a
  version number rather than a build.
- As whoever is **evaluating** AIRA, I want the demo to run from published images, so that seeing
  it work does not require Python, Node and a twelve-minute build.
- As whoever performs a **rolling update** (`FRD-127`), I want the deployment to name an immutable
  reference, so that "one instance at a time" names a specific artefact.
- As whoever **integrates** AIRA, I want to be told which address I have not supplied, at the
  moment I start it, rather than finding a connection error in a container log twenty minutes later.

## 4. Functional Requirements

- **FR-1** A release composition takes all three application images from
  `${AIRA_IMAGE_REGISTRY:-ghcr.io/inigoimago}/aira-<name>:${AIRA_VERSION}`, and **every** service
  that builds one of them is re-pointed — the four gateway processes, the three management
  processes, the console, and the demo's seed. One service left behind is a stack where the odd one
  out is a migration or a consumer, sharing a schema with eight containers of a different version.
- **FR-2** Their `build:` is **removed**, not overridden (`!reset`). A build that remains is a
  build: `docker compose up` builds an image that is not local rather than pulling it, and
  `--build` rebuilds it even when a pull succeeded.
- **FR-3** `AIRA_VERSION` has **no default**. The composition is refused with a message naming the
  variable, because `:-latest` moves under a restart and `:-dev` is silently a build again. `latest`
  remains in the registry; typing it is a deliberate act.
- **FR-4** The registry prefix is a variable, and its default is lower-cased — **GHCR rejects an
  upper-case path**, and this owner's name has capitals.
- **FR-5** `RELEASE=vX.Y.Z` on any `up*` target adds the overlay **and** drops the `--build` that
  `up-apps`, `up-full` and `showcase` pass by default. One switch, because either half alone is a
  contradiction.
- **FR-6** The three image names the overlay runs are the three the release job publishes. Nothing
  else relates the two files, and a rename in one keeps working from the copy already on the
  machine until a new host joins.
- **FR-7** `make release-pull RELEASE=vX.Y.Z` fetches the three images and nothing else, separately
  from `up`, because a pull is the one step that needs the network and fails for reasons nothing
  else does — a version never published, or a package still private.
- **FR-8** The integrated composition starts **no** infrastructure: no Postgres, Keycloak, Kafka,
  Redis, Vault or collector. One started here is a second database beside the one the deployment was
  pointed at, and both would be healthy.
- **FR-9** The four addresses that cannot be guessed — database host, broker list, issuer, JWKS URL
  — are **required**. `docker-compose.apps.yml` defaults them to the container names of the stack it
  ships beside, which are right there and resolve to nothing here.
- **FR-10** `AIRA_REDIS_URL` and `VAULT_ADDR` must be **answered**, and may be answered with
  nothing. Both have a real "none" — per-process rate limits, and credentials from the environment —
  and both are states to choose rather than to arrive at. Required-and-non-empty would make a lie
  the only way to start.
- **FR-11** No service is left depending on a container the composition does not start. Compose
  refuses the **whole file** for a dependency on an undefined service, so this one fails for every
  integrated deployment at the first `up`; the dependencies between the applications themselves —
  a migration before the process that reads the schema — are kept.
- **FR-12** Creating the nine compacted Kafka topics is **opt-in** (`--profile create-topics`).
  Against a broker reached over SASL or TLS the one-shot cannot work, and two services wait on it
  completing successfully and would never start.
- **FR-13** The `aira` network **and the Compose project name** are declared by the integrated
  composition with the **same expression** as `docker-compose.yml` uses. A second expression for the
  network gives the applications a bridge of their own, on which every address supplied resolves
  somewhere else or nowhere. A different project name is subtler: Compose takes it from the project
  *directory* when no file declares one, so the composition would be the project `compose` while the
  stack is `aira` — and because the container names are explicit, nothing would look wrong. What
  breaks is `make down`, `make ps` and `make logs`, each of which names the stack and would report
  on, and remove, a project these containers are not in.
- **FR-14** Neither overlay declares a service the core files do not define. That is what keeps them
  out of `compose_files.ALL` — the list thirteen checks read to ask *"does any container receive
  this variable"* — honest.

## 5. Design & Architecture

Four files, four compositions, named once in `tools/compose_files.py` so that nobody spells a
combination by hand:

| Composition | `compose_files` | Started by |
| --- | --- | --- |
| infra + apps | `CORE` | `make up-apps` — builds, unchanged |
| infra + apps + release | `RELEASE` | `make up-apps RELEASE=vX.Y.Z` |
| infra + apps + showcase + release | `SHOWCASE_RELEASE` | `make showcase RELEASE=vX.Y.Z` |
| apps + external (+ release) | `INTEGRATED`, `INTEGRATED_RELEASE` | `make up-integrated [RELEASE=…]` |

**The release overlay is three properties per service**, aliased so an entry is one line: the image,
`build: !reset null`, and `pull_policy: ${AIRA_PULL_POLICY:-missing}`. `missing` because a version is
immutable — having pulled it once, asking the registry again on every start buys nothing and fails
without a network. `always` is for a tag that moves.

**The external overlay is three changes**: required addresses (aliased per plane), `depends_on`
reset or replaced, and topic creation behind a profile. It also carries the `aira` network
definition, because `docker-compose.apps.yml` names that network for every service and defines
nothing — the infrastructure file owns it, and this composition does not include that file.

Two traps are worth naming, because both are silent:

**`extends:` does not see an overlay.** `management-seed` is
`extends: {file: docker-compose.apps.yml, service: management-migrate}`, and `extends` reads that
file *from disk*, not the merged `-f` chain. So re-pointing `management-migrate` does not reach it:
without its own entry, the demo's seed would be the only container built from source, running
`manage.py seed_demo` out of a checkout against a database three published images own. Its entry
carries `profiles: ["demo"]`, because naming a service in an overlay *declares* one — an image with
no command, started by every `up` that does not layer the showcase on.

**A shared module's import list is part of its contract.** `tools/compose_files.py` gained
`import yaml` for the loader below, and `make down` broke with a traceback about PyYAML:
`tools/stack_addresses.py` imports that module, and the Makefile runs *it* with the system
`python3` on every target — `STACK_ADDRESSES := $(shell python3 tools/stack_addresses.py make)`.
The failure mode is the instructive part: `$(shell …)` swallows the exit code, so every address
became the empty string and `make wait-healthy` polled `curl -sf /` for four minutes before
reporting that the stack was not ready. The import is inside the one function that parses, and
`tools/tests/test_one_owner_for_the_stack_addresses.py` now runs that script with a bare
interpreter.

`compose_files.load()` exists because the two tags an overlay cannot do without — `!reset` and
`!override` — make `yaml.safe_load` raise. A test that read an overlay with `safe_load` would not
fail about the overlay; it would fail about YAML, from inside a helper.

## 6. Open

- **A private package needs `docker login ghcr.io` first**, and the error without it is a 401 that
  reads like a typo in the image name. These packages are public; the overlay cannot tell the
  difference, and nothing in it can.
- **`linux/amd64` only.** On an arm64 host the pull is what refuses, with the registry's words
  rather than this project's.
- **Compose reports one missing variable at a time**, not every reason at once — which is the rule
  the applications' own startup checks follow and as far as interpolation goes.
- **Every Compose command on a release composition needs the version**, `ps` and `logs` included:
  a required variable is required for reading the file at all, not only for starting it. The
  Makefile's `ps`, `logs` and `down` do not include the overlay, so they are unaffected; a hand-run
  `docker compose -f … ps` is refused until `AIRA_VERSION` is set. Met while verifying §7, and left
  as it is: the alternative is a default, and a default is what this file exists to refuse.
- **The overlay repeats a list: which services exist.** A ninth application service would run from
  source while eight ran from the registry. Guarded by a test that derives the list from
  `docker-compose.apps.yml`, not by review.
- **No Kubernetes manifests**, and no Compose file for the infrastructure a deployment supplies —
  by definition.
- **CI does not run the suite against a published image, deliberately.** The only version a
  pre-release commit could pull is the *previous* release, and running this checkout's tests
  against it would go red the first time a test moves ahead of the product — a gate that fails for
  being out of date rather than for being broken. The release job already tests the commit it
  publishes (`FRD-137` FR-5); what is left unchecked is the packaging, and that is checked by
  running it (§7) when a release is cut.
- **Nothing checks that a running image is the release it claims to be.** The tag is trusted;
  digests, signing and provenance are `FRD-137` §6.
- **A version cannot be named in a configuration file.** `config/integrated.example.yaml` is the
  integrated deployment's file, and `AIRA_VERSION`, `AIRA_IMAGE_REGISTRY` and `AIRA_PULL_POLICY`
  are not in it: they are Compose's own variables rather than settings of either plane, and
  `config_render.py` refuses what is not a setting. So the version lives on the command line
  (`RELEASE=`), which is one place rather than two — consistent with `ADR-0029`, and still a thing
  a reader may expect to find in the file and will not.

## 7. Testing & Acceptance Criteria

`tools/tests/test_a_release_runs_from_the_registry.py` — 21 guards (25 cases) over the parsed
overlays, the Makefile and the workflow. The ones that are about a failure nobody would see:

- **every image built here is re-pointed**, derived from `docker-compose.apps.yml` rather than
  listed, so a ninth service is a failing test and not a mixed-version stack;
- **`build` is present and `None`** — *reset*, not overridden. The two states are different
  statements, and only one of them prevents a build;
- `${AIRA_VERSION:?` with no `:-latest` or `:-dev` fallback anywhere;
- the three image names **equal the three the release job pushes**, read out of `ci.yml`;
- the seed is re-pointed **and** confined to the demo profile (the `extends` trap);
- the integrated overlay starts **no** service the infrastructure file defines;
- **the merged dependency graph**, computed the way Compose merges, names only services the
  composition starts;
- `:?` for the four addresses and `?` for the two that may be answered with nothing — asserted as
  two separate properties, because "required" and "must be answered" are different rules and the
  wrong one in either place is a working file with a wrong meaning;
- the `aira` network expression **and the project name** match `docker-compose.yml`'s;
- `RELEASE=` changes **the file and the flag together**, and no `up` target hard-codes `--build`;
- `COMPOSE_INTEGRATED` does **not** contain the infrastructure file;
- and one test that is allowed to skip: `docker compose config` over all four compositions, which
  shells out and so skips where there is no `docker` (present on a GitHub-hosted runner, not
  guaranteed anywhere). It is what catches a `!reset` spelled `!resets`, or an anchor defined below
  its use — things that parse as YAML and break the composition.

Mutations `IM1`–`IM20`. Every one of them leaves a stack that comes up healthy, which is the
point: a `build:` left in place, an address defaulted instead of required, a dependency that merges
instead of replacing, an image renamed on one side of the registry. `IM18` is the import that broke
`make` itself, and it is defended by
`tools/tests/test_one_owner_for_the_stack_addresses.py` rather than by this file, because what it
breaks is the Makefile's address source and not a compose overlay.

*One guard was found by its own mutation,* again. `IM11` turns the **gateway's** required JWKS URL
back into a default, and the first draft of the test asked whether `${AIRA_OIDC_JWKS_URI:?` appeared
in the file — which it still did, in the control plane's copy. Green about a deployment where one of
two planes reached for a container that is not there. Checked per occurrence now: the same shape as
`FRD-137`'s `PB18`, two features apart, and the same cause — **a substring is present as long as
one of the places that needs it still has it.**

**Proven by running it, which is the only way this one can be proven.** On 2026-10-11, against
`ghcr.io/inigoimago/*:v0.1.0` pulled from the registry (not built and re-tagged):

- `aira_pgdata` was **deleted** first, so the published image's `alembic upgrade head` and
  `manage.py migrate` created both schemas from nothing;
- all six long-running application containers reported the GHCR references in `docker inspect`;
- `make demo-ready` seeded the demo through the published management image;
- and the **whole integration suite** — 1066 tests against a live stack, 15 skipped — passed
  against it, in 24 minutes, exit 0.

That last one also answers a question nothing had asked before: whether the images `FRD-137`
publishes are functionally complete. `v0.1.0`'s product code is identical to the commit under test
(the three commits since it are CI and test changes only), which is what makes the suite a fair
oracle for them.

**And the integrated composition, the same way.** The six application containers were removed and
started again from `docker-compose.apps.yml` + `external` + `release` alone — a composition that
manages no Postgres, no Keycloak, no Kafka, no Redis and no Vault — with the four addresses passed
in. The infrastructure left running stood in for the deployment's, which is exactly what it is from
that composition's point of view: a set of addresses it was handed and does not manage. The gateway
reported the supplied values in `docker inspect`, `make wait-healthy` passed in 3 seconds, the
integration suite ran again — **1066 passed, 15 skipped, 22 minutes, exit 0** — and `make down`
then left **no container behind**, which is the property `name: ${AIRA_STACK:-aira}` exists for.

The pull itself was exercised cold as well: `ghcr.io/inigoimago/aira-frontend:v0.1.0` was deleted
from the local store and `make release-pull RELEASE=v0.1.0` fetched it back through Compose,
skipping the two that were already there — which is `pull_policy: missing` doing what a pinned,
immutable version wants.

Two caveats about *this* run, so that nobody reads more into it than it says. The stand-in
infrastructure was reachable by container name on the shared bridge rather than by an address
outside Docker, so what was exercised is the composition and the configuration, not a network path
nobody here has. And because the test reused the bundled stack's Compose project, `up` warned about
orphan containers — the infra containers the composition does not define. A real integrated
deployment has no such containers in its project and sees no warning.

**Acceptance**

- *Given* `make up-apps RELEASE=v0.1.0`, *when* it runs on a machine that has never built AIRA,
  *then* the stack comes up and `docker inspect` names `ghcr.io/…:v0.1.0` for every application
  container.
- *Given* the release overlay and no `AIRA_VERSION`, *when* any Compose command is run, *then* it
  is refused with a message naming the variable — before a container starts.
- *Given* `make showcase RELEASE=v0.1.0`, *when* the demo is seeded, *then* the seed ran from the
  published image and not from this checkout.
- *Given* `make up-integrated` with no addresses supplied, *when* it runs, *then* it names a
  variable it needs instead of starting a container that cannot reach anything.
- *Given* `make up-integrated` with the four addresses supplied, *when* it runs, *then* exactly the
  eight application services start, and `make down` still removes them.
- *Given* a new application service in `docker-compose.apps.yml`, *when* the test suite runs, *then*
  it fails until the release overlay names it too.
