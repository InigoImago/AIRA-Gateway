# AIRA Gateway — Project Guidance (CLAUDE.md)

Guidance for anyone (human or AI) working on **AIRA Gateway — AI REST API**.
Read this first, then `docs/PRD.md` and `docs/ROADMAP.md`.

> Note: the sandbox/environment guidance lives in the parent `../CLAUDE.md`. **This** file is about
> the AIRA project itself: what we're building, how we build it, and the conventions to follow.

---

## 1. What we are building
An enterprise-grade AI gateway with two self-developed components + open-source infrastructure:
- **Gateway API** (data plane) — FastAPI.
- **Management & Monitoring** (control plane) — Angular SPA + Django REST Framework.
- Infra: PostgreSQL, Keycloak (SSO), Apache Kafka (event bus), HashiCorp Vault (secrets),
  OpenTelemetry Collector → SigNoz (observability). The two components communicate over **Kafka**.

Full detail: `docs/PRD.md`. Delivery is phased: `docs/ROADMAP.md`.

## 2. Locked-in decisions (see `docs/adr/` for rationale)
- **Language**: all docs, code, and identifiers in **English**.
- **Management UI**: **Angular** (TypeScript SPA) + **Django REST Framework** backend.
  Django keeps ORM/migrations/`django-guardian` object-level RBAC/admin; Angular is the frontend.
- **Gateway**: **FastAPI** (Python **3.14**).
- **Toolchain** (see `ADR-0003`): **Python 3.14 + uv**, **Node 26** (Angular). Pin versions in
  `pyproject.toml`/`.python-version` and `package.json` `engines`/`.nvmrc`.
- **AuthN**: Keycloak OIDC (bearer) **and** self-generated API keys (hashed at rest).
- **Roles (initial)**: Global Administrator, IT Security, IT Steuerung (Governance),
  Use Case Administrator, Use Case User. Least-privilege, object-scoped.
- **Secrets**: only in **HashiCorp Vault** — never commit secrets.
- **Observability**: OTLP → OpenTelemetry Collector → **Grafana `otel-lgtm`** locally (ADR-0004,
  supersedes the earlier SigNoz choice in ADR-0002).
- **Deployment**: **Docker Compose** locally now; Kubernetes/Helm later.
- **Demo mode**: mock upstream + one-command **automated seeding** must always work.

## 3. Engineering conventions
- **Test-first / high coverage**: near-100% unit-test coverage is a hard goal; **CI enforces the
  gates** (`.github/workflows/ci.yml`) — Python via `pytest --cov-fail-under`, Angular via
  `coverageThresholds` in `angular.json`. `make ci` runs exactly what CI checks, locally.
  Every feature ships with tests. No feature is "done" without tests. Frontend tests assert the
  **rendered DOM and real interactions**, not just component methods.
- **A green test proves nothing on its own.** It proves the code and the test agree, which they
  inevitably do when both were written from the same mental model — and line coverage cannot see
  a *missing requirement*: a review once found seven real defects behind a green suite at 99%
  coverage. So: **prove a test can fail.** Break the property, watch it go red, restore.
  `make mutants` (`tools/mutation_check.py`) does this for **421 properties** across auth, budgets,
  pipeline, retention, the management control plane and the gateway's counters; when
  you fix a bug, add the mutation that reintroduces it. Two traps that cost real defects here:
  a stand-in that is more permissive than the thing it replaces (reuse the real method where you
  can), and a test whose setup never reaches the path it is named after — SQLite enforces no
  column lengths, and `TestClient` buffers a whole streamed body before you can hang up.
- **Three test layers**, each for what the layer below cannot see:
  `unit` (hermetic, `make test`) → `tests/integration/` (live stack, `make test-integration`)
  → `e2e/` (real browser, `make test-e2e`). Anything needing a user token belongs in `e2e/`: the
  dev realm has the password grant disabled, so a token only comes from the real code flow.
- **Typed code**: Python type hints (mypy), TypeScript strict mode.
- **A surface parses; the layer decides.** Both halves of the request path now have one owner —
  `prepare_for_dispatch` before dispatch (`FRD-126`) and `accounting` after it (`FRD-128`). The
  second was found by asking whether every path had been tested with a dropped connection: four of
  six lost the audit row when a caller went away mid-answer. A request that reached an upstream is
  recorded however it ended, including `499`/`client_gone`. `api/serving.py` shared the *steps* of the pre-dispatch
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
- **Lint/format**: Python (ruff + black), Angular (eslint + prettier). CI blocks on violations.
- **API contracts**: OpenAPI for HTTP; explicit, versioned schemas for Kafka events.
- **Config over code**: behavior driven by use-case configuration, not hard-coded branches.
- **Async on the hot path**: persistence and event emission must not block the gateway request path.
- **Security by default**: validate input, scope every query by role/object, redact where required.

