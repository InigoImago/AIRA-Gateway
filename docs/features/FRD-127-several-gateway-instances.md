# FRD-127 — Several gateway instances

> Phase: 9 (operability) · Status: **Draft — the evaluator defect is fixed; the rest is a gap** · Owner: Vadim Scheibe
>
> Origin: the owner. Related: `FRD-405` (rate limits), `FRD-401` (budgets),
> `FRD-204` (read-model), `FRD-500`/`501` (anomaly detection), `ADR-0007` (deployment safety),
> `ADR-0014` (detection is asynchronous).

> **This is not `FRD-118`.** That one is about several *Keycloak issuers* and came from reading the
> predecessor's code. This one is the owner's actual requirement, and it was recorded under the
> wrong name in `CLAUDE.md` §6 — which is the failure mode that section exists to prevent, since a
> planner reads it before anything else.

## 1. Problem

One gateway process is one thing that can stop. Two consequences, and the second is the one that
motivated this:

- **Availability.** A crash, an OOM, a host reboot takes the whole data plane with it.
- **Updates are a flag day.** Deploying today means stopping the gateway and starting the new one:
  every in-flight request dies and there is a window with no data plane at all. The owner's
  requirement is a **rolling update** — bring one instance to the new version, confirm it is
  healthy, then the next.

