# AIRA Gateway — Project Guidance (CLAUDE.md)

Guidance for anyone (human or AI) working on **AIRA Gateway — AI REST API**.
Read this first, then `docs/PRD.md` and `docs/ROADMAP.md`.

> Note: the sandbox/environment guidance lives in the parent `../CLAUDE.md`. **This** file is about
> the AIRA project itself: what we're building, how we build it, and the conventions to follow.

---

## 1. What we are building
An AI gateway with two self-developed components + open-source infrastructure:
- **Gateway API** (data plane) — FastAPI.
- **Management & Monitoring** (control plane) — Angular SPA + Django REST Framework.
- Infra: PostgreSQL, Keycloak (SSO), Apache Kafka (event bus), Redis (shared counters), HashiCorp
  Vault (secrets), an OpenTelemetry Collector (→ Grafana `otel-lgtm` locally). The two components
  communicate over **Kafka**.

Full detail: `docs/PRD.md`. Delivery is phased: `docs/ROADMAP.md`.

## 2. Locked-in decisions (see `docs/adr/` for rationale)
- **Language**: all docs, code, and identifiers in **English**.
- **Management UI**: **Angular** (TypeScript SPA) + **Django REST Framework** backend.
  Django keeps ORM/migrations/`django-guardian` object-level RBAC/admin; Angular is the frontend.
- **Gateway**: **FastAPI** (Python **3.14**).
- **Toolchain** (see `ADR-0003`): **Python 3.14 + uv**, **Node 26** (Angular). Pin versions in
  `pyproject.toml`/`.python-version` and `package.json` `engines`/`.nvmrc`.
- **AuthN**: Keycloak OIDC (bearer) **and** self-generated API keys (hashed at rest).
- **Roles**: a role is a permission set held through one Keycloak group (`ADR-0025`). Three are
  built in — Global Administrator and IT Security fixed, IT Steuerung editable — and an
  installation may define more. Administering or using a use case is a grant on it (`ADR-0017`).
  Least-privilege, object-scoped; every check asks one permission, never a role.
- **Secrets**: only in **HashiCorp Vault** — never commit secrets.
- **Observability**: OTLP → OpenTelemetry Collector → **Grafana `otel-lgtm`** locally (ADR-0004,
  supersedes the earlier SigNoz choice in ADR-0002).
- **Deployment**: **Docker Compose** locally now; Kubernetes/Helm later.
- **Demo mode**: mock upstream + one-command **automated seeding** must always work.

## 3. Engineering conventions
- **Test-first / high coverage**: near-100% unit-test coverage is a hard goal; **CI enforces the
  gates** (`.github/workflows/ci.yml`) — Python via `pytest --cov-fail-under`, Angular via
  `coverageThresholds` in `angular.json`. `make ci` runs CI's hermetic gates locally; the
  integration and browser suites run in CI on live stacks of their own, `make mutants` does not.
  Every feature ships with tests. No feature is "done" without tests. Frontend tests assert the
  **rendered DOM and real interactions**, not just component methods.
- **A green test proves nothing on its own.** It proves the code and the test agree, which they
  inevitably do when both were written from the same mental model — and line coverage cannot see
  a *missing requirement*: a review once found seven real defects behind a green suite at 99%
  coverage. So: **prove a test can fail.** Break the property, watch it go red, restore.
  `make mutants` (`tools/mutation_check.py`) does this for **1924 properties** across auth, budgets,
  pipeline, retention, the management control plane, the gateway's counters and the console — a
  `.spec.ts` selection runs under Vitest, everything else under pytest; when
  you fix a bug, add the mutation that reintroduces it. Two traps that cost real defects here:
  a stand-in that is more permissive than the thing it replaces (reuse the real method where you
  can), and a test whose setup never reaches the path it is named after — SQLite enforces no
  column lengths, and `TestClient` buffers a whole streamed body before you can hang up.
- **Three test layers**, each for what the layer below cannot see:
  `unit` (hermetic, `make test`) → `tests/integration/` (live stack, `make test-integration`)
  → `e2e/` (real browser, `make test-e2e`). Anything needing a user token belongs in `e2e/`: the
  dev realm has the password grant disabled, so a token only comes from the real code flow.
- **Typed code**: Python type hints (mypy), TypeScript strict mode.
- **A comment states the rule and its reason** (`ADR-0023`). How a rule was found — dates,
  measurements, "used to" — goes in the DEVLOG entry and the FRD, not in the code. Constants sit at
  the top of their module; a file that holds several concerns becomes a package, one module per
  concern, with an `__init__` that re-exports its public names.