### Reader-facing documentation
The ADRs and FRDs record *why*; six documents record *what*, and the `README.md` is a hub linking
them: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (C4 in Mermaid),
[`docs/REQUEST-LIFECYCLE.md`](docs/REQUEST-LIFECYCLE.md) (one request, every control, in order),
[`docs/SETUP.md`](docs/SETUP.md) (demo · standalone · dev · integrated),
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) (every `AIRA_*` variable, defaults dumped from the
settings classes rather than remembered), [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md) (what each
connected system must provide) and [`docs/GAP-ANALYSIS.md`](docs/GAP-ANALYSIS.md) (requirements
against reality). Licence: **Apache 2.0** (`LICENSE`, `NOTICE`). When a feature changes what a
reader would do or expect, the relevant one of those six changes with it — a link checker and the
settings dump make the mechanical half cheap.

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

**What must not go in `CLAUDE.md`.** Per-feature status, and the story of a round. Both have a home
above, and both were written here as well until §6 stood at **1667 lines — 93% of this file** and
was a third copy of the DEVLOG. The cost was not length: twenty-two FRD headers had
gone stale saying *Draft* about shipped features, because the copy that is read every session
stayed true and the copy nobody opens rotted. `tools/tests/test_claude_md_stays_short.py` fails when
§6 grows past its limit, for the reason every other guard here exists: a rule only a reviewer
enforces is one the next round breaks.

Rule of thumb: if a future contributor would be surprised or have to reverse-engineer a decision,
write it down (ADR or DEVLOG). Prefer small, frequent updates over big retroactive ones.

## 5. Repository layout (target)
```
AIRA/
├── CLAUDE.md                  # this file
├── README.md
├── docs/
│   ├── PRD.md                 # requirements
│   ├── ROADMAP.md             # phases
│   ├── DEVLOG.md              # running change log
│   ├── adr/                   # architecture decision records
│   └── features/              # FRDs (one per feature)
├── gateway/                   # FastAPI data plane
├── management/
│   ├── backend/               # Django + DRF control plane
│   └── frontend/              # Angular SPA
└── deploy/                    # docker-compose, later helm/k8s
```
(Directories are created as phases begin; not all exist yet.)

## 6. Current status

**Phases 0–5 delivered; Phase 8 (KIRA parity) delivered; Phase 6 (reporting) delivered.** What
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
| [`docs/adr/README.md`](docs/adr/README.md) | why a decision was taken (18 ADRs) |
| [`docs/DEVLOG.md`](docs/DEVLOG.md) | what changed when, and what a round measured |
| [`docs/LESSONS.md`](docs/LESSONS.md) | **rules this project has already paid for** — read before planning |
| [`docs/PRD.md`](docs/PRD.md) §1.1 | the owner's canonical feature list |
| [`docs/GAP-ANALYSIS.md`](docs/GAP-ANALYSIS.md) | requirements against reality |

**Test layers** (all four are run; totals in `docs/TESTING.md`): hermetic `make test` · live-stack
`make test-integration` · browser `make test-e2e` · `make mutants`, which breaks a property and
requires a test to notice.

**Open, deliberately** — the three features the index marks unfinished, plus two decisions:

- `FRD-118` (several Keycloak backends) — requirement not confirmed.
- `FRD-121` (document normalisation for models that cannot read documents) — specified so the
  option exists; the recommendation is not to build it first (`ADR-0012` §4, `ADR-0013`).
- `FRD-307` — approval is delivered; candidate lists and builder pickers are not.
- `FRD-406`'s **PII half is declined, not deferred** (`ADR-0016`): the sensitive content and the
  useful content are the same content, so stored prompts are gated by role and every read is
  recorded instead.
- `FRD-106` (an OpenAI-compatible **surface**) stays **withdrawn** — `FRD-132` measured a real
  coding assistant against the existing Gemini surface and it worked unmodified. The OpenAI
  *dialect* as an upstream is unaffected and shipped.

A stream still cannot fall back once a chunk is on the wire; conditions are checked, the chain is
not. Recorded rather than closed — a fallback for streams is a feature.

## 7. Working agreement
- Confirm scope via PRD/FRD before large changes; work phase by phase.
- Read relevant files before editing; follow existing patterns.
- Run tests after changes; never weaken the coverage gate to make tests pass.
- Ask when requirements are ambiguous rather than guessing.
