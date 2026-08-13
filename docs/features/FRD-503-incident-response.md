# FRD-503 — Incident response

> Phase: 5 · Status: **Done** · Owner: AIRA
> Related: `ADR-0014`, `FRD-500` (the rules), `FRD-501` (the engine), `FRD-405` (rate limiting and
> the fail-closed rule), `FRD-122` (the audit row that records it), PRD §1.1 features 6 and 7

## 1. Summary

`FRD-501` detects and records. This acts.

A rule configured to `block` or `throttle` now creates a **suspension**: a written decision naming a
target, an author, a reason and an expiry. The pre-dispatch gate reads it, and a suspended caller is
refused before anything is spent. An operator can create the same object by hand — the kill switch
of PRD §1.1 feature 7 — and lift either kind.

Until this landed, a `block` rule detected and recorded `detected_not_enforced`, in those words.
That was the honest interim; this is the control.

## 2. Goals & Non-Goals

**Goals**
- A rule's action is carried out, on the target it names, for the time it states.
- An operator can stop a caller, a credential or a use case **now**, without authoring a rule.
- Every suspension names **who** created it and **why**, and every refusal it causes is an audit row
  with its own outcome.
- Lifting is as easy as applying, and is recorded too.
- Nothing expires silently into permanence: an automatic suspension has an expiry, always.

**Non-Goals**
- Not alert delivery (mail, webhook). An event is a row; who gets told has its own blast radius and
  is not in this stage.
- Not a replacement for rate limits or budgets. Those are steady-state controls, checked
  atomically on every request; this is what happens when something has already gone wrong.

## 3. Functional requirements

**FR-1** — A suspension has: a scope (`use_case`, or global), a **target** (`subject` |
`credential` | `use_case`) and its value, an **action** (`block` | `throttle`), a **throttle rate**
when throttling, an **expiry**, an **author**, and a **reason**.

**FR-2** — A rule that fires with a non-`alert` action creates one, lasting `action_minutes`. The
anomaly event records `blocked` or `throttled` rather than `detected_not_enforced`.

**FR-3** — `guard_before_work` — the one pre-dispatch gate every verb takes (`FRD-126`) — refuses a
blocked caller with **429** and a `Retry-After` naming when the suspension ends. Not 403: the
condition is temporary and the client's correct behaviour is to come back, which is exactly what 429
means.

**FR-4** — A throttled caller is admitted through an additional token bucket at the suspension's
rate, alongside whatever rate limits already apply. All-or-nothing across buckets, as `FRD-405` FR-4
requires, so a throttled request does not debit the limits that would have granted it.

**FR-5** — A refusal caused by a suspension is recorded with outcome **`suspended`**, its own value
in the closed vocabulary. Folding it into `rate_limited` would hide "we stopped this caller on
purpose" inside "this caller is going too fast", and those want different answers.

**FR-6** — `POST /v1beta/suspensions` creates one by hand; `GET` lists; `DELETE /{id}` lifts. All
three require an **oversight role** (IT Security or Global Administrator) — the same roles that may
author a global rule (`FRD-500` FR-8), because a hand-made suspension is a global rule's effect
without the rule.

**FR-7** — A hand-made suspension may have **no expiry**. A person who applied it can lift it; a
rule cannot, which is why an automatic one always expires (`ADR-0014` §2).

**FR-8** — Lifting records who lifted it and when. The row is kept, not deleted: "this caller was
blocked for two hours last Tuesday" is exactly the kind of question an incident review asks.

## 4. Behaviour and decisions

### 4.1 Postgres, not the shared counter store — an amendment to `ADR-0014`

`ADR-0014` §2 said the pre-dispatch gate would read decisions "from the shared counter store, seeded
from Postgres on a miss", by analogy with `FRD-405`. Building it showed the analogy is wrong.

A counter is written on **every request** and read on every request; Redis earns its place there
because Postgres cannot take that write rate and because concurrent requests must see each other's
increments. A suspension is written when something goes wrong — a handful a week — and read on every
request. That is a *cache* problem, not a shared-state problem, and a short-lived in-process cache
over Postgres solves it with one query every few seconds per instance and no second system.

It is also the safer failure mode, and that argument decides it. `FRD-405` settled that the moment a
control stops working is the worst moment to stop applying it. A suspension held only in Redis
disappears when Redis does; held in Postgres, it survives — and Postgres is already the database the
gateway cannot serve a request without.

The cost is stated: a lift takes up to the cache TTL to reach every instance. For a control that
*removes* a restriction, being slightly late is the harmless direction.

### 4.2 429 rather than 403