**Management stays a single instance** (owner's decision, same conversation). It is the control
plane: it is not on the request path, its traffic is a handful of console users, and a single
instance keeps the seed, the migrations and the outbox relay free of coordination questions that
would buy nothing. Only the gateway is scaled.

## 2. Goals & Non-Goals

**Goals**
- *N* gateway instances behind a load balancer, each serving any request.
- A rolling update: one instance at a time, with the old and new versions **serving concurrently**
  for the duration.
- No control weakens as *N* grows — a limit of 60/min is 60/min across the fleet, not per instance.
- No evidence is duplicated or lost by scaling.

**Non-Goals**
- Scaling Management, the Kafka consumer, or the retention worker. Each stays exactly one (§5.2).
- Multi-region or active/active across datacentres. One deployment, several processes.
- Autoscaling. The count is an operator's decision.
- Zero-downtime *schema* changes as a general programme. §5.4 bounds what this requires.

## 3. User Stories
- As an **operator**, I want to update the gateway without a window in which the API is down.
- As an **operator**, I want an instance that fails its readiness probe taken out of rotation rather
  than serving errors.
- As **IT Security**, I want the same anomaly finding once, not once per instance.

## 4. Functional Requirements

- **FR-1 Stateless request path.** No request may depend on state held only in the process that
  serves it. **Already true** (§5.1).
- **FR-2 Shared enforcement.** Rate limits and budget reservations are counted in Redis, so the
  fleet shares one allowance. **Already true and tested across instances** (§5.1).
- **FR-3 Singleton background work.** The Kafka consumer and the retention worker run **once**
  (§5.2). The anomaly evaluator runs in every instance and claims a lock so exactly one evaluates
  per tick — **done** (§5.3).
- **FR-4 Graceful shutdown.** On `SIGTERM` an instance stops accepting new work, finishes what is in
  flight, and **drains its audit queue** before exiting. **Already true** (§5.1).
- **FR-5 Readiness gates rotation.** `/readyz` is what the load balancer polls; an instance reports
  ready only after its first upstream probe has a verdict. **Already true.**
- **FR-6 Two versions may serve at once.** A migration deployed by version *n+1* must not break
  version *n* while both are running (§5.4).
- **FR-7 Scalable by configuration.** `docker compose up --scale gateway=N` works (§5.5).

## 5. Design & Architecture

### 5.1 What already holds, and why

Worth stating precisely, because the honest answer is *most of it* and an FRD that implied
otherwise would send somebody to rebuild working code.

- **No in-process caches on the request path.** Every decision reads Postgres or Redis per request.
  The one memo that exists, `ModelCatalog.per_request()`, is scoped to a single request on purpose:
  a catalog cached for the application's lifetime would keep answering an old declaration after
  somebody replaced it, which is `FRD-307` inverted.
- **Rate limits and budgets are already shared.** `RedisTokenBucket` and the Lua reservation scripts
  count in Redis, and `test_two_gateway_instances_enforce_one_limit_not_one_each` builds two
  services with separate caches and asserts the burst is **shared, not granted to each** — the
  property this whole FRD depends on, tested at the layer where it is real.
- **The bucket refills on Redis' own clock**, so instances that disagree about wall time still agree
  about the rate.
- **Shutdown drains the audit queue.** `LogWriter.stop` races the drain against the worker so a
  redeploy cannot discard queued rows, and cannot hang waiting for a worker that has died either.
  The comment there already says *"a redeploy must not discard audit rows"* — written before this
  requirement existed, and correct for it.
- **Readiness is a verdict before serving.** The lifespan probes once before yielding, so a fresh
  instance never reports "unknown" into a load balancer.

### 5.2 The processes that must stay singletons

The architecture is already the right shape: the API, the Kafka consumer, the retention worker and
Management's outbox relay are **separate containers**. Scaling the API therefore does not scale the
background work — provided nobody scales the others. This FRD's contribution is to say so, and to
make it hard to get wrong:

| Process | Count | Why |
|---|---|---|
| `gateway` | **N** | the request path; the subject of this FRD |
| `gateway-consumer` | 1 | applies `pipeline.upserted` and friends; two would apply each event twice |
| `gateway-retention` | 1 | deletes expired payloads; two would race over the same rows |
| `management`, `management-relay` | 1 | owner's decision (§1) |

### 5.3 The anomaly evaluator — **fixed**

**Found while writing this FRD, and it only misbehaved when scaled.** `AnomalyService` starts in the
gateway's own lifespan — so every instance ran it — and three things about it were per-process: the
touched set, the cooldown map, and the decision to evaluate at all. Instances therefore evaluated
the same shared `request_logs`, reached the same verdict, and wrote an event each, while both sat
inside their own cooldowns — so the mechanism meant to stop repeat firing was the one thing that
could not see the repeat. With enforcement on, one finding became one suspension per instance.

Each of the three is now a **shared** fact:

- **Which scopes saw traffic** is read from `request_logs`. The old set was filled by *this*
  instance's audit writer, so behind a load balancer the evaluator knew about the fraction of
  traffic it happened to serve and the rest was measured by no rule at all — and nothing said so: it
  evaluated, found nothing, and looked exactly like a quiet minute. A watermark (`_since`) replaces
  the set, which also simplifies the give-the-window-back property: a failed round leaves the
  watermark where it is, so there is nothing to merge.
- **The cooldown** is the `anomaly_events` table. It now holds across instances *and* across
  restarts — a rolling update used to bring up an instance with an empty map, so every rule fired
  again the moment it started, describing traffic its predecessor had already reported. That was a
  second defect hiding inside the first, and it is the one that would have bitten every deploy.
- **One evaluator per tick**, claimed with `pg_try_advisory_xact_lock`. Transaction-scoped
  deliberately: released however the transaction ends, where a session-scoped lock survives a
  crashed process until its connection is reaped and would leave the fleet with *no* evaluator —
  turning a duplicate-detection defect into an absent-detection one, which is much worse.

**The recommendation in the first draft of this FRD was option 1 — move it to its own container —
and implementing it changed my mind.** A new container makes the singleton structural, which is
genuinely the better property; but it also means every existing deployment silently stops detecting
anything until its operator adds the container. A capability that disappears on upgrade unless
somebody reads the release notes is worse than one that needs a lock. The lock keeps one deployment
unit, works at N=1 and N>1, and needed no compose change, no Helm change and no operator action.

Note that the lock alone would have been **wrong**: the instance that wins it must see the whole
fleet's traffic, which is why the touched set had to leave the process first. Either half without
the other is a fix that looks like one.

The audit writer's `on_written` hook is gone with the touched set. It told the evaluator something
the evaluator can now read for itself, and told it only about one instance's share — a parameter
whose only caller was the defect.

### 5.4 Two versions serving at once — the schema rule

FR-6 is the constraint a rolling update actually imposes, and it is easy to violate without
noticing. **This repository violated it on 2026-08-16**, which is the honest example to record:
migration `0035` drops `pipeline_configs.start_model`, and the version before it selects that
column on every pipeline read. A rolling update from `09d1700` to `3abedde` would leave the old
instances erroring against the new schema until the last one was replaced.

The rule that avoids it is ordinary and needs writing down here because nothing enforces it today:

- **A destructive migration is deployed one release after the code that stopped using the column.**
  Release *n* stops reading it; release *n+1* drops it. Both halves are dull; skipping the gap is
  what breaks a rolling update.
- **Additive migrations are safe** in either order, provided the new column is nullable or has a
  default — which is what `0034` did.
- A migration that renames is two releases, never one.

Whether to *enforce* this (a check that fails CI when a migration drops or renames a column the
previous release still selects) is a decision for when this FRD is built. Stating it is the minimum.

### 5.5 Compose blocks scaling today

Every service sets `container_name`, and Docker refuses to create two containers with one name — so
`docker compose up --scale gateway=3` fails outright. The fixed names are genuinely useful for
`docker logs aira-gateway` and for the health checks, so this is a trade rather than a mistake: drop
`container_name` from `gateway` alone, or provide a scaling overlay that omits it. In Kubernetes the
question does not arise.

### 5.6 What a load balancer needs from us

Nothing new, and worth confirming rather than assuming: `/readyz` already reports Postgres, Kafka and
the counters, and distinguishes *degraded* from *not ready* so that a Redis outage takes the fleet to
a documented fallback rather than out of rotation. Sticky sessions are not required — there is no
session.

## 6. Data Model

None of its own. The change in §5.3 moves where a background task runs; it does not add a table.

## 7. API / Interface Contract

No API change. Configuration only: the instance count, and — if §5.3 option (1) is taken — one more
container in the compose file and the Helm chart.

## 8. Security & Privacy

- **A control that weakens with N is a control that fails silently.** FR-2 is therefore a security
  requirement, not a performance one: per-process counters would grant N× the configured limit while
  each instance remained individually "correct".
- Duplicate anomaly events (§5.3) are an *evidence* defect: an incident review that counts findings
  would over-count by the instance count, and duplicated suspensions are duplicated enforcement
  decisions with one author.

## 9. Observability

Every audit row and span should carry the **instance** that served it. Without it, "one instance is
slow" and "the system is slow" are the same picture, which is the question a fleet exists to let you
ask. Cheap: a hostname on the row.

## 10. Testing & Acceptance Criteria

- **Integration** — the existing two-instance rate-limit test is the model; extend it to budget
  reservations, which have the same shape and are the other control that must not scale with N.
- **Unit, done** — two evaluators over one database write **one** event between them; a restarted
  evaluator does not re-fire inside the window; the touched scopes come from the audit rows rather
  than from the process. Guarded by `N10b` and `N10c`.
- **Integration, still to write** — the advisory lock itself, which only exists on Postgres: two
  real evaluators ticking concurrently, one of which returns empty because it did not claim the
  tick.
- **Integration** — an instance stopped mid-request drains its audit queue: the row for the
  in-flight request is present after shutdown.
- **Live** — a rolling update with traffic in flight: no 5xx, and the audit trail has no gap.
- **Mutation** — the shared-counter property was already guarded; `N10b` and `N10c` now guard the
  shared cooldown and the shared touched set. Two existing mutations were re-anchored onto the code
  that carries their properties now (`N10`, `QA29`), and `QA29` **survived** the first re-anchoring:
  its test failed before the round ever opened a session, so where the watermark is written was
  never exercised. That test exists now.

**Acceptance**
- *Given* three instances and a limit of 60/min, *when* traffic arrives at all three, *then* 60 are
  served in the minute and the rest are refused.
- *Given* a rolling update, *when* one instance is replaced, *then* no request fails and no audit row
  is lost.
- *Given* a rule that fires, *when* three instances are running, *then* exactly one event is
  recorded and exactly one suspension is created.

## 11. Dependencies & Risks

- ~~The anomaly evaluator must be settled before scaling~~ — **done** (§5.3). It was the one item
  here that was a defect rather than a gap.
- **The schema rule (§5.4) is a process risk, not a code one.** Nothing enforces it; the repository
  has already broken it once.
- Redis becomes load-bearing for correctness at N > 1 rather than merely for sharing. The fallback
  to in-process buckets (`FallbackTokenBucket`) is *per instance*, so a Redis outage under N
  instances degrades to N× the limit — bounded and documented, but worth an operator knowing.

## 12. Rollout / Demo

A compose overlay running two gateway instances behind a small load balancer, and a documented
rolling update: update one, watch `/readyz`, update the other, with a request loop running
throughout that must not see an error.
