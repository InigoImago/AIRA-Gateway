# FRD-117 — Diagnostics and client compatibility

> Phase: 8 (KIRA parity) · Status: **Done except FR-7 (2026-08-06)** · Owner: Vadim Scheibe · Last updated: 2026-08-06
> Origin: `kira_api.md` §2.5, §2.6, §8.1–8.3, §12.2, programme: `ADR-0010`.
> Touches `FRD-001` and `FRD-105` (observability), `FRD-100` (surface).

## 1. Problem

A collection of small things the predecessor has and AIRA does not. Individually each is minor;
together they are the difference between an API an operations team can run and one they have to
ask us about.

- **No build identity.** Given a running instance there is no way to ask which commit it is. The
  first question of every incident is unanswerable from the system.
- **Health does not check the models.** `/readyz` probes Postgres, Kafka and Redis. It says
  nothing about whether the gateway can reach the thing it exists to reach, so a total upstream
  outage presents as green.
- **No trace correlation for the caller.** `trace_id` is on spans and in the audit row, but a
  client that reports "request X was slow" has no identifier to give us.
- **Upstream calls are not traced at all.** Only FastAPI is instrumented — no HTTPX, no
  SQLAlchemy. The single most interesting span in the system, the model call, does not exist. "The
  gateway is slow" cannot be split into our time and their time.
- **No CORS.** The SPA works because nginx proxies same-origin. Any other browser client cannot
  call the API at all.
- **OpenAPI 3.1 only, with no vendor metadata.** The predecessor also publishes 3.0 with
  `x-api-id`-style extensions because its API management portal requires them.

## 2. Goals & Non-Goals

**Goals**
- Answer "what is running", "can it reach the models", "which request was that" and "where did the
  time go" from the system itself.
- Make the API callable from a browser client that is not our SPA, deliberately rather than
  accidentally.
- Publish the schema in the form a corporate API portal can ingest.

**Non-Goals**
- Dashboards and alerting rules. This is about the signals; what watches them is separate.
- Making `/healthz` do more. Liveness stays trivial — see §5.2.

## 3. User Stories
- As an **operator**, I want to ask an instance which commit it is, so that an incident starts with
  a fact.
- As an **on-call engineer**, I want a red readiness signal when the models are unreachable, so
  that I do not learn it from a user.
- As an **application developer**, I want a trace id on my response so that a support request is
  one identifier rather than a timestamp and a guess.

## 4. Functional Requirements

- **FR-1 `GET /version-info`.** Commit, short commit, branch, build time, build number, and the
  environment. Unauthenticated. Absent build metadata yields nulls, not an error — the predecessor's
  behaviour, and correct: a development run has no build number and should still answer.
- **FR-2 Upstream readiness.** `/readyz` reports, per configured provider, whether it is reachable
  — using a **cached background probe**, never an inline call (§5.2).
- **FR-3 Upstream failure is degraded, not down.** Consistent with `FRD-405`: `/readyz` returns
  200 with `degraded: true` when an upstream is unreachable but the gateway is otherwise serving.
  A gateway that can still refuse requests correctly, enforce budgets and serve reporting is not
  down, and taking it out of the load balancer would help nobody.
- **FR-4 `x-trace-id` on every response**, including error responses.
- **FR-5 Outgoing calls are traced.** HTTPX and SQLAlchemy instrumented, so an upstream call and a
  query are spans with durations.
- **FR-6 CORS, configured explicitly.** An allow-list of origins from configuration, default empty.
  Not `*` — see §5.4.
- **FR-7 OpenAPI 3.0 alongside 3.1**, with configurable `x-`extensions in `info`.

## 5. Design & Architecture

### 5.1 Build metadata

Written at build time into a file the image carries, read once at startup. Absent file → nulls
(FR-1). The Compose and CI builds pass the commit and build number; a local `uv run` simply has
none, and that is a valid state rather than a broken one.

### 5.2 Health must not become a load generator

This is the design point worth the space.

The predecessor's `/health` probes the database **and every registered model** on every call. With
a Kubernetes readiness probe every few seconds, across every replica, that is a continuous stream
of upstream calls — billable ones, against a provider quota, whose purpose is to answer a question
whose answer changes rarely. It also makes the probe as slow as the slowest upstream, so a degraded
provider can cause readiness timeouts and evict healthy pods. A health check that can take down a
healthy service is a liability.

So:

- **`/healthz` stays trivial** — the process is alive. No I/O. It is what a liveness probe should
  call, and making it do more is how a restart loop gets built.
- **`/readyz` reads a cached verdict.** A background task probes each provider on an interval
  (default a minute) with a short timeout and a cheap call — a model list where the provider offers
  one, never a generation. `/readyz` reports the last verdict and its age.
- **A stale verdict is reported as stale**, not as healthy. If the prober has not run, that is
  itself information.

The probe result feeds the existing `DegradationLog` from `FRD-405`, so there is one vocabulary for
"something is not working but we are still serving" rather than a second one.

### 5.3 Tracing the call that matters

`HTTPXClientInstrumentor` and `SQLAlchemyInstrumentor`, enabled with the existing OTel switch.
SQLAlchemy is instrumented with statement text **hidden** — the predecessor does the same, and it
is right: a bound parameter can carry a prompt fragment or a subject identifier, and spans are
exported to a system with different access control from the database.

`x-trace-id` (FR-4) is set by middleware from the active span, on every response including errors —
which requires it to sit outside the exception handlers, or the responses that most need
correlating are the ones that lack it.

### 5.4 CORS is an allow-list, and the predecessor's setting is not one to copy