- **A surface parses; the layer decides.** Both halves of the request path now have one owner —
  `prepare_for_dispatch` before dispatch (`FRD-126`) and `accounting` after it (`FRD-128`). The
  second was found by asking whether every path had been tested with a dropped connection: four of
  six lost the audit row when a caller went away mid-answer. A request that reached an upstream is
  recorded however it ended, including `499`/`client_gone`. `api/serving` shared the *steps* of the pre-dispatch
  path with both API surfaces and not their *order* — and every guarantee that layer makes is a
  guarantee about the order (rate limit before the pipeline, declaration and thinking after
  routing, reservation last). Both surfaces wrote the same six calls by hand until `FRD-126`;
  the third would have written them again. `prepare_for_dispatch` owns the sequence, a surface owns
  parsing and its error envelope, and `test_surface_layering.py` fails on a surface that calls a
  step directly. A layering rule only a reviewer enforces is one the next surface breaks.
- **A page is a parent plus panels.** `use-case-detail` grew to 1238 lines and six concerns
  before it was split: the parent loads and owns the tab bar (whose counts must exist before any
  tab is opened, which is why loading stays there), and each panel is a child owning its form
  state and mutations. A new tab is a new child, never another block in the parent. Outcomes go
  through the page's single `PageFeedback` — one banner per page, not one per panel.
  In a child, an `input()` is a **signal**: `{{ slug }}` renders the function, `{{ slug() }}`
  renders the value, and only a browser will show you the difference.
- **Angular is zoneless**: all mutable component state must be a `signal`. A plain property
  changed from code schedules no re-render, so `[(ngModel)]` is written as
  `[ngModel]="x()" (ngModelChange)="x.set($event)"`. See FRD-203 §4.
- **No silent failures in the UI**: every load and mutation reports its outcome through
  `core/api/error-message.ts`, which surfaces the backend's error envelope.
- **Lint/format**: Python is **ruff** — `ruff check` *and* `ruff format --check`, no black. Angular
  is **Prettier**, with `ng build` (so `tsc`) standing in for a linter. CI blocks on all of these
  (`make lint-py`, `make lint-frontend`, `.github/workflows/ci.yml`).
  **There is no ESLint** — no config, no `lint` target, no CI step; do not claim one until it exists
  (`LESSONS.md` §5).
- **A change to what is processed is a change to the privacy notice** (`ADR-0027`, `FRD-625`).
  Every stored column of both planes is named in `apps/privacy/activities.py` or declared not
  personal, and the suite fails otherwise; a changed register or text needs a new `EDITION` and
  `SOURCE_DIGEST` (`apps/privacy/edition.py`) and is described in **every** `texts/*.toml`. A new
  transmission, telemetry attribute or browser storage stores nothing the check can see — add it
  to the register and the texts by hand. Say what the system does, gaps included.
- **API contracts**: both planes' HTTP APIs and the events between them are in `docs/API.md`, held
  to the code by tests. Events carry no version: a change to one keeps both planes' handlers and
  the cross-plane tests in step, and an older gateway skips an event type it does not know.
- **Config over code**: behavior driven by use-case configuration, not hard-coded branches.
- **Async on the hot path**: persistence and event emission must not block the gateway request path.
- **Security by default**: validate input, scope every query by role/object, redact where required.

### Reader-facing documentation
The ADRs and FRDs record *why*; these record *what*, and the `README.md` is a hub linking them:
[`ARCHITECTURE`](docs/ARCHITECTURE.md), [`REQUEST-LIFECYCLE`](docs/REQUEST-LIFECYCLE.md) (one
request, every control, in order), [`deployment/`](docs/deployment/README.md) (four ways to run it),
[`CONFIGURATION`](docs/CONFIGURATION.md) (every `AIRA_*` variable, defaults dumped from the settings
classes), [`INTEGRATIONS`](docs/INTEGRATIONS.md), [`API`](docs/API.md), [`ROLES`](docs/ROLES.md) and
the planning map [`GAP-ANALYSIS`](docs/GAP-ANALYSIS.md). Licence: **Apache 2.0**. Except that map,
**they describe and never cite** an FRD, an ADR, the DEVLOG, the lessons or this file: the reader
needs the fact in the sentence (`test_reader_documents_describe_rather_than_cite.py`). When a
feature changes what a reader would do or expect, the relevant one changes with it — and a document
is read against the code **whole**: the older half of a page goes stale while its newest section is
right.

