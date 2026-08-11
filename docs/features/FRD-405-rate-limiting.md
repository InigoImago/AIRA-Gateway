# FRD-405 — Rate limiting, atomic budget reservation, and persistence off the hot path

> Phase: 4 · Status: **Done** · Owner: Vadim Scheibe · Last updated: 2026-08-05
> Depends on `FRD-401` (budget enforcement) and `FRD-403` (cost budgets). Decided in `ADR-0008`.

## 1. Problem

Three defects with one shared cause: **the gateway decides on state it has already stopped
being sure about by the time it acts.**

**1. No caller can be slowed down.** A client in a retry loop is served at whatever rate it asks
for. Measured on the running stack: one request opens **six to seven separate database sessions**.
A burst therefore exhausts the connection pool, and the first casualties are the *other* use
cases, which did nothing wrong. The same burst spends the shared Google quota, which is throttled
per installation. A budget states how much may be spent, never how fast.

**2. A budget can be exceeded by a multiple.** `guard()` reads usage, dispatch runs, `record()`
books. Requests in flight are invisible to each other's guard:

```
A: reads 95 € of 100 € → passes → dispatches → books 0,50 €
B: reads 95 €  ← A has not booked yet → passes → …
C: reads 95 €  ← neither has booked → passes → …
```

Since FRD-403 the limit is a sum of money, so this is a wrong number on an invoice, not a
rounding artefact.

**3. The audit write blocks the answer.** `api/gemini/routes.py` awaits `record_request(...)`
before returning, so the caller waits for the log row to be committed. `CLAUDE.md` line 55 says
the opposite is required: *"persistence and event emission must not block the gateway request
path."* The code has been contradicting its own convention.

A load balancer in front of the gateway — the stated intention — makes (1) and (2) worse rather
than better: per-process counters mean N instances permit N times the configured limit.

## 2. Goals & Non-Goals

**Goals**
- A **rate limit per use case and per member**, enforced across all gateway instances.
- Budget enforcement that **cannot be overshot by concurrency**.
- The request log written **off the request path**, without ever losing a record.
- Degradation that is decided, not accidental: a Redis outage must not become a product outage.

**Non-Goals**
- Content redaction (`FRD-406`) — unrelated, deliberately deferred.
- Per-caller *concurrency* caps (how many requests at once, as opposed to per minute). The same
  Redis primitive supports it; it is not needed yet.
- Queueing or shaping. Over the limit means a `429`, not a delayed answer: the caller controls its
  own retry, and holding a connection open is exactly the resource a limiter is protecting.
- Global limits across use cases, and limits per API key rather than per subject.

## 3. Functional Requirements

- **FR-0 Every verb**: the controls apply to `generateContent`, `streamGenerateContent` **and**
  `embedContent`. They are enforced at one shared point rather than per method, because a control
  that applies to some verbs and not others is one a caller evades by picking another verb.
- **FR-1 Definition**: a rate limit belongs to a use case, with `scope` = `use_case` |
  `each_member` | `member` (mirroring budgets — see `FRD-400` §2.1 for why three), `limit_rpm`
  (sustained requests per minute) and `burst` (how many may arrive at once). Authored in
  Management, distributed over Kafka, read by the gateway.

  **`each_member` needed no change to the service** and that is worth stating rather than assuming:
  `_applicable` resolves every configured row against the caller on each request, so a row that
  names nobody binds whoever is asking, under exactly the bucket key a row naming them would use.
  The budget path had to be repaired for the same scope, because there the key was read off the row
  long after the caller was out of scope — the same rule, two implementations, one of them wrong.

  **Burst says what it costs, in the console.** It is the size of the bucket, not a second rate: a
  bucket of 20 refilling at `limit_rpm/60` per second lets twenty requests arrive together and then
  admits them at the sustained rate. Raising it does **not** raise how much a caller may send per
  minute; it decides how spiky that minute is allowed to be. Reported as unclear by the owner, who
  had configured the field — a control whose own author cannot say what it does is one that gets
  set by imitation.