`kira_api.md` §8.1 has `allow_origins=["*"]` with `allow_credentials=True`. That combination is
rejected by browsers and, where a server implements it by reflecting the origin, it disables the
protection entirely — any site a user visits can call the API with their credentials.

Ours takes an explicit origin list, defaults to empty, and refuses `*` together with credentials at
startup rather than at request time. Parity here would be a security regression, and this is the
second place in the programme where the answer is "no" (`FRD-115` FR-5 is the first).

### 5.5 OpenAPI 3.0

A second document generated from the same routes, downgraded where 3.1 and 3.0 disagree (nullable
representation, exclusive bounds), served at its own path. The `x-` extensions come from
configuration rather than being hard-coded, since they identify *this deployment* in a portal.

## 6. Data Model
None.

## 7. API / Interface Contract

- `GET /version-info` → `{buildNumber, buildTime, environment, git:{commit, commitShort, branch}}`,
  camelCase as the predecessor has it. Any field may be null.
- `GET /readyz` → the existing envelope plus
  `checks.upstreams: {<provider>: {ok, detail, checked_at, required: false}}`.
- `GET /openapi-3.0.json` alongside `/openapi.json`.
- Every response: `x-trace-id`.

## 8. Security & Privacy

- `/version-info` is unauthenticated and returns a commit hash. That is a deliberate, small
  disclosure — the alternative is an endpoint nobody can use during an incident. It carries no
  configuration, no dependency versions and no paths.
- FR-6/§5.4: CORS is opt-in per origin.
- §5.3: SQL statement text is not exported.
- The upstream probe must not include credentials in what it reports (`FRD-115` FR-6).

## 9. Observability

This FRD *is* the observability work: upstream and database spans, the trace header, and a
readiness signal that reflects the dependency the service exists for.

## 10. Testing & Acceptance Criteria

- **Unit** — `/version-info` with and without the metadata file; `x-trace-id` present on a success,
  a 4xx and a 5xx; CORS headers for an allowed and a disallowed origin; `*` with credentials
  refuses to start; the 3.0 document is valid 3.0 and carries the configured extensions.
- **Unit (the probe)** — `/readyz` **never calls an upstream inline** (asserted by failing the
  provider's client and showing the endpoint still answers promptly); an unreachable upstream
  yields `degraded: true` with **200**; a stale verdict is reported as stale. The first of these is
  the one that protects §5.2 and must be written to fail against an inline implementation.
- **Integration** — against the live stack: an upstream span appears in the trace for a real
  request, and the trace id in the response header matches it.
- **Mutation** — the CORS list actually restricts; readiness is 200-when-degraded and not 503; the
  probe interval is actually honoured.

**Acceptance**
- *Given* a running gateway, *when* `/version-info` is called, *then* it returns the commit the
  image was built from.
- *Given* an upstream that is unreachable, *when* `/readyz` is called, *then* it returns 200 with
  `degraded: true` naming the provider, within the usual response time, and no upstream call was
  made during the request.

## 10a. What was built (2026-08-06)

FR-1 through FR-6. **FR-7 (a second OpenAPI 3.0 document) is not built**, and that is a choice
rather than an omission: it exists for a legacy API portal that this deployment does not have, and
a generated document nobody reads is a thing that silently stops matching the routes. It is a
half-day whenever a portal actually needs it.

### The mistake the first draft made, and why it mattered

The prober called `provider.models()`. That is **local configuration** — evaluated once when the
registry is built — so it can neither fail later nor say anything about the network. Every verdict
would have been a confident green describing nothing at all, which is *worse* than no probe,
because a green board is acted upon. It was found by writing a test with a provider that raises,
discovering the provider could not be registered at all, and following that back.

Adapters now implement an optional `ping()` — a **GET of a listing**, never a generation. An
adapter without one is reported `probed: false, "not checked"`, because "we did not look" and "it
is fine" are different answers and only one is safe to act on. Verified live: `gpu-a` and `gpu-b`
report `2 model(s) listed, 2ms`; the mock says it was not checked.

### The three rules the design turns on

- **A health check must not be able to take down a healthy service.** The predecessor's probes
  every model on every call, making readiness as slow as the slowest upstream. A live test asserts
  ten readiness probes finish in under five seconds, and another asks the model server directly
  whether probing loaded anything.
- **Stale is reported as stale.** A prober that died leaves its last good verdict behind, and a
  reader that trusted it would see a green board describing a minute long past. Staleness counts as
  degraded.
- **Unreachable is degraded, not down.** Verified by stopping the model container: `/readyz` stayed
  **200 `ready`** with `degraded: true`, and cleared when the container came back. A load balancer
  keeps the instance; the signal is for an alert, not an eviction.

`x-trace-id` sits in pure ASGI middleware mounted outermost — a `BaseHTTPMiddleware` would run the
app in a separate task and lose the span context, so the header would be missing exactly when a
span exists. Confirmed against the deployed gateway: the 401 carries one.

CORS refuses `*` **with credentials at startup**. The predecessor ships that combination
(`kira_api.md` §8.1); browsers reject it, and a server implementing it by reflecting the origin lets
any site a user visits call the API with their credentials. A misconfiguration that only shows up
under a browser is one that ships.

24 hermetic tests, 9 integration, mutations **D1**–**D6**.

## 11. Dependencies & Risks

- Independent of the rest of the programme; can ship at any point and makes the rest easier to
  operate, which is an argument for doing it early.
- **Risk** — the readiness probe is one more background task with its own failure mode. It uses the
  same shape as `FRD-405`'s degradation reporting rather than inventing another.

## 12. Rollout / Demo

The Compose build passes commit and build number so `/version-info` is populated in the demo
stack. Stopping the mock provider demonstrates FR-2 and FR-3: `/readyz` turns degraded, stays 200,
and names the provider.
