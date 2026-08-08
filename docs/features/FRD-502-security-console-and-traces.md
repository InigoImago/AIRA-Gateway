# FRD-502 — The IT Security console, own warnings, and per-use-case traces

> Phase: 5 · Status: **Done (2026-08-08)** · Owner: AIRA · Last updated: 2026-08-08
> Related: `FRD-500`/`501`/`503` (rules, engine, response), `FRD-601` (reporting),
> `FRD-206` (the console tells the truth), `ADR-0009`, `ADR-0014`, `FRD-406` (redaction)

## 1. Summary

Phase 5 built the rules, the engine and the enforcement, and **the screens were missing** — findings,
suspensions and the kill switch reachable only over the API, which put IT Security in exactly the
position `FRD-206` was written about: a role whose console shows it nothing.

Three things, one delivery:

1. **An IT Security console** — findings across every use case, the suspensions in force, and the
   kill switch, with the global rules that produce them.
2. **Own warnings.** A use case's members see the findings *about their own use case*. A warning
   nobody who could act on it can see is a warning that changes nothing.
3. **A trace overview per use case** — what actually happened, request by request: when, which
   model, which outcome, how many tokens, what it cost, how long it took, and the trace id.

All three **update live**, without the reader reloading anything.

## 2. What this is not, and why that is now sayable

`ADR-0009` deferred "per-request browsing" until content redaction (`FRD-406`) exists, and the
reporting module says why in its own docstring: *browsing individual requests would show stored
prompts to people who are precisely not members of the use case that produced them.*

That reasoning is about **stored prompts** shown to **non-members**. The trace overview here is
neither:

- **No payloads.** Not the prompt, not the response, not a snippet of either. The columns are the
  metadata `FRD-122` records about a request, all of which the reporting screen already aggregates —
  this shows the same facts one row at a time instead of summed.
- **Scoped to who may see the use case.** The same `visible_scope` the report uses. A member sees
  their use case; an oversight role sees every one; somebody with neither gets an empty list rather
  than a refusal.

So `FRD-406` still blocks what it always blocked — showing a prompt — and does not block this. The
distinction is written down here because "we deferred per-request browsing" is the kind of sentence
that grows to cover more than it was about.

## 3. Functional requirements

### The console (IT Security)

**FR-1** — A **Security** screen, reachable by an oversight role (`it-security`, `it-steuerung`,
`global-admin`). What each may *do* there differs and the screen says so: only an **incident** role
(`it-security`, `global-admin`) may stop or restore traffic (`FRD-503` FR-6).

**FR-2** — It shows **findings** across every use case the caller may see: when, which rule, which
kind, which use case, the target, the measurement **and the numbers it was drawn from**, and what
was actually done (`alert` / `blocked` / `throttled` / `detected_not_enforced`).

**FR-3** — It shows the **suspensions**: in force, expired, and lifted — with author, reason and
expiry. Lifted ones stay: "blocked for two hours last Tuesday" is what a review asks.

**FR-4** — It offers the **kill switch**: stop a subject, a credential or a use case, with a reason
and an optional duration; and lift one. Both are `FRD-503`'s endpoints, so the authorisation is the
server's and the console only reflects it.

**FR-5** — It shows the **global rules** in force, because a rule that can block traffic anywhere is
one the person watching the findings needs to see next to them.

### Own warnings (use-case members)

**FR-6** — The use-case detail gains a **Warnings** tab listing the findings about *that* use case,
with the same numbers the console shows. Visible to anybody who may see the use case.

**FR-7** — If the use case is suspended, the tab says so first — that is the fact a member most
needs and the one that explains everything else on the screen.

**FR-8** — The tab count reflects findings in the current window, so a use case with something to
look at says so before it is opened.

### Traces (per use case)

**FR-9** — A **Traces** tab: one row per request, newest first. Columns: time, operation, model
(served, and requested when they differ), status, outcome, tokens (prompt/completion), cost,
latency, and the **trace id**.

**FR-10** — Filterable by outcome, and by "refusals only" — the shape somebody investigating
actually asks for.

**FR-11** — **No payloads, ever.** Not behind a click, not truncated, not on hover.

**FR-12** — Paged by cursor, not by offset. Rows arrive continuously; an offset page-2 under a
growing table shows some rows twice and skips others.

### Live

**FR-13** — All three views refresh on an interval **without reloading the page**, and without
losing the reader's place: no scroll jump, no filter reset, no collapse of what they were reading.

**FR-14** — Live is **visibly on**, and can be switched off. A screen that changes under somebody
who did not ask it to is a screen they stop trusting.

**FR-15** — Polling stops when the tab is hidden and when the component is destroyed. A console left
open in a background tab overnight must not be a load generator.

## 4. Behaviour and decisions

### 4.1 Polling, not a stream

