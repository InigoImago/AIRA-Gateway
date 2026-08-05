# ADR-0008 — Redis as the shared counter store for rate limits and budget reservations

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** Vadim Scheibe

## Context

Two problems in the gateway have the same root cause, and one planned deployment change turns
both from theoretical into certain.

**Nothing limits how fast a caller may consume.** A client in a retry loop is accepted at full
speed. Each request opens roughly six to seven separate database sessions (authentication,
pipeline config, budget check, price lookup, budget booking, log write), so a single misbehaving
caller exhausts the connection pool and every *other* use case starts waiting or failing. The same
burst also spends the shared upstream quota at Google, which is billed and throttled per
installation, not per use case. A budget says *how much*; nothing says *how fast*.

**The budget guard and the booking are two separate steps.** `guard()` reads the period's usage
and decides; `dispatch` runs; `record()` books the result. Concurrent requests therefore all read
a figure that does not yet include each other:

```
A: read 95 € of 100 € → pass → dispatch → book 0,50 €
B: read 95 €  ← A has not booked yet → pass → dispatch → book
C: read 95 €  ← neither A nor B has booked → pass → …
```

With 200 requests in flight the limit is not exceeded by rounding — it is exceeded by a multiple.
Since FRD-403 a budget is a **sum of money**, which makes this an accounting defect rather than a
cosmetic one.

**The deployment plan makes both unavoidable.** The intention is to run the gateway behind a load
balancer. In-process counters are then wrong by construction: three instances each honouring a
limit of 100/minute let 300 through, and each one is individually "correct".

So a counter is needed that is *shared across instances* and where *checking and reserving are one
indivisible operation*. The question is where it lives.

## Options considered

- **PostgreSQL (already in the stack)** — no new component, durable, transactional. But every
  request for a given use case contends on the *same row*: `UPDATE … RETURNING` serialises them,
  which is the one access pattern a row-locking MVCC database handles worst. It also pays a WAL
  write and later vacuum pressure for a counter whose useful life is sixty seconds. The hottest
  path in the system would be pointed at the component that is already the throughput ceiling.

- **In-process counters** — fastest possible, no dependency. Correct only while exactly one
  gateway process exists, which contradicts the load-balancer plan. Rejected as the primary
  mechanism, but retained as the *rate-limit* fallback (see below) and for hermetic tests.

- **Redis** — `INCR`/`EXPIRE` and Lua scripts give atomic check-and-reserve in one round trip,
  sub-millisecond, lock-free, with self-expiring keys. Costs a new infrastructure component and a
  new failure mode on the request path.

## Decision

**Add Redis** as the shared store for rate-limit buckets and budget reservations.

The decisive argument is not raw speed, it is the access pattern: these counters are
high-frequency, tiny, contended, and worthless after their window closes. Redis is built for
exactly that shape; Postgres would be asked to do it at the cost of the resource the whole gateway
depends on. The same primitive solves both problems, so one component buys both.

**Redis is a cache with atomic operations, never the system of record.** Postgres remains
authoritative for budget usage. Redis counters are seeded from Postgres on a miss and reconciled
after every request, so losing Redis costs at most the in-flight reservations, not the period's
accounting.

### What happens when Redis is unavailable

This must be decided deliberately, because a new dependency on the request path can otherwise turn
a cache outage into an outage of the product.

| Concern | Behaviour | Why |
|---|---|---|
| **Rate limiting** | **Fall back to a per-instance in-memory bucket** | Not fail-open. A Redis outage is exactly when infrastructure is already under strain, so it is the worst moment to stop bounding a runaway caller. An in-memory bucket is exact on one instance and permits N × the limit on N instances — degraded, but still bounded, and it still protects the connection pool, which is the point. |
| **Budget enforcement** | **Fall back to the Postgres read-then-book path** | Refusing traffic would make a cache outage a full outage; skipping enforcement would make it a free-money mode. Falling back degrades to exactly today's behaviour — enforcing, but racy — which is a defensible middle. |

The asymmetry is deliberate: an in-memory bucket is acceptable for a rate limit, because the
counter is ephemeral and per-window and losing it costs nothing beyond precision. It would be
wrong for a budget, whose figure is durable, shared, and about money — there, the only honest
fallback is the store that is already authoritative.

Both fallbacks log a warning and are visible in the health endpoint, so degraded operation is
observable rather than silent.

## Consequences

- **Positive**: one caller can no longer degrade every other use case; budgets stop being
  exceedable by a multiple under concurrency; the design is correct behind a load balancer from
  the start rather than needing rework when the second instance appears.
- **Positive**: the same atomic primitive covers a later per-caller concurrency cap and the
  throttle action that Phase 5 (`FRD-503`) needs for incident response, so that work does not need
  a second mechanism.
- **Negative**: a further component to run, monitor and secure. Redis has no authentication in the
  local stack; a production deployment needs a password and TLS, which is recorded in
  `docs/DEPLOYMENT.md`.
- **Negative**: two stores now hold budget figures. The reconciliation direction is fixed
  (Postgres is authoritative, Redis is seeded from it) precisely so this cannot drift into an
  ambiguity about which one is right.
- **Trade-off**: reserving a cost before it is known requires an *estimate*, so a budget may
  briefly appear more consumed than it is. The estimate is corrected the moment the response
  arrives, and a failed request releases its reservation. Erring toward over-reserving is the
  right direction for a spend limit.
- **Follow-ups**: Redis is not yet used for anything else. The pipeline config and model prices
  are read from Postgres on every request and change rarely — caching them is the larger remaining
  latency win, and is deliberately not part of this decision.
