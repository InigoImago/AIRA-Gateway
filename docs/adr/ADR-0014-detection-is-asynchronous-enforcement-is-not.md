# ADR-0014 — Detection is asynchronous; enforcement is not

- **Status:** Accepted
- **Date:** 2026-08-07
- **Deciders:** AIRA (product owner + engineering)
- **Related:** `ADR-0008` (Redis as the shared counter store), `ADR-0013` (auditable model access),
  `FRD-122` (complete audit trail), `FRD-405` (rate limiting), `FRD-500`–`FRD-504`

## Context

Phase 5 carries three of the owner's central features (PRD §1.1): **anomaly detection** (16),
**incident response** (6), and **blocking dangerous requests** beyond the injection filter (7).
They are the *evidence* half of the product — the governance half is largely built.

They pull in opposite directions.

- Detection worth having looks **across requests**: a caller whose refusal rate jumped, a use case
  whose spend tripled overnight, a credential suddenly used from a new source, a prompt that is
  unremarkable alone and one of two hundred identical ones. None of that is a property of the
  request in hand.
- Response worth having happens **before the damage**. An incident engine that can only describe
  what already went out is a report, not a control, and feature 7 says *blocking*.

§3 of `CLAUDE.md` says persistence and event emission must not block the request path. Cross-request
analysis on the hot path would violate that immediately — and would make every request pay for the
analysis of every other.

## Decision

**Detection runs asynchronously. Enforcement runs synchronously. They meet at a written decision,
never at a shared computation.**

### 1. Detection reads the audit trail, and nothing else

Evaluation is fed by the request log — the same rows `FRD-122` made complete, including refusals.
Not a second collection path.

Two consequences, both wanted. A detector cannot see anything the audit trail does not, so
"the alert says X but the report says Y" is not a state this system can reach. And detection sees
**refusals**, which is where much of the signal is: a thousand rate-limited requests is the anomaly,
and a detector fed only served traffic would be blind to exactly the caller worth noticing.

The fan-out point is the existing `RequestLogWriter` queue: already off the hot path, already
bounded, already drained on shutdown. A detector is a second consumer of a row that was going to be
written anyway.

### 2. An action is a written decision with an author and an expiry

Detection never reaches into the request path. It **writes a decision** — "this subject is blocked,
by rule R, until T" — and the pre-dispatch gate reads it.

> **Amended 2026-08-07 by `FRD-503` §4.1.** This said "from the shared counter store, seeded from
> Postgres on a miss", by analogy with `FRD-405`. Building it showed the analogy is wrong. A counter
> is written on *every* request, which is why Redis earns its place there; a suspension is written
> when something goes wrong and read on every request, which is a **cache** problem rather than a
> shared-state one. It is now a short-lived cache over Postgres — and that is also the safer failure
> mode: a decision held only in Redis disappears when Redis does, while Postgres is the database the
> gateway cannot serve a request without anyway.

Three properties are not negotiable:

- **An author.** Every block names what produced it — a rule, or a person. An automatic block with
  no author is indistinguishable from an outage, and the first thing anyone asks at 03:00 is *who
  did this*.
- **An expiry.** An automatic block that never lifts is an outage with a good reason. Rules state
  how long they block for; a human block may be indefinite, because a human can also lift it.
- **A record.** The block, the reason and the lift are audit rows in their own right. A control
  that acts without leaving evidence is the failure `ADR-0013` exists to prevent.

### 3. Recording is not enforcing, and the row says which happened

`FRD-125b` learned this the hard way: a control that wrote its finding to one store and enforced
from another was right on paper and absent in practice. So an anomaly event records **what was
detected** and **what was done about it** as two fields, and "detected, action `alert`" is a
first-class outcome rather than a failure to act.

This also makes the safe rollout possible: a new rule starts in `alert`, is watched, and is
promoted to `throttle` or `block` once its false-positive rate is known. A detection system whose
only setting is "block" is a detection system nobody switches on.

### 4. A rule is configuration, distributed like every other rule

Anomaly rules are authored in Management, distributed over Kafka to a gateway read-model, exactly
like budgets (`FRD-400`), rate limits (`FRD-405`) and pipelines (`FRD-300`). No new mechanism.

The gateway never asks Management on the request path, and an installation that has never authored
a rule behaves precisely as it does today — **absence means no detection**, never a default that
starts refusing traffic on upgrade.

### 5. The kill switch is a first-class object, not a rule with a threshold of zero

Feature 7 also wants an operator to be able to stop something *now*: a use case, a credential, a
model. That is the same decision object as an automatic block, created by a person instead of a
rule. Modelling it as a rule would mean an operator in an incident has to author a threshold, a
window and an action in order to say "stop".

## Consequences

**Good**

- Analysis cost never touches the request path; a rule that is expensive to evaluate slows nothing.
- The detector and the report cannot disagree, because they read the same rows.
- A block is a lump of data with an author, an expiry and a record — so it can be listed, explained,
  and lifted, including by somebody who was not there when it was applied.
- Alert-first rollout is the default path rather than a discipline people have to remember.

**Bad, and accepted**

- **Detection is inherently late.** Between the request that crosses a threshold and the block
  taking effect, more requests are served. This is the cost of not putting analysis on the hot
  path, and it is the right trade: the requests in that window are all *recorded*, so the harm is
  bounded and evidenced, while a slow request path harms everything. Where a limit genuinely must
  be exact and immediate, that is a rate limit or a budget — both already synchronous, both already
  atomic (`FRD-405`).
- ~~A gateway with no shared counter store falls back to per-instance blocking~~ — **no longer
  applies** after the §2 amendment. A suspension lives in Postgres, so a Redis outage does not
  reach it at all; what it costs instead is that a *lift* takes up to the cache TTL to reach every
  instance, and being slightly late to remove a restriction is the harmless direction.
- Two places can now refuse a request before dispatch. They are deliberately kept in one gate
  (`FRD-126`'s `prepare_for_dispatch`), because a second refusal path is how `:embedContent` came to
  bypass both controls once already.

## Alternatives considered

**Evaluate rules inline, on the request path.** Immediate, and exactly what §3 forbids. Every
request would pay for the history of every other, and the analysis worth having (a hundred similar
prompts, a spend curve) cannot be computed per request at all.

**Detect in a separate service reading Postgres on a schedule.** Simple, and adds a component with
its own deployment, its own failure mode and its own copy of the visibility rules. The audit writer
is already a queue in the process that has the rows; a cron job over the same table buys nothing
except latency.

**Let a detector call back into the gateway to block.** An HTTP hop where a shared store already
exists, with a fan-out problem across replicas and an ordering problem when a block and a lift
cross. The written decision has neither.
