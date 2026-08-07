# FRD-500 — Anomaly rules

> Phase: 5 · Status: **In progress (2026-08-07)** · Owner: AIRA · Last updated: 2026-08-07
> Related: `ADR-0014` (detection vs. enforcement), `FRD-501` (detection engine), `FRD-503`
> (incident response), `FRD-122` (the audit trail rules are evaluated against), `FRD-405`
> (rate limits — deliberately *not* this), PRD §1.1 features 6, 7 and 16

## 1. Summary

The gateway records everything (`FRD-122`) and nobody is watching. A caller can be refused a
thousand times, a use case can triple its spend overnight, a credential can start being used from
somewhere new — and the only way to find out is for a person to open the reporting screen and
notice.

This is the **rule**: what an installation considers abnormal, said out loud, per use case or across
all of them. `FRD-501` evaluates it; `FRD-503` acts on it. Splitting them is deliberate — a rule
that cannot be authored before the engine exists is a rule nobody can review, and the engine is the
larger half.

## 2. Goals & Non-Goals

**Goals**
- An installation can state, as configuration, what counts as abnormal — without deploying code.
- A rule is reviewable before it acts: it says what it watches, over what window, above what
  threshold, and what it then does.
- **Alert first.** A rule can be switched on in a mode that only records, so its false-positive
  rate is known before it refuses anybody.
- The vocabulary of *what can be watched* is closed and small. An open-ended query language would
  be a second reporting API with none of its scoping.
- Absence means no detection. An installation that authors no rule behaves exactly as today.

**Non-Goals**
- Not a query language, not a dashboard, not statistics. A rule is a threshold over a window.
- **Not a rate limit.** `FRD-405` already refuses too-fast traffic synchronously and atomically;
  a rule that tried to do the same would be slower, later and weaker. What a rule adds is a *shape*
  a limit cannot express — a ratio, a change against the recent past, a new source.
- Not per-request content inspection. That is the pipeline (`FRD-300`), and it runs before dispatch
  where it belongs.

## 3. Functional requirements

**FR-1** — A rule has: a **scope** (one use case, or global), a **kind** (what it watches), a
**window**, a **threshold**, an **action**, and an enabled flag.

**FR-2** — The kinds are closed, and each exists because a real question was asked of it:

| Kind | Watches | The question it answers |
|---|---|---|
| `refusal_rate` | share of requests refused, over the window | is somebody probing, or is a client misconfigured and hammering a wall |
| `error_rate` | share of requests that failed upstream | is a model or a region failing for one use case and nobody noticing |
| `spend_spike` | spend in the window against the same-length window before it | did cost change shape, not merely exceed a cap |
| `request_spike` | request count in the window against the one before it | the same question where nothing is priced |
| `new_source_ip` | a credential used from an address not seen for it in the reference period | a leaked key is used from somewhere its owner never was |
| `payload_size` | share of requests above a byte threshold | bulk extraction, and the shape `FRD-405` cannot see because it counts requests |
| `blocked_prompt_rate` | share of requests the pipeline blocked | the injection filter earning its keep, or a use case under attack |

**FR-3** — A **window** is minutes, bounded (1 minute to 24 hours). A ratio kind compares the window
against the immediately preceding window of the same length; a rate kind needs a **minimum sample**
so that "one of one request was refused" is not 100 %.

**FR-4** — The **action** is one of `alert`, `throttle`, `block` (`FRD-503` defines what each does).
A rule created through the API defaults to `alert`.

**FR-5** — A `block` or `throttle` rule states **how long** it acts for. An automatic action with no
expiry is an outage with a good reason (`ADR-0014` §2).

**FR-6** — A rule states **what it acts on** when it fires: the `subject` (the caller), the
`credential` (the key or client that was used), or the whole `use_case`. Getting this wrong is
expensive in both directions — blocking a use case because one key misbehaved stops everybody, and
blocking a subject when a whole use case is under attack stops nothing.

**FR-7** — Rules are authored in Management (`GET/POST/DELETE /api/v1/anomaly-rules/`, plus the
per-use-case form under `/use-cases/{slug}/anomaly-rules/`) and distributed over
`aira.anomaly-rules` to a gateway read-model, exactly like budgets and rate limits (`FRD-204`).

**FR-8** — **Who may author what**: a use-case administrator may author rules scoped to a use case
they administer. Only IT Security and Global Administrator may author a **global** rule, because a
global rule can block traffic for use cases its author cannot see.

**FR-9** — Validation happens where the rule is written, not where it runs: a threshold outside its
kind's range, a window outside the bounds, a ratio kind with a reference period shorter than its
window, or a `block` with no duration are all refused with a message naming the field.

## 4. Behaviour and decisions

### 4.1 Why the kinds are closed

The temptation is a rule engine: a field, an operator, a value. It fails on the first review — a
rule saying `p95_latency > 900` reads fine and is unimplementable against a store that has no
percentile (`FRD-601` already ran into exactly this and said so). A closed vocabulary means every
kind is one somebody has implemented, tested and can explain, and adding one is a code change with
a test rather than a configuration line that may or may not evaluate.

The same argument as `FRD-114`'s capability flags: **undeclared means unsupported**, and a rule kind
nobody wrote is not silently accepted.

### 4.2 A ratio is not a threshold

`spend_spike` and `request_spike` compare against the preceding window rather than a fixed number,
because a fixed number is a budget and there is already one. What these catch is a *change of
shape* — a use case that has spent €4/day for a month spending €40 today is worth a look even if
its cap is €100, and no cap can express that without being lowered to the point of refusing normal
traffic.

The cost is stated rather than hidden: a ratio has no opinion about small numbers, so it carries the
same minimum sample as a rate. Doubling from one request to two is not a spike.

### 4.3 `alert` is the default, and that is a safety property

A detection system whose first setting is `block` is a detection system that blocks the wrong thing
once and is then switched off forever. A new rule records what it *would* have done, somebody reads
that for a week, and it is promoted. `ADR-0014` §3 makes the promotion possible by recording
detection and action as two separate facts.

This is deliberately the opposite default from `FRD-125`'s classifier, and for a reason that
generalises: **that** control had already been chosen, configured and displayed as active, so
failing open made it a badge without a control. A rule is a hypothesis about what abnormal looks
like until somebody has watched it be right.

### 4.4 Global rules and who may write them

A rule scoped to a use case is that use case's business. A global one — "any credential used from a
new country" — crosses every boundary the console otherwise enforces, and its effects land on use
cases the author may not be able to see. That is IT Security's job description (PRD §154), so it is
IT Security's to author, and the API says so rather than the UI.

## 5. Testing

- Every kind round-trips: authored, validated, distributed, present in the gateway read-model.
- The validation of FR-9 is tested **through the API**, not against the serializer alone — the
  lesson from `FRD-124`'s route-wiring gap, which coverage cannot see.
- Authorisation: a use-case administrator is refused a global rule; IT Security is granted one; and
  the answer the object reports matches what the request does (`FRD-206`'s agreement test, applied
  to a second surface).
- Deleting a use case removes its rules and tombstones them in the read-model — the mistake
  `FRD-205` made once with keys.
- Mutation entries for: the default action being `alert`, the minimum-sample guard, and the global
  authorisation.

## 6. Open

- Alert *delivery* (mail, webhook, Kafka topic for an external system) is `FRD-503`, not here. A
  rule that fires records an event; who gets told is a separate decision with a separate blast
  radius.
- `new_source_ip` needs a reference period longer than the window, and its first evaluation after
  deployment sees every address as new. `FRD-501` handles the warm-up; the rule only declares it.