## 4. Documentation discipline (IMPORTANT — keep this current)
Always keep documentation in sync with what is actually built. On any meaningful change:
1. **ADRs** — record every significant/architectural decision as an ADR in `docs/adr/`
   (copy `docs/adr/ADR-TEMPLATE.md`, increment the number, link it from `docs/adr/README.md`).
2. **FRDs** — before building a feature, write/refresh its FRD in `docs/features/`
   (from `docs/features/FRD-TEMPLATE.md`). Update it if the implementation deviates, **and update
   its `Status:` header when it ships** — that header is the single source, and
   `docs/features/README.md` is generated from it (`uv run python tools/features_index.py --write`).
3. **DEVLOG** — append a dated entry to `docs/DEVLOG.md`: what changed, what was measured, what a
   round found. **This is where the narrative goes.**
4. **LESSONS** — only when a round produced a *new* rule, add it to `docs/LESSONS.md`, **merged
   into the existing entry** if it is the same shape in different clothes.
5. **PRD/ROADMAP** — update these when scope, phases, or requirements shift.
6. Keep this `CLAUDE.md` updated as **conventions** evolve.

**Cutting a release is a documentation act too** (`ADR-0030`, `FRD-137`). A release is an
annotated tag, and **the tag's message is the release notes** — there is no changelog and no
second place a version lives. Write it from the template, never from a blank buffer:

```bash
make next-version                        # what to call it
make release-tag VERSION=v0.1.2          # opens docs/RELEASE-TEMPLATE.txt in $EDITOR
make release-check VERSION=v0.1.2        # the same refusal CI gives, before pushing
git push origin v0.1.2                   # this is what publishes
```

The template asks the reader's two questions in order — *what changed*, and *do I have to do
anything* — and five rules refuse an annotation that answers neither: it must be an annotated tag
object, with a subject that does not merely restate the version (`Release v0.1.0` and
`AIRA Gateway v0.1.1` are both refused), a body, and **no placeholder left from the template**. A
rejected annotation is deleted again, so a failed attempt does not spend a version number. Two of
those rules exist because a release went out wrong: `v0.1.0` with the annotation `Release v0.1.0`,
and `v0.1.1` carrying a *commit message* as its notes.

**What must not go in `CLAUDE.md`.** Per-feature status, and the story of a round. Both have a home
above, and both were written here as well until §6 stood at **1667 lines — 93% of this file** and
was a third copy of the DEVLOG. The cost was not length: twenty-two FRD headers had
gone stale saying *Draft* about shipped features, because the copy that is read every session
stayed true and the copy nobody opens rotted. `tools/tests/test_claude_md_stays_short.py` fails when
§6 grows past its limit, for the reason every other guard here exists: a rule only a reviewer
enforces is one the next round breaks.

Rule of thumb: if a future contributor would be surprised or have to reverse-engineer a decision,
write it down (ADR or DEVLOG). Prefer small, frequent updates over big retroactive ones.

## 5. Repository layout
```
AIRA/
├── CLAUDE.md · README.md · CONTRIBUTING.md · Makefile
├── docs/              # PRD, ROADMAP, DEVLOG, LESSONS, the reader-facing documents,
│                      # adr/ (decisions), features/ (FRDs), deployment/ (four ways to run it)
├── gateway/           # FastAPI data plane
├── management/        # backend/ (Django + DRF control plane), frontend/ (Angular console)
├── libs/              # aira_common — what both planes must agree on
├── config/            # one YAML per installation, rendered into a .env
├── deploy/compose/    # infrastructure, applications, demo provisioning, overlays
├── tools/             # scripts, and tests/ — the repository's checks of itself
├── tests/integration/ # the live-stack suite
└── e2e/               # the browser suite (Playwright)
```

## 6. Current status

**Phases 0–6, 8 (KIRA parity) and 9 (agents) delivered; Phase 7 in part** (published images,
running them, capacity measured — Helm open). What
runs today: two API surfaces (Gemini-compatible and the KIRA compatibility layer) over a
provider-agnostic core, four upstream families (Vertex EU with Gemini + Anthropic, Google AI
Studio, Azure/Foundry, any OpenAI-compatible server), a Django/DRF control plane with an Angular
console, and Kafka carrying configuration between them. Governance — use cases, roles from groups,
budgets, rate limits, model release, pipeline — and evidence — a complete audit trail, anomaly
detection, incident response, reporting and export — are both built.

**Per-feature status lives in each FRD's header**, and
[`docs/features/README.md`](docs/features/README.md) is generated from those headers
(`tools/features_index.py`; a test fails when they disagree). **Do not restate a feature's status
here** — that is what grew this section to 1667 lines and left twenty-two FRD headers saying
*Draft* about features that had shipped.

