# FRD-501 — The detection engine

> Phase: 5 · Status: **Done** · Owner: AIRA
> Related: `ADR-0014` (detection vs. enforcement), `FRD-500` (the rules), `FRD-503` (what is done
> about a finding), `FRD-122` (the rows it reads), `FRD-405` (the counter store it will write to)

## 1. Summary

`FRD-500` lets an installation state what abnormal looks like. This evaluates it.

The engine reads the **request log** — the same rows `FRD-601` reports from and `FRD-122` made
complete — decides whether a rule's threshold is crossed, and writes an **anomaly event**: what was
detected, over what window, about whom, and what was done. It runs entirely off the request path
(`ADR-0014`).

## 2. Goals & Non-Goals

**Goals**
- Every rule kind in `FRD-500` is evaluated. A kind that is declared and not evaluated is worse
  than one that does not exist, because the console shows it as active.
- Evaluation costs a request nothing. No query, no lock, no counter on the hot path.
- An event says **what was measured**, not just that something fired: the value, the threshold, the
  sample it was drawn from. A finding nobody can check is a finding nobody will act on.
- Nothing is evaluated for a scope where nothing happened. A quiet installation does no work.
- The same finding does not fire every minute for an hour.

**Non-Goals**
- No enforcement here. Blocking and throttling are `FRD-503`; this stage detects and records.
- No alert *delivery*. An event is a row; who gets told is `FRD-503`, with its own blast radius.
- No statistics. A threshold over a window, compared with the window before it. Not a model.

## 3. Functional requirements

**FR-1** — A background task evaluates rules on an interval (`AIRA_ANOMALY_INTERVAL_SECONDS`,
default 60). It is started with the gateway and stopped with it.

**FR-2** — Only **touched** scopes are evaluated. The request-log writer marks the use case,
subject and credential of every row it persists; the evaluator takes that set and clears it. An
installation serving no traffic performs no queries.

**FR-3** — A rule is evaluated at most once per **cooldown**, which is its own window. A rule with a
15-minute window that fires does not fire again on the next tick for the same target — the second
finding would be the same requests counted twice.

**FR-4** — Each kind is evaluated as follows, always grouped by the rule's `target` and filtered to
its scope:

| Kind | Measured |
|---|---|
| `refusal_rate` | rows whose outcome is not `served`, as a percentage of rows in the window |
| `error_rate` | rows whose outcome is `upstream_error`, likewise |
| `blocked_prompt_rate` | rows whose outcome is `blocked_by_pipeline`, likewise |
| `payload_size` | rows whose request body is at least `parameter` bytes, likewise |
| `spend_spike` | spend in the window as a percentage of the preceding window of the same length |
| `request_spike` | request count, likewise |
| `new_source_ip` | source addresses in the window not seen for that target in the reference period |

**FR-5** — A rate or ratio with fewer than `min_sample` rows in the window is **not evaluated**, and
that is recorded as nothing rather than as a pass. One refusal out of one request is 100 %.

**FR-6** — A ratio against an **empty** preceding window does not fire. Growth from nothing is not a
multiple of anything, and treating it as infinite makes every use case's first hour an incident.

**FR-7** — An event records: the rule, the target and its value, what was measured, the threshold,
the sample size, the window, and the action taken. Actions beyond `alert` arrive with `FRD-503`;
until then a rule configured for one records that it was **detected and not enforced**, in those
words. A control that is displayed as active and does nothing is the defect `FRD-125` exists to
prevent, and stating it is the minimum honest interim.

**FR-8** — Events are readable through the gateway at `GET /v1beta/anomalies`, scoped by the same
`visible_scope` function reporting uses (`FRD-601`) — one definition of who may see what, because a
second entry point is a second chance to forget it.

## 4. Behaviour and decisions

### 4.1 A dirty set, not a scan and not a per-request hook

Two obvious designs are both wrong. Evaluating on every persisted row means N queries per request,
off the hot path but not off the *machine*. Scanning every rule on a timer means a quiet
installation with 200 use cases runs 200 pointless queries a minute, forever.

The writer already touches every row; marking three strings costs nothing and turns the timer into
"evaluate what changed". The set is bounded and dropped on overflow — losing a *hint* delays a
finding by one tick, and a bounded loss beats unbounded memory in the component whose whole job is
to still be running when something goes wrong.

### 4.2 The cooldown is the window

A 15-minute window evaluated every minute would fire fifteen times about the same fifteen minutes.
Suppressing by rule and target for the length of the window makes each finding describe a distinct
stretch of traffic. The consequence is stated rather than hidden: a condition that clears and
returns within the window is reported once.

### 4.3 What a refusal is, and why the engine does not decide

`refusal_rate` counts outcomes that are not `served`, using the closed vocabulary in
`aira_gateway.audit.Outcome`. Not a list of "bad" outcomes maintained here — `FRD-122` already made
that enum the one place a control's existence is recorded, and a second list would go stale the
first time a control was added.

`client_gone` is deliberately included. A caller hanging up is not our failure, but a *thousand*
callers hanging up is exactly the kind of shape a detection system exists to surface.

### 4.4 A kind that needed two numbers was declared with one

Found while implementing `payload_size`: `FRD-500` describes it as "the share of requests above a
byte threshold" and the rule carries **one** threshold, which is the share. The byte figure had
nowhere to live.

The fix is a nullable `parameter` on the rule — "the kind's second number, when it needs one" —
required for `payload_size` and refused for every other kind, so it cannot quietly become a second
free-form field. Not a rule engine: still no operators, still a closed set of kinds, and a kind
that wants a third number is a code change with a test.

Worth recording because of *how* it was found. Stage A's rule model, its serializer, its API, 18
tests and six mutations were all green, and the gap was invisible to every one of them — because
they tested that the rule round-trips, and nothing had yet tried to *evaluate* it. A configuration
schema is only proved by the code that consumes it.

### 4.5 Where the byte count comes from

`request_logs` had no size. The body-size middleware (`FRD-122` §12) already counts bytes to
enforce the ceiling, so it now records what it counted, and the audit row carries `request_bytes`.
Rows without it — anything written before this migration — are excluded from both the numerator and
the **denominator**, so an old row cannot look like a small one.

## 5. Testing

- Each kind: one case that fires and one that does not, against rows inserted with known outcomes.
- FR-5 and FR-6 explicitly: a 100 % refusal rate over two requests does not fire, and a first-ever
  window does not fire as an infinite spike.
- The cooldown: the same condition evaluated twice in a row produces one event.
- The dirty set: with no traffic, a tick issues no queries; with traffic in one use case, a rule
  scoped to another is not evaluated.
- Scope: a use-case rule ignores other use cases; a global rule sees them all.
- `GET /v1beta/anomalies` honours `visible_scope` — driven through the route, not the function,
  which is the gap `FRD-124` and `FRD-602` both left the first time.
- Mutations for: the sample floor, the empty-previous-window guard, the cooldown, and the
  `parameter` requirement.

## 6. Open

- Enforcement (`FRD-503`). Until it lands, a `block` rule detects and says it did not enforce.
- `new_source_ip` sees every address as new on its first evaluation after deployment. The reference
  period is the window before the window, so the warm-up is one window long and self-clearing; a
  longer memory is a later question.