Server-sent events would push. They also need a long-lived connection per open console through
whatever proxy sits in front, a reconnect story, and a second delivery path for facts that already
have one. The data changes at human speed — a finding every few minutes at worst — so an interval
poll of a normal endpoint is the smaller thing that is also easier to reason about when it breaks.

The cost is stated: a finding can be up to one interval old, on top of the detector's own interval.
Both are shown as "updated N seconds ago" rather than implied.

### 4.2 The trace list is a cursor, and the cursor is a timestamp plus an id

Rows arrive while somebody reads. Offset paging under an appending table shows duplicates and skips
rows, and neither is visible to the reader — they just get a wrong list. The cursor is
`(created_at, id)` so it is stable and total even when two rows share a millisecond.

### 4.3 Refreshing must not steal the reader's place

The list is re-rendered from a signal keyed by row id, so Angular reuses the DOM it already has. A
refresh that rebuilt the table would scroll the reader to the top every few seconds — which is the
behaviour that makes people close a live view and reload manually instead.

### 4.4 What a member may see is what the server says

The same rule as `FRD-206`: the screen renders the kill switch only when the server would accept it,
and the answer comes from the principal's roles rather than from the console's own idea of them.

## 5. Testing

- **Scope**: a member sees their own findings and traces and nobody else's; an oversight role sees
  every use case; a caller with neither gets an empty list rather than a 403.
- **No payloads**: asserted on the endpoint's *response body* — no `request_payload`,
  `response_payload`, or any key carrying one, whatever the row holds.
- **Cursor**: two pages over a table that grew between them contain no duplicate and no gap.
- **Live**: the interval fires, the DOM updates, the timer stops on destroy and when the document is
  hidden.
- **Authorisation**: the console offers the kill switch only to an incident role, and the agreement
  test (`FRD-206`) covers the reported answer against the request.
- Mutations `N24`–`N27`: the payload exclusion (add the two payload columns back to `TRACE_FIELDS`),
  the use-case scope (drop the `IN` clause), the cursor's tie-break (`<` becomes `<=`, which repeats
  a row when two share a moment), and the `limit + 1` that decides whether there is a next page.

Counts as built: **16** gateway tests (`gateway/tests/test_traces.py`), **50** frontend tests across
`live.spec.ts`, `security-page.spec.ts`, `warnings-tab.spec.ts`, `traces-tab.spec.ts` and the new
service cases. One of them earned its place before it was written: the `Live` teardown test failed
with seven ticks after destroy, because the first harness provided the service in the testing module
while every real screen provides it on the component — and `DestroyRef` resolves to whichever
injector created it. **A harness that configures a service differently from production tests a
different service**; the fix was on both sides, so the timer now stops explicitly rather than only
where it happens to be provided correctly.

## 5.1 What the browser layer found

Two defects, both invisible to 356 green frontend tests and 259 mutation properties, both caught the
first time a real person's session opened the screens.

**1. `Live` was provided nowhere.** The service is `@Injectable()` without `providedIn: 'root'` —
deliberately, because a poll that outlives the screen that started it is a poll nobody stops — and
neither tab declared it. Every unit test passed because each harness provided it. In the browser the
panels failed to construct and rendered *nothing at all*: the tab was selected, the tab body was
blank. Both tabs now declare `providers: [Live]`, and both harnesses stopped providing it, so the
next regression is caught one layer earlier. **A harness that configures a component differently
from production tests a different component** — the same lesson `live.spec.ts` had already taught
about `DestroyRef`, one layer up.

**2. An empty tab was stating the wrong reason.** The gateway derives use-case membership from
Keycloak **groups** (`FRD-102`); creating a use case in this console creates a guardian object
permission, not a group. So the administrator of a use case they had just created opened Traces,
read *"No requests match. Traffic appears here within seconds of arriving."*, and was looking at a
use case with traffic in it. That is worse than an empty state with no explanation: the reader
concludes the recording is broken, and then distrusts every figure on the page.

Both endpoints now return **`in_scope`**, and both tabs say which of the two empties this is, naming
the group somebody has to be added to. `in_scope` reports the **caller's own visibility** and
nothing else, so the reason a 403 was refused still holds — it confirms nothing about whether the
use case exists. `/v1beta/anomalies` also gained a `use_case` filter while it was there, because
the tab had been fetching the newest hundred findings and keeping the matching ones: on a busy
installation a quiet use case's findings are pushed off the end by somebody else's, and the screen
would have said "nothing has crossed a threshold" about a use case it had simply not asked about.

Mutations `N28`/`N29`; seven Playwright cases in `e2e/tests/security-console.spec.ts`, including one
that opens a trace tab **before** the traffic exists and requires the row to arrive without a
reload — the live refresh is not a property any lower layer can observe.

## 6. Open

- Alert **delivery** (mail, webhook) is still not built; the console is where a finding is seen, not
  where it is sent.
- Trace **search** by subject or credential is not built — the filters are outcome and window.
- `FRD-406` remains open, and remains the blocker for showing a payload anywhere.