| Where to look | For |
| --- | --- |
| [`docs/features/README.md`](docs/features/README.md) | every feature, its status, its document |
| [`docs/adr/README.md`](docs/adr/README.md) | why a decision was taken (38 ADRs) |
| [`docs/DEVLOG.md`](docs/DEVLOG.md) | what changed when, and what a round measured |
| [`docs/LESSONS.md`](docs/LESSONS.md) | **rules this project has already paid for** — read before planning |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) §1a | **what one instance carries**, measured — read before promising a concurrency |
| [`docs/PRD.md`](docs/PRD.md) §1.1 | the owner's canonical feature list |
| [`docs/GAP-ANALYSIS.md`](docs/GAP-ANALYSIS.md) | requirements against reality |

**Test layers** (`docs/TESTING.md`): hermetic `make test` · live-stack `make test-integration` ·
browser `make test-e2e` · `make mutants`, which breaks a property and requires a test to notice.

**Open, deliberately** — the features the index marks unfinished, plus two decisions:

- `FRD-127` (**several gateway instances**) — the owner's requirement: availability, and a rolling
  update that takes one instance at a time. Management stays a single instance. Most of what it
  needs already holds; the two real gaps are named in the FRD.
- `FRD-118` (several **Keycloak** issuers) — FR-1 is **built** in the gateway (a token is routed by
  its own `iss`, `AIRA_OIDC_ISSUERS`; the console reads one issuer); FR-3, groups from UserInfo,
  stays declined. It is **not** the multi-instance requirement above — that is `FRD-127`.
- `FRD-616` (the audit trail as an **event stream**) — the delivery channel (`FRD-618`) forwards one
  span per request to a SIEM, refused attempts included; the audit rows themselves are not streamed.
- `FRD-121` (document normalisation for models that cannot read documents) — specified so the
  option exists; the recommendation is not to build it first (`ADR-0012` §4, `ADR-0013`).
- `FRD-406`'s **PII half is declined, not deferred** (`ADR-0016`): the sensitive content and the
  useful content are the same content, so stored prompts are gated by role and every read is
  recorded instead.
- `FRD-106` (an OpenAI-compatible **surface**) stays **withdrawn** — `FRD-132` measured a real
  coding assistant against the existing Gemini surface and it worked unmodified. The OpenAI
  *dialect* as an upstream is unaffected and shipped.
- **Strict data mode is on by default** (`FRD-627`, `ADR-0036`): a person — their content, figures,
  name and key on a request, a finding about them — is seen by Global Administrator and IT Security
  only. A test of the per-use-case ladder asks for `strict_data_off` by name.
- **Stated gaps, not oversights** (`docs/GAP-ANALYSIS.md`): FRD-131's `tool_results` filter scope
  is not built; configuration changes are not audited except roles; Kafka events carry no schema
  version; a refusal before attribution is on its span, not in the audit table (`FRD-122`).
- **A model reachable only at `global` is not usable here — unless permitted by name**
  (`FRD-611`, `ADR-0012` §6, `ADR-0034`). `global` names no region and guarantees none, so it is not
  in the residency default and `gemini-3.5-flash` stays refused where its region is typed. The open
  models GLM and DeepSeek answer only there, so each is permitted it in `AIRA_ALLOWED_MODEL_REGIONS`.

- **Capacity is measured** (`FRD-136`) and the answer is *not the request path*: a
  chat request costs **48 ms** of gateway CPU, an agentic one **93 ms**, and both scale linearly
  with worker count. What gives way is **deployment**, in three places — the image starts **one**
  uvicorn worker, so one instance is one core; a single event loop lengthens its tail at about
  two-thirds utilisation, so size for half a core of request work per worker; and every upstream
  adapter takes httpx's default **100 connections, 20 kept alive**, which binds before the
  processor does whenever answers are long. 300 agentic plus 300 chat seats is 2.5 cores of
  request work and about **68 GB a day** of audit trail. Nothing refuses under load — it queues,
  which from outside looks like a slow model.

A stream still cannot fall back **once a chunk is on the wire** — until then it can, and does
(`FRD-302`, `ADR-0032`): every model access is a chain now, including each pipeline step's own. What
remains stated rather than built is a fallback *mid-answer*, which would splice two models' text
into one answer, and a retry of the same model, which is a different feature with a different
failure mode.

## 7. Working agreement
- Confirm scope via PRD/FRD before large changes; work phase by phase.
- Read relevant files before editing; follow existing patterns.
- Run tests after changes; never weaken the coverage gate to make tests pass.
- Ask when requirements are ambiguous rather than guessing.