A blocked caller is not unauthorised — their credential is valid and their membership is real. They
are stopped, temporarily, and the honest status for "come back later" is 429 with a `Retry-After`.
403 would tell a client to fix its permissions, which is advice about a problem it does not have.

### 4.3 The kill switch does not go through Kafka

Every other piece of configuration is authored in Management and distributed over Kafka. This one is
not: it is created directly against the gateway, by a caller holding an oversight role.

An incident control that depends on the event bus fails exactly when the bus is the problem — and
"traffic is doing something alarming" and "the pipeline between the planes is unhealthy" are not
independent events. The gateway already knows realm roles (`ADR-0009`), so it can make the
authorisation decision itself, and the thing being stopped is on the data plane anyway.

The console reaches it through the existing `/gw` proxy, exactly as the dry-run and consumption
views already do.

### 4.4 A throttle needs a number, and says so

`throttle` was declared in `FRD-500` as an action and given no rate. The same shape as `FRD-501`
§4.4's missing byte figure, found the same way — by building the consumer — and fixed the same way:
`throttle_rpm` is required when the action is `throttle` and refused otherwise.

Twice now a declared setting has turned out to be missing the number it needs. The pattern worth
naming: **an enum member is not a specification.** Adding a value to an action or a kind should
prompt the question "what does this one need that the others do not", and the answer belongs in the
schema before anything ships.

## 5. Testing

- A rule that blocks creates a suspension with the rule as its author and an expiry; one that alerts
  does not.
- A suspended subject is refused at the gate with 429 and a `Retry-After`, and the audit row carries
  outcome `suspended`.
- The refusal happens **before** the pipeline, so a blocked caller does not pay for a classifier —
  the same property `FRD-126` was built for, asserted for this control too.
- A throttled caller is admitted at the throttle rate and refused above it, and the refusal does not
  debit the ordinary rate limits.
- An expired suspension does not refuse anybody; a lifted one stops refusing within the TTL.
- The endpoints refuse a caller without an oversight role, and the answer the object reports matches
  what the request does.
- Mutations for: the expiry being honoured, the lift being honoured, the outcome being `suspended`
  rather than `rate_limited`, and the oversight check.

## 6. What a live round found (2026-08-07)

84 cases against the running stack — a real Postgres, a real gateway process, a real model, and the
two planes actually talking over Kafka. Five defects, none of which the hermetic suites, the
mutation harness or a green `make ci` could have seen.

**1. Two planes, one question, two answers.** The gateway guarded its kill switch with
`has_oversight` — a **visibility** predicate — so `it-steuerung` could stop traffic there while
Management (correctly) refused it a global rule. PRD §154 gives that role every figure and no write
anywhere. Reusing "may see every use case" for "may stop every use case" is `FRD-206`'s mistake one
level down. There is now a third set, `INCIDENT_ROLES`, in `aira_common.roles`, and **both planes
read it**.

**2. The `payload_size` kind measured a column nothing wrote.** The middleware counted the bytes,
the column existed, and no wire ran between them — so a whole rule kind could never fire on real
traffic. The hermetic tests seeded the column directly and were green. Two correct halves and no
wire: the third time this repository has recorded that exact shape.

**3. A refused request was counted as unpriced.** The console reported **105** unpriced requests
where **5** had actually run on an unpriced model. A refusal has a NULL cost for the *opposite*
reason — nothing was spent because nothing ran — and counting both made the "spend is a lower
bound" caveat permanent, which is a warning nobody reads. The project's own rule in the direction it
was missing: unknown is not zero, and **zero is not unknown**. A NULL *outcome* still counts, because
that is a row from before `FRD-122`, when only served requests were logged at all.

**4. `aira.anomaly-rules` was created by nothing.** Rules were authored, Management answered 201,
the relay published, and the broker dropped every one. The only trace was a line in a consumer log.
This is the **second** time (`FRD-405` shipped `aira.rate-limits` the same way, and the DEVLOG says
so), and the topic list is written out by hand in three places while the names have a single source
of truth. So the fix is not a fourth copy: `tools/tests/test_kafka_topics_are_created.py` now checks
the three against the constants, in both directions.

**5. Thirty-eight mutation ids named more than one property.** Every entry ran, so the checking was
sound — but a report saying "N3 survived" named two unrelated things, and a summary that sends
somebody to the wrong line is worse than none. Later duplicates were renamed and the first of each
kept, because `CLAUDE.md` and the DEVLOG cite ids by name.

Two of the five were mine from this week; three were older and only became visible once something
actually exercised them. That is the argument for the layer.

## 7. Open

- Alert delivery (`FRD-502`'s console will show events; sending them somewhere is later).
- A suspension is not distributed to Management, so the console reads it from the gateway. That is
  deliberate for now (§4.3) and worth revisiting only if a second consumer appears.