- **FR-2 Enforcement**: over the limit → **429 `RESOURCE_EXHAUSTED`** with a `Retry-After` header
  in seconds, so a well-behaved client backs off instead of retrying immediately.
- **FR-3 Shared across instances**: two gateway processes behind a load balancer enforce **one**
  limit, not one each.
- **FR-4 Both scopes apply**: where a use-case limit and a member limit exist, both are checked
  and the stricter one wins. A member's own burst may not consume the whole use case — and,
  equally, a member who is *refused* must cost the use case nothing, or one throttled member
  becomes a denial of service for everyone else. The decision is therefore all-or-nothing across
  every bucket a request must pass.
- **FR-5 Atomic budget reservation**: the budget check reserves before dispatch, so a concurrent
  request sees the reservation. A completed request reconciles the reservation to the actual cost;
  a **failed request releases it** — an upstream error must not permanently consume budget. This
  holds on *every* exit path, including a stream that fails, a client that hangs up mid-stream,
  and a failure that is not an `UpstreamError` at all.
- **FR-6 Postgres stays authoritative**: Redis holds a running counter seeded from Postgres and is
  never the only copy. Losing Redis loses in-flight reservations, not the period's accounting.
- **FR-7 Decided degradation**: Redis unavailable → rate limiting falls back to a **per-instance
  in-memory bucket** (bounded, but N × the limit across N instances), budget enforcement falls
  back to the **Postgres read-then-book path** (today's behaviour: enforcing but racy). Both log
  a warning and surface in `/readyz` as degraded rather than failing it.
- **FR-8 Off by default**: a use case without a configured rate limit is unlimited, exactly as
  today. This feature must not silently start rejecting existing traffic on upgrade.
- **FR-9 Persistence off the hot path**: the response is returned before the log row is written.
  Under a full queue the write happens inline rather than being dropped — an audit trail may be
  delayed, never lost.

## 4. Design

### 4.1 Token bucket, not a fixed window

A fixed-window counter is simpler and wrong in a way that matters here: it permits twice the limit
across a window boundary (100 in the last second of one minute, 100 in the first second of the
next), and it treats a short legitimate burst exactly like sustained abuse.

A **token bucket** matches the actual goal — "fast is fine, sustained flooding is not". The bucket
holds `burst` tokens and refills at `limit_rpm / 60` per second. A request takes one token; an
empty bucket means 429, and `Retry-After` is the time until the next token accrues.

Refill is computed lazily from the elapsed time on each check, so no timer or background job is
needed and an idle bucket costs nothing.

The whole check — refill, test, take — is **one Lua script**, so it is one round trip and
indivisible. Doing it as separate `GET`/`SET` calls would reintroduce the very race this FRD
exists to remove.

### 4.2 Reserve, then reconcile

The budget cannot be checked exactly before dispatch, because the cost depends on how many tokens
the model returns. So:

```
guard    → reserve (1 request, estimated tokens, estimated cost)   ── atomic, in Redis
dispatch
success  → reconcile: adjust the reservation by (actual − estimate), persist the actual
failure  → release: undo the reservation entirely
```

The estimate uses the caller's `maxOutputTokens` where it was given, otherwise a configured
default. It is deliberately conservative: for a spend limit, briefly over-reserving is the safe
direction, and the correction lands within milliseconds.

**Redis is a cache, Postgres is the record.** On a cache miss the counter is seeded from
`budget_usage`, which `record` keeps current. A Redis restart therefore costs the reservations
currently in flight — not the month's accounting.

A counter also **expires well before its budget period does** (`COUNTER_TTL_SECONDS`, five
minutes). This is what bounds drift: if the correction after a request cannot reach Redis, the
counter keeps that request's deliberately high estimate, and it cannot be repaired from there —
the store holding the stale figure is the store that is unreachable. Letting it expire makes the
drift self-healing, and costs nothing, because every reservation already reads the Postgres figure
to seed with. The trade is that reservations still in flight when a counter expires are forgotten,
briefly reopening the race for whatever is in flight at that instant; a rare under-count of one
estimate is a far better failure than a permanent over-count nobody can clear.

### 4.3 What happens when Redis is down

Decided in `ADR-0008`, restated here because it is the part most likely to be reconsidered by
someone reading only this document:

| | Behaviour | Reasoning |
|---|---|---|
| Rate limiting | per-instance in-memory bucket | Deliberately **not** fail-open. Redis being down is when infrastructure is already strained — the worst moment to stop bounding a runaway caller. In-memory is exact on one instance and N × the limit on N, which is degraded but still bounded, and still protects the connection pool. |
| Budget enforcement | fall back to the Postgres read-then-book path | Refusing would turn a cache outage into an outage; skipping enforcement would turn it into a free-money mode. Falling back is exactly today's behaviour — enforcing, but racy. |

The asymmetry is the point: an ephemeral per-window counter may live in memory, a figure about
money may not. For a budget the only honest fallback is the store that is already authoritative.

### 4.4 Persistence off the hot path

A bounded queue with a worker draining it, started and stopped with the application lifespan.

Two properties are non-negotiable. **Bounded**, because unbounded background tasks under load are
the same failure mode the rate limiter exists to prevent — the queue would simply move the
exhaustion from the connection pool to memory. And **drained on shutdown**, so a redeploy does not
discard whatever had not yet been written.

When the queue is full the write happens inline, applying backpressure to the caller that is
causing it. That is a deliberate choice over dropping the record: a request log that silently
loses entries under load is worse than one that is occasionally slow, because the entries lost are
exactly the ones from the incident someone will later investigate.

## 5. Testing & Acceptance

- **Unit**: bucket refills over time; a burst is allowed and the next request is not; `Retry-After`
  matches the refill rate; both scopes apply and the stricter wins; an unconfigured use case is
  unlimited; Redis unavailable → allowed, and the fallback is reported.
- **Unit (budget)**: a reservation is visible to a concurrent guard; reconciliation lands on the
  actual figure; a failed dispatch releases the reservation; a cache miss seeds from Postgres;
  Redis unavailable → the Postgres path still enforces.
- **Unit (persistence)**: the response does not wait for the write; the queue drains on shutdown;
  a full queue writes inline instead of dropping.
- **Integration (live stack, real Redis)**: the limit holds across **two gateway instances**, which
  is the property no unit test can demonstrate; N concurrent requests against a budget with room
  for one do not overshoot it.
- **e2e**: the rate-limit panel sets, shows and removes a limit, and reports a refused change.
- **Acceptance** (verified against the live stack, 2026-08-05): a use case limited to burst 2
  answered 2 × 200 then 429 with `Retry-After` through the real gateway; two independent limiter
  instances sharing one Redis allowed **4** of 6 requests against a burst of 4, rather than 4
  each; 25 concurrent guards against a budget with room for one admitted **exactly one**; 20
  concurrent guards against a 1.00 cost budget with a 0.40 estimate admitted **exactly three**;
  ten released reservations left the budget untouched; and a counter seeded from Postgres refused
  a request whose budget had already been spent before Redis knew about it.
  The e2e specs for the new tab are written but **were not executed** in the authoring
  environment — the Playwright browser download is blocked there by network policy
  (`cdn.playwright.dev`, 403). They run with `make test-e2e` wherever the browser is available.

## 6. Consequences & follow-ups

- Redis becomes a runtime dependency of the gateway. It is optional in the sense that its absence
  degrades rather than breaks, but a production deployment should treat it as required.
- Existing installations are unaffected until a rate limit is configured (FR-8).
- **Follow-ups**: per-caller concurrency caps; caching pipeline config and model prices (the
  larger remaining latency win, deliberately out of scope here); using the same primitive for the
  `throttle` incident-response action in `FRD-503`.
