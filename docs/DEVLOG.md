# AIRA Gateway — Development Log

A running, dated log of meaningful changes and decisions. Newest entries on top.
Keep entries short; link to ADRs/FRDs/commits for detail.

---

## 2026-08-08 — access follows the group, and three things that carried nothing (`FRD-209`)

Access to a use case was granted **one person at a time**, by username. Two things were wrong with
that, and they were the same thing from two sides: it does not survive somebody joining or leaving a
department, and there were already **two answers** to "is this person a member" that disagreed — the
gateway read Keycloak groups `/use-cases/<slug>`, Management read its own rows, and a use case
created in the console produced only the second. That is exactly the defect `FRD-208`'s round
surfaced, where an administrator opened their own use case's Traces tab and was told, correctly,
that the identity provider did not consider them a member.

**A grant now binds a principal to a use case, and a principal is a group or a person.** A group is
whatever path the realm actually uses — `/abteilungen/vertrieb/nord` — with a role (`user` or
`admin`). AIRA reads the directory and never writes to it: who is in the group stays the identity
provider's answer, which is the entire point.

The mechanism is the part worth keeping: **`django-guardian` assigns object permissions to a user
*or a Django group*.** So a group grant assigns them to a Django group mirroring the Keycloak path,
and every authenticated request syncs the caller's group paths onto their Django groups — exactly as
`FRD-201` already does for roles. `scope_queryset`, `may_admin` and `may_manage` then needed **no
change at all**. A second permission path beside guardian's would have been a second chance to
forget one, which is the mistake the two planes had already made about membership.

Two rules written into `aira_common.access` so neither plane can restate them differently: the two
routes are a **union** (being a member twice over is being a member) and where roles differ **the
stronger wins** — an access decision that depended on which row was read first is not a decision
anybody can review. And degradation refuses: if the read-model cannot be read, the naming convention
still resolves from the token alone, and somebody who was a member *only* by grant is refused.

The console gets **one** picker for both kinds, because the question is "who should get this", not
"am I about to name a group or a person". Without an admin client it falls back to what Management
already knows and **says so** — "no results" from a directory nobody could reach reads exactly like
"no such group".

**Then the live round found three defects, all of the same family: a correct half with nothing
carrying it to the other side.**

1. **An event with no topic — the third instance of this shape here.** The first grant was written,
   listed and shown in the console, and reached the gateway *never*. `record_to_outbox` matches
   against a hand-written map and **returns silently** for anything unknown — deliberate, so an
   older Management does not crash on a newer event, and precisely what made the missing entry
   invisible. `aira.rate-limits` and `aira.anomaly-rules` were both previously topics created by
   nothing. There is now a test that parses every `emit(...)` in the source and compares it against
   the map **in both directions** — and the reverse half immediately found **`pipeline.deleted`: a
   topic with no emitter**, dead configuration that reads as a working path.
2. **A compacted topic needs a key per grant.** Two grants on one use case keyed by the slug alone
   meant the second erased the first from the log, so a gateway rebuilding its read-model would
   silently lose access somebody holds. The key is `slug|group_path` now.
3. **A token with no `groups` claim grants nothing.** The mapper was on the SPA client and none of
   the service accounts, so their tokens carried no groups at all. A configuration requirement of
   the feature rather than a bug in it — now in `INTEGRATIONS.md`, in the dev realm, and asserted
   live.

A fourth came from refusing to leave an assertion weak: a grant on the bare realm root `/` was
accepted, and can **never** match — every path a token reports begins with a name — so it was
permanently inert while reading to a person as "the whole realm". Refused now.

Counts: 36 shared-library, 38 Management, 14 gateway and 21 console tests; **85 live cases** in the
`FRD-129` style; 7 Playwright cases; mutations `N30`–`N39` (271 properties).

---

## 2026-08-08 — paging that is real, and a rule somebody can change (`FRD-208`)

Asked directly whether the search and paging `FRD-207` added were real or client-side, the honest
answer was **client-side**. The useful follow-up is where that matters, and it is one of the three
lists.

**The use-case list.** Unbounded — a live round found 801 — and its serializer answers
`can_admin`/`can_manage`/`is_member` **per row** (`access.py`). Slicing that in the browser leaves
every one of those computations happening on every load: the reader waits exactly as long and then
sees twenty-five rows. Now `?page=`/`?q=` at the server, ordered explicitly (an unordered queryset
may hand the same row back twice and never show a third). Measured after: **1.6 s, 211 use cases
across 9 pages**.

**Findings** are paged too, by **cursor** rather than page number — the same choice the trace view
made and for the same reason: an append-only log, so a detector firing while somebody reads page two
pushes rows across the boundary and they see one twice and miss another, invisibly.

**The catalog stays client-side, and that is now written into the viewset.** It is bounded by how
many models an organisation contracts, and two of the console's warnings count over the *whole*
catalog — paging would turn "N models have no price" into "N on this page", a figure that means
nothing. Report breakdowns likewise: one aggregate response, already computed.

Three behaviours a server-paged list has to have, each a way it goes wrong: typing does not fire a
request per keystroke (250 ms, and identical queries are not re-sent — nine letters would otherwise
be nine round trips against the slowest endpoint here); a new search starts at page one (otherwise
it asks for page 4 of a two-page result and gets nothing, which reads as "no matches"); and a late
answer never overwrites a newer one (a slow "a" must not land after a fast "abc").

**The bigger finding: the console pointed at a screen that did not exist.** `FRD-207` had the
security console say a use-case rule *"is changed on that use case"* — and there was no such
screen. That is the `FRD-206` defect one level of indirection further out: not a button that
answers 403, but an instruction with no destination. The server had allowed it all along
(`AnomalyRuleViewSet._guard`, `upsert_use_case_rule`); only the screen was missing. There is now a
**Rules** tab on the use-case detail — list, create, edit, delete, each rule described in a
sentence — and the console's sentence became a link to it. Global rules are deliberately absent
from it: they are not that use case's to change.

**One form, two screens.** `rule-form.ts` is thirteen fields with a per-kind validation contract; a
second copy is how one screen quietly loses the field the other gained. It refuses to edit a rule's
**kind** (the kind decides what the threshold *means* — 50 is half the requests under one and half a
multiple under another) and its **name** (the server upserts by name, so a rename would create a
second rule and leave the first watching).

And the layout defect reported alongside: `.actions` carries `flex-wrap: wrap`, right in a form row
and wrong in a table cell, so Edit and Remove wrapped onto two lines. Same cell as `FRD-207` §2.3,
a second way of leaving the row.

Test note: the search is asserted by **watching for the request carrying `q=`**, not by checking
which rows are on screen — the second passes for a client-side filter too, which is how this pass
would have "proved" the thing it was correcting.

---

## 2026-08-08 — the console holds still, and says what its controls do (`FRD-207`)

`FRD-206` made the console stop promising what the server refuses. A walkthrough of the running
console asked the next question — *can I actually read this?* — and produced twelve findings. Two
of them turned out to be defects rather than polish, and both are the same shape.

**The jiggle was one element, and it was measurable.** A `PerformanceObserver` on `layout-shift`
reported **five shifts in forty seconds on the security console, every one of them the Refresh
button**: the stamp beside it changes width — "updating…" against "updated 12s ago", "9s" against
"10s" — twice a tick. That is why it was hard to name: a few pixels, nothing appears to *happen*,
and the reader is left with an impression rather than an observation. The stamp now reserves its
widest form with tabular figures, and "refreshing" is a dot that fades in space it already
occupies. The observer reports nothing at all now.

**The navigation marker was never applied.** `app.html` carried `routerLinkActive="is-active"` on
every item and `app.ts` did not import `RouterLinkActive`. Angular does not complain about an
attribute matching no directive — it is inert markup — so the class was never set and the
`.is-active` rule had been styling nothing for as long as the shell has existed. **The same shape
as `FRD-502`'s `Live` two days earlier: a declaration that is silently inert**, invisible to every
unit test and obvious in a browser.

Also a real layout defect: `.table__actions` was `display: flex` **on the `<td>`**. A cell made a
flex container stops participating in its row — it leaves the row's height and baseline — which is
exactly the "break between a model row and its own Edit/Delete buttons" that was reported. And the
trace filter row was `align-items: center`, so a bare checkbox beside a field with a label above it
sat on a different line from the control it is read as a pair with; a filter row aligns on the
**bottom**, where the controls are. Both measured afterwards rather than eyeballed.

**A finding opens, and a rule says what it does.** Six columns is as much as a table can be read
at, so the other six fields go under the row. `rule-language.ts` turns a rule into English, which
is safe to write only because the vocabulary is **closed**: seven kinds, one meaning each. It keeps
two things honest — **a ratio is not a threshold** (`spend_spike` at 300 is "three times the window
before", not 300 euros; `FRD-500` chose a ratio because a fixed number is a budget and there
already is one), and **`alert` is not enforcement** (`ADR-0014`), with `detected_not_enforced`
saying in words that the block was asked for, not applied, and the traffic continued.

Rules can now be **edited** — threshold, window, sample, action and its duration or rate, and
whether it watches at all. **Not the kind and not the name**: a kind decides what a threshold
*means*, so changing it in place would silently reinterpret a number somebody chose deliberately.
Authority follows `FRD-206`: a global rule to an incident role, and a use-case rule named as
belonging to its use case rather than guessed at, because object-level permission is not in the
token. The kill switch explains its reach on hover — one caller, one key, one use case, and
**no switch for the installation**, which is a deliberate absence. And the line reading *kept,
because "blocked for two hours last Tuesday" is what a review asks* — a note to the author sitting
where a sentence for the reader belongs — was rewritten.

**Reporting shows one table at a time.** Four stacked breakdowns made the page long enough that its
own export control scrolled out of sight, and left two ideas of "which table": one for the screen
and one for the file. The selector governs both now. `by_outcome` is shown and not exported, and
says so — the CSV renderer takes three breakdowns (`FRD-602`), and a button that looks ready and
answers 400 is the defect `FRD-206` was about. The token, spend and latency columns carry their
definitions, including *why* prompt and completion are shown apart: they are priced apart.

**Search and paging** on the breakdowns, the catalog and the use-case overview, extracted into
`core/ui/table-view`. A live round found **801** use cases in one installation, which made the
overview useless without a line of it being wrong. Two rules encoded: searching returns to page one
(a filter applied on page 4 that leaves you there shows an empty table), and the reader is always
told what they are not seeing — the pager renders even on a single page, because a control that
appears only once a list grows teaches nobody it exists.

The explanation hint was **written twice in a week** and the second copy promptly collided with the
first on a `data-testid`; it is now `core/ui/info-hint`, one pinned at a time page-wide, since the
panels are overlays and two open cover each other.

One test lesson: the first e2e for the rule editor **skipped itself** when the installation had no
rules — so the part of this pass with the most behaviour in it would have been exercised in a
browser exactly never. It creates its own rule now. *A test that skips when the data is
inconvenient reports green about nothing.*

Also noted, not fixed: `/api/v1/use-cases/` computes object-level permissions per row, so hundreds
of use cases take many seconds to answer. Search and paging make that survivable, not fast.

---

## 2026-08-08 — a console for the evidence, and what actually happened per request (`FRD-502`)

Phase 5 built rules (`FRD-500`), an engine (`FRD-501`) and enforcement (`FRD-503`), and none of it
had a screen. That put IT Security in exactly the position `FRD-206` was written about: a role whose
console shows it nothing. Two screens close it.

**The IT Security console** (`/security`) — findings with the numbers they were drawn from, what is
stopped right now, and the rules that produced them, all three in one place because a finding read
without its rule is a number without a claim, and an empty findings list means nothing until the
page says whether anything is being watched at all. It keeps **two permissions apart**, which is the
mistake this project has already made once: *seeing* every use case is an oversight role, *stopping*
traffic is an incident role. `it-steuerung` gets the whole view and no kill switch, and the page
names who does — an action nobody can carry out is worse than an absent one.

**Warnings, per use case** — the same findings, scoped, for the people who could actually fix the
cause. A warning only IT Security can see is a warning nobody who could change the prompt, the limit
or the client ever reads. The tab leads with "this use case is stopped", because without it a wall
of 429s reads as a broken gateway.

**Traces** — `GET /v1beta/traces`, and a tab per use case: every request, newest first, with who,
which model, how it ended, what it cost. **Metadata only, never a payload** — and that is *not* the
per-request browsing `ADR-0009` deferred: that reasoning is about showing stored prompts to
non-members, and this shows neither prompts nor anything to a non-member. `FRD-406` still blocks
what it always blocked. The field list is an **allow-list**, so a column added to `request_logs`
tomorrow cannot appear here because somebody forgot to exclude it, and the two that must never
appear are exactly the ones a forgotten exclusion would leak.

Three decisions worth keeping:

- **Cursor paging, not offset.** Rows arrive while somebody reads; under an appending table an
  offset page shows some rows twice and skips others, *invisibly* — the reader simply gets a wrong
  list. The cursor is `(created_at, id)`, because two rows can share a millisecond and a timestamp
  alone would either repeat one or lose one. Written out rather than as a row comparison: SQLite has
  no tuple comparison, and paging exercised against only one of the two stores is paging tested on
  one of the two.
- **Live by polling, and visibly so.** `core/ui/live.ts` is one primitive with three guarantees, each
  a way live views go wrong: it **stops** (on destroy, and while the tab is hidden — a console left
  open overnight must not be a load generator), it is **visible** (the reader sees "updated 12s ago"
  and can switch it off, because a screen that changes under somebody who did not ask it to is a
  screen they stop trusting), and it **never stacks** (a tick during a slow response is skipped, not
  queued, or a refresh interval becomes a load test against the endpoint already struggling).
  Server-sent events would push, and would also need a long-lived connection per open console
  through whatever proxy sits in front, a reconnect story, and a second delivery path for facts that
  already have one.
- **Scope resolved once, at the edge.** Traces reuse `visible_scope` — the same function the report
  and the CSV export use. `FRD-602`'s assertion (each endpoint resolves the scope exactly once)
  caught the new endpoint immediately.

The test that earned its place before it was written: `Live`'s teardown case failed with **seven
ticks after destroy**. The harness had provided the service in the testing module while every real
screen provides it on the component, and `DestroyRef` resolves to whichever injector created it — an
environment-level one outlives every component. **A harness that configures a service differently
from production tests a different service.** Fixed on both sides: the harness mirrors production,
and the timer now stops explicitly rather than only where it happens to be provided correctly.

Mutations `N24`–`N29` cover the payload exclusion, the scope, the cursor tie-break, the `limit + 1`
that decides whether a next page exists, and the two-kinds-of-empty distinction below
(**261 properties**). 21 gateway tests, 56 new frontend tests, 7 live integration cases, 7
Playwright cases; the frontend gate stays where it was.

**Then the browser found two more**, both invisible to 356 green frontend tests. `Live` is
`@Injectable()` without `providedIn: 'root'` — deliberately, so a poll cannot outlive its screen —
and **neither tab declared it**, so in production both panels failed to construct and rendered
nothing at all while every unit test passed on a harness that provided it. And an empty tab was
**stating the wrong reason**: the gateway reads use-case membership from Keycloak *groups*
(`FRD-102`), which creating a use case in this console does not create, so its own administrator
read "no requests match" about a use case with traffic in it. Both endpoints now return `in_scope`
and both tabs name the group somebody has to be added to — `in_scope` describes the **caller's own
visibility** and nothing else, so it confirms nothing about whether a use case exists and the reason
a 403 was refused still holds. `/v1beta/anomalies` gained a `use_case` filter in passing, because
the tab had been keeping the matching findings out of the newest hundred — on a busy installation
that is how a quiet use case comes to be told nothing crossed a threshold.

**And a flake that had been there all along.** `make mutants` refused to run — red baseline on
`test_log_writer.py`. `test_a_full_queue_writes_inline_instead_of_dropping` fails about one run in
five with *"cannot operate on a closed database"*, which reads as a defect in the writer and is not
one: in-memory SQLite behind a `StaticPool` hands every session the **same** connection, and that
test's whole subject is an inline write happening *while* the worker writes. The module docstring
had warned about it in prose; the test then did it anyway. It now runs on a file-backed database,
where each session gets its own connection as it does on Postgres, so the overlap the test is about
is legal. Asserting the invariant without the overlap would have been testing something else and
calling it this.

---

## 2026-08-07 — documentation, and a licence

A reader arriving at this repository had a 96-line README, a `DEPLOYMENT.md` and forty ADRs and
FRDs. That is a lot of *why* and very little *what*. Six documents now sit between them, each with
one job, linked from a README that is a hub rather than a wall:

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — C4 at three levels, in Mermaid. Context (who uses it, what
  it reaches), containers (six processes, what each is), and components inside each plane.
- [`REQUEST-LIFECYCLE.md`](REQUEST-LIFECYCLE.md) — one request end to end: middleware,
  authentication, the seven pre-dispatch steps **in order and why that order**, dispatch with its
  conditions, the single accounting exit, and what happens asynchronously afterwards.
- [`SETUP.md`](SETUP.md) — the four ways to run it: demo, standalone, development from source, and
  integrated onto existing infrastructure.
- [`CONFIGURATION.md`](CONFIGURATION.md) — every `AIRA_*` variable with its **real default**, read
  out of the code rather than remembered, plus what degrades when each piece is missing and what
  refuses to boot.
- [`INTEGRATIONS.md`](INTEGRATIONS.md) — one section per connected system: what Postgres, Keycloak,
  Kafka, Redis, each model platform, the collector and the proxy must provide, which credentials,
  which settings on *their* side, and a checklist each.
- [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md) — requirements against what is built. **Described, not
  fixed**, at the owner's request.

Also: **Apache 2.0**, with a `NOTICE` that names the third-party systems this software connects to
and disclaims affiliation. The licence text was written out rather than fetched — the sandbox denies
`apache.org`, and a licence file assembled from memory is one worth saying was assembled from
memory, so it was checked against the canonical structure clause by clause.

Two accuracy notes, because documentation drifts the same way code does. Every default in the
configuration reference was **dumped from the settings classes**, not typed; every relative link is
checked by a script; and two claims were wrong on first writing and corrected against the source —
the traffic target is `showcase-traffic`, and the published ports are overridable
(`AIRA_GATEWAY_PORT` and friends). The README's status line said "Phases 0–5 delivered", which is
not true while `FRD-502` is missing; it now says so.

**What the gap analysis found** is worth having in this log rather than only in that file. Against
PRD §1.1: nine features built, six partial, two missing — and the partials are breadth rather than
correctness. The two that matter most are both about *evidence being usable*: `FRD-406` (redaction)
is the only open item that blocks two others — per-request browsing and the IT Security console's
scoped payload view — and it is the only place the product currently makes a promise it does not
keep, since payloads are stored and nothing masks anything inside them. `FRD-502` is the one that
turns work already done into work somebody can use: Phase 5 built the rules, the engine and the
enforcement, and the role whose job that is has no screen for any of it.

---

## 2026-08-07 — 84 live cases against the anomaly work, and five defects

`FRD-500`/`501`/`503` shipped with hermetic tests, 251 mutation properties and a green gate. A
developer round against the running stack — real Postgres, real gateway process, real model, both
planes talking over Kafka — found **five defects that none of those could see**. Three of them
predate this week.

**Two planes, one question, two answers.** The gateway guarded its kill switch with `has_oversight`,
which is a *visibility* predicate, so `it-steuerung` could stop traffic there while Management
correctly refused it a global rule. PRD §154 gives that role every figure and **no write anywhere**.
Reusing "may see every use case" for "may stop every use case" is `FRD-206`'s mistake one level
down — and the way it surfaced is worth keeping: **asking both planes the same question and
comparing the answers**. `INCIDENT_ROLES` now lives in `aira_common.roles` and both read it.

**A whole rule kind measured a column nothing wrote.** `payload_size` compares against
`request_bytes`; the middleware counted the bytes, the column existed, and nothing carried the
number between them. It could never have fired on real traffic. The hermetic tests seeded the column
directly and were green — the third time this repository has recorded *two correct halves and no
wire*, and the second time coverage was blind to it.

**A refused request was counted as unpriced traffic.** The console reported **105** unpriced
requests where **5** had run on an unpriced model. A refusal has a NULL cost for the opposite reason
to an unpriced one — nothing was spent because nothing *ran* — and counting both made the "spend is
a lower bound" caveat permanent, which by this project's own test (`a fully priced period carries no
caveat`) is a warning nobody reads. The rule stated in the direction it was missing: **unknown is
not zero, and zero is not unknown.** A NULL *outcome* still counts, because that is a row from
before `FRD-122`, when only served requests were logged at all — fixing a present figure must not
quietly change a historical one.

**`aira.anomaly-rules` was created by nothing.** Rules were authored, Management answered 201, the
relay published, and the broker dropped every message. The only evidence anywhere was
`Topic ... not found in cluster metadata`, repeated forever in a container nobody watches. This is
the **second** time — `FRD-405` shipped `aira.rate-limits` the same way and the DEVLOG says so — and
the topic list is written by hand in three places while the names have one source of truth. The fix
is therefore not a fourth copy but a check: `tools/tests/test_kafka_topics_are_created.py` compares
the Makefile, the Compose step and `DEPLOYMENT.md` against the constants, **in both directions**, so
a topic nothing publishes to is caught as well.

**Thirty-eight mutation ids named more than one property.** Found by reusing `N3`, which already
existed. Every entry runs regardless, so the checking was sound — but "N3 survived" named two
unrelated things, and a summary that sends somebody to the wrong line is worse than no summary. The
*later* duplicate of each pair was renamed and the first kept, because `CLAUDE.md` and the DEVLOG
cite ids by name and renaming a cited one breaks the prose explaining why the property exists. The
harness now refuses duplicates.

Two test lessons from writing the round itself, both about **measuring from the wrong moment**: a
suspension takes up to the cache TTL to reach the gateway, so "a blocked caller consumes no budget"
and "a blocked caller pays for no classifier" both failed until they counted from *after* the block
took effect rather than from zero — the requests served while the cache caught up were served
perfectly correctly. And the round needed a third Keycloak service account (`it-security`), because
neither existing one may act in an incident — which is the same distinction defect 1 was about,
arriving from the test side before the fix did.

---

## 2026-08-07 — Phase 5, stage C: a finding becomes a control

`FRD-503`. `FRD-501` detected and recorded; a rule set to `block` wrote `detected_not_enforced` on
the row, in those words, because saying so was the only honest interim. This carries it out.

A **suspension** is the written decision `ADR-0014` promised: a target, an action, an expiry, an
**author** and a **reason**. The last three are what make it a decision rather than a side effect —
the first thing anyone asks at 03:00 is who did this. The pre-dispatch gate reads it, so a stopped
caller is refused at the one place every verb passes (`FRD-126`) and does not pay for a classifier
on the way to being told. Rows are kept after they are lifted: "this caller was blocked for two
hours last Tuesday" is exactly what an incident review asks.

**An amendment to `ADR-0014`, from building it.** The ADR said the gate would read decisions from
the shared counter store, seeded from Postgres — by analogy with `FRD-405`. The analogy is wrong. A
counter is written on *every* request, which is what earns Redis its place; a suspension is written
when something goes wrong and read on every request, which is a **cache** problem, not a
shared-state one. A five-second cache over Postgres does it with one query per instance and no
second system — and survives a Redis outage, which for a control that *stops* traffic is the
direction that matters. The cost is stated: a lift takes up to the TTL to reach every instance, and
being slightly late to *remove* a restriction is the harmless direction.

Three smaller decisions:

- **429, not 403.** The credential is valid and the membership is real; the caller is stopped
  temporarily, and "come back later" is what 429 means. A 403 sends a client off to fix permissions
  it has no problem with.
- **`suspended` is its own audit outcome.** Folding it into `rate_limited` would hide "we stopped
  this caller on purpose" inside "this caller is going too fast", and those want different answers.
- **The kill switch does not go through Kafka.** Every other piece of configuration is authored in
  Management and distributed; this one is created directly against the gateway by an oversight role.
  An incident control that depends on the event bus fails exactly when the bus is the problem, and
  "traffic is doing something alarming" and "the pipeline between the planes is unhealthy" are not
  independent events.

**A pattern worth naming, because it has now happened twice.** `throttle` was declared as an action
and given no rate — the same shape as `FRD-501`'s missing byte figure, found the same way, by
building the consumer. **An enum member is not a specification.** Adding a value to an action or a
kind should prompt "what does this one need that the others do not", and the answer belongs in the
schema before anything ships.

**Two things the existing suite caught, both worth more than the code they rejected.** The
architecture assertion widened yesterday — "each endpoint in the reporting module resolves the
visible scope exactly once" — went red on the new suspension endpoints, which resolve it **zero**
times. Correctly: they are bounded by *role*, not by use case. Two different ways of being safe do
not belong behind one heading, so they moved to `api/incidents.py`. And the mutation harness caught
`N19` surviving: every endpoint test in the new file ran with authentication switched off, which
takes the demo-principal path and returns *before* the role check — so the check itself was
untested while five tests around it passed. It is now driven with a real principal.

Also: three mutations came back stale because this change edited the lines they pointed at, and one
(`N15`, "a lifted suspension stops refusing people") **survived correctly** — the load query already
filters lifted rows, so the in-memory check is the second of two guards. Removed as a mutation and
kept as code, on the `X3` precedent: a property guarded twice cannot be a mutation, and that is not
a reason to remove a guard.

25 tests, migration `0017`, five new mutations. `make ci` green.

---

## 2026-08-07 — Phase 5, stage B: the engine that reads the rules

`FRD-501`. `FRD-500` let an installation say what abnormal looks like; this measures it. All seven
kinds evaluate, against the request log — the same rows `FRD-601` reports from, so a detector cannot
see anything the report cannot.

The scheduling is the part with the engineering in it. Two obvious designs are both wrong:
evaluating on every persisted row is N queries per request — off the hot path but not off the
*machine* — and scanning every rule on a timer means a quiet installation with 200 use cases runs
200 pointless queries a minute forever. So the writer, which touches every row anyway, **marks
which scopes saw traffic**, and the timer evaluates only those. A quiet installation does no work.
The set is bounded and dropped on overflow: losing a *hint* delays a finding by one tick, and a
bounded loss beats unbounded memory in the component whose whole job is to still be running when
something goes wrong.

The cooldown is the window itself. A 15-minute window evaluated every minute would fire fifteen
times about the same fifteen minutes.

**A gap in stage A, found by building the thing that consumes it.** `payload_size` is "the share of
requests above a byte threshold" and the rule carried **one** threshold — the share. The byte figure
had nowhere to live. Stage A's model, serializer, API, 18 tests and six mutations were all green,
and every one of them was blind to it, because they tested that a rule *round-trips* and nothing had
yet tried to *evaluate* one. **A configuration schema is only proved by the code that consumes it.**
The fix is a nullable `parameter` — required where a kind needs it, refused everywhere else, so it
cannot quietly become a second free-form field. And the byte count itself had nowhere to come from,
so the body-size middleware now records what it was already counting to enforce the ceiling.

Three measurement decisions that are easy to get wrong and expensive to get wrong quietly:

- **A rate over too few rows is not evaluated.** One refusal out of one request is 100 %.
- **Growth from nothing is not a spike.** Treating an empty previous window as infinite growth would
  make every use case's first hour an incident, and the alert that fires on arrival is the one
  people switch off before it ever says anything true.
- **A request whose size is unknown is excluded from both sides of the share** — numerator *and*
  denominator. Counting an unknown as small would make old traffic look innocent.

`refusal_rate` counts everything that is not `served`, straight from `Outcome` rather than from a
second list of "bad" outcomes — `FRD-122` already made that enum the one place a control's existence
is recorded, and a copy here would go stale the first time somebody added a control. `client_gone`
is deliberately in: one caller hanging up is not our failure, a thousand is exactly the shape a
detector exists to surface.

Until `FRD-503` lands, a rule configured to block **detects and records that it did not enforce**,
in those words on the row. A control displayed as active and doing nothing is the defect `FRD-125`
exists to prevent; saying so is the minimum honest interim.

**An existing architecture assertion caught the new endpoint, and was right for the wrong reason.**
`FRD-602` left a test asserting that `visible_scope` is resolved exactly once *in the reporting
module* — meaning "the CSV path did not grow its own". The anomaly list is a second, legitimate
endpoint scoped by the very same function, so the count went to two and the test went red. It now
says what it meant: **each endpoint** in that module resolves the scope exactly once — which is the
stronger property, because it also catches an endpoint that resolves it **zero** times.

30 engine tests, 3 more in Management, migration `0016`, seven mutations (`N7`–`N13`). `N12` came
back **STALE** rather than surviving — `ruff format` had reflowed the line it pointed at — and was
re-anchored; a mutation whose anchor moved protects nothing.

---

## 2026-08-07 — Phase 5 begins: what an installation considers abnormal

`ADR-0014` + `FRD-500`, stage A. The gateway has recorded everything since `FRD-122` and nobody was
watching. Phase 5 carries three of the owner's central features (PRD §1.1) — anomaly detection,
incident response, and blocking dangerous requests beyond the injection filter — and they are the
*evidence* half of the product. The governance half is built.

The design decision came first, because the two halves pull opposite ways. Detection worth having
looks **across requests**: a caller whose refusal rate jumped, a use case whose spend tripled
overnight, a credential suddenly used from a new address. Response worth having happens **before**
the damage. §3 forbids putting analysis on the request path, and an engine that can only describe
what already went out is a report rather than a control.

`ADR-0014` settles it: **detection is asynchronous, enforcement is not, and they meet at a written
decision.** Evaluation is fed by the request log — the same rows, so a detector cannot see anything
the report cannot, and "the alert says X but the report says Y" is not a reachable state. It also
means detection sees **refusals**, which is where much of the signal is: a thousand rate-limited
requests *is* the anomaly, and a detector fed only served traffic would be blind to exactly the
caller worth noticing. Actions are written decisions with an **author**, an **expiry** and a
**record** — an automatic block with none of those is an outage with a good reason.

This stage is the rule itself: what to watch, over what window, above what threshold, and what to
do then. Seven kinds, a **closed** vocabulary on the same argument as `FRD-114`'s capability flags
— the tempting alternative is a rule engine (field, operator, value), and it fails on the first
review: `p95_latency > 900` reads perfectly and is unimplementable against a store with no
percentile function, which `FRD-601` already ran into and said so.

Three decisions worth keeping:

- **`alert` is the default, and that is a safety property.** A detection system whose first setting
  is `block` blocks the wrong thing once and is then switched off forever. A rule is a hypothesis
  about what abnormal looks like until somebody has watched it be right. Deliberately the opposite
  default from `FRD-125`'s classifier, for a reason that generalises: *that* control had already
  been chosen, configured and displayed as active, so failing open made it a badge without a
  control.
- **A ratio is not a threshold.** `spend_spike` compares against the preceding window rather than a
  fixed number, because a fixed number is a budget and there is already one. What it catches is a
  change of *shape* — €4/day for a month then €40 today is worth a look under a €100 cap, and no cap
  expresses that without being lowered until it refuses normal traffic.
- **A global rule is IT Security's to author.** Its effects land on use cases its author may not be
  able to see, so the *API* says so rather than the UI (`FRD-206`'s rule, applied to a second
  surface). A global rule is nonetheless **visible to everybody** — a rule that can block your
  traffic is a rule you are entitled to know about, whoever wrote it.

Two shapes that cost nothing now and would have cost a debugging session later: `use_case` is
**NULL** for a global rule rather than an empty string, because "" is a use case named "" that
matches nothing while looking like it matches everything — and a consumer event that carries no
`use_case` key at all is **skipped rather than treated as global**, since widening the reach of a
rule that can block traffic is the wrong way to be forgiving about a malformed event.

18 Management tests, 5 gateway consumer tests, six mutations (`N1`–`N6`, all caught), migration
`0015`. Next: `FRD-501`, the engine that reads them.

---

## 2026-08-07 — the console stops promising what the server refuses

`FRD-206`. A walkthrough of the running console, role by role, produced fourteen findings. Most were
cosmetic. Three were not, and they shared one shape: **the console was answering questions only the
server can answer, and answering them generously.**

A use-case *user* was shown "Add member" and "Remove" on every row; using either produced a `403`
from the screen that had just invited the click. IT Security signed in to an empty console. Anyone
who could open the pipeline builder could rearrange a graph they could never save.

The cause is structural rather than a slip. Object-level permission lives in `django-guardian` rows,
so it is **not in the token**, so `/api/v1/me` cannot carry it — the console had no way to know and
filled the gap with an assumption. The fix is that the object says what this caller may do
(`can_admin` / `can_manage` / `is_member`), computed by `apps/usecases/access.py` — **the same three
predicates the viewset enforces with**, extracted from private methods so both sides read one
definition. Restating the rules in TypeScript would have been the same defect with an extra copy to
forget.

The test that matters is not "the reader sees no button" but an **agreement test**: for each of the
three answers, the corresponding request is attempted and its status must match what the object
reported. Two mutations (`Z23`, `Z24`) hardcode a reported permission to `true` and are caught by
it. `G1` and `G3` were re-anchored, because this change moved the code they pointed at and a
mutation whose anchor has moved protects nothing.

Three smaller decisions came out of it and generalise:

- **An action nobody can carry out is worse than an absent one.** An absent action reads as a
  boundary; a present one that fails reads as a broken system — and the reader's next move is to
  distrust the figures on the same page. So every withheld action is replaced by one sentence
  naming who performs it, and read-only stays *usable*: members, budgets, limits and the pipeline
  are all still visible, and the dry-run panel still runs, because none of that changes anything.
- **Read-only means inert, not un-saveable.** The builder's graph sits in a native
  `<fieldset disabled>`, so the add/remove buttons inside it cannot be used either. Hiding Save
  alone would let somebody rearrange a pipeline for nothing — the same defect one step later.
- **`is_member` and `can_manage` are separate answers, and so is visibility.** An oversight role
  sees every use case and belongs to none of them (`ADR-0007`), so it must not be offered a key; a
  member belongs to one without administering it, so it must be.

`IT Security` was the other half of the same mistake: `scope_queryset` used one role set for both
"sees every use case" and "sees every figure". PRD §154 gives that role the first and not the
second, and folded together it saw nothing at all. Split into `OVERSIGHT_ROLES` ⊃
`GOVERNANCE_ROLES` — oversight decides visibility, governance decides spend.

And a message that was true and still wrong: the budgets tab told a reader "the gateway does not
count you as a member of this use case" while they were looking at their own name in the Members
tab. Both statements were correct — the gateway takes membership from the Keycloak group
`/use-cases/<slug>`, Management from its own table. It now says exactly that, because the remedy is
a group and not a table.

The rest of the walkthrough, fixed in the same pass: the session now renews itself (`offline_access`
+ silent refresh — an expired token was reporting "invalid credentials" on every screen, which reads
as the data being untrustworthy rather than the session having ended); creating a use case is a
button and a window that ends on the new use case's **settings**, since one with no members, no
budget and no limits is not finished and the list is what makes it look finished; "slug" became
**technical id**, filled in from the name and described by what makes it matter (it is permanent and
appears in every API key; the name is not); the model editor became a window that names the model it
is editing; the reporting cards got short headings plus an info button holding the sentence that
says what each figure counts — "Refused by a control" was breaking the card row, and the answer to a
heading that does not fit is not a smaller font; and the export row and the catalog's Edit/Remove
pair got the spacing they never had, the latter because two buttons touching invite the wrong one
and one of them is destructive.

Also documented rather than left in the DEVLOG alone: `FRD-130` (the demo showcase), which the
previous entry referenced without a document existing.

**A session that has ended is a login, not an error.** Reported after the rest of this pass: a
token going invalid — the tab left open, or Keycloak restarted — produced "invalid credentials" on
every panel at once. True, and the wrong thing to say: it reads as the backend rejecting the
person, and the next thing doubted is the figures on the same page. A `401` on `/api` or `/gw` now
drops the dead token and starts the login, which is the only action available anyway. `403` is
deliberately left alone — that is a real answer about a real permission, and logging somebody out
over it would hide the boundary behind a login screen. One login is started however many requests
fail together, and the path is carried through `state` and restored, but **only if it is a
same-origin path**: `state` survives a round trip through the browser, so treating it as a
destination would be an open redirect with extra steps. Two endings, both checked in a browser
against a real token that was then broken: with the Keycloak session alive the round trip is
invisible; with it gone — the reported case, reproduced by ending the session through the admin
API — the login form appears.

**Two defects I shipped and had to be told about.** The smaller one first: the reporting screen's
new info buttons showed nothing. They carried a `title` attribute — a native tooltip needs a long
hover, never appears on a touch screen, and is invisible to a keyboard — so a control sat there
looking clickable and did nothing when used. That is the exact defect this pass was written to fix,
committed inside the fix for it. The first repair opened it on click, which worked and was still
the wrong answer — an "i" is a thing you point at, and the report had said so. It now shows on
**hover**, on **focus** for a keyboard, and stays pinned on a click for a touch screen, which has
no hover to offer; the panel is positioned rather than in flow, so the card does not grow under the
pointer. An e2e case exercises it as a real hover, because only a browser can tell "renders a
tooltip attribute" from "shows the reader anything" — which is how the `title` version passed
review at all.

**The larger one: the console would not load at all.** The
session-renewal fix (above) added `offline_access` to the requested scopes to get a refresh token.
This realm does not permit offline tokens, so the code-to-token exchange came back
`not_allowed` — and Keycloak answers *that* failure without CORS headers, so the browser reported
a CORS error naming neither the scope nor the realm setting. The page went blank after a
successful login, which looks like a crash and is nothing of the kind.

Two things worth keeping from it. `offline_access` was the **wrong instrument** even where it
works: the authorization-code flow already returns a refresh token, and `offline_access` asks for
one that outlives the SSO session — a credential a governance console has no business holding. And
the reason it reached the running stack is that **I ran three of the four test layers and skipped
the fourth**, on a change that lives only in the fourth: no unit test can perform an OIDC
redirect, and `e2e/tests/auth.spec.ts` — which does — would have failed on the first run. The
config is now pinned by a unit test that says *why*, but the layer rule is the real lesson: a
change to the login flow is an e2e change, whatever else it touches.

That run also turned up sixteen e2e failures — every one of them a test driving a screen this
pass deliberately changed, which is what an e2e suite is *supposed* to do when the UI moves. The
creation form became a button and a window, so the shared `createUseCase` helper drives that
instead; "the inputs are cleared after a successful POST" became "the window is gone and the page
moved on", which is the same zoneless property observed where it now lives. Two changed meaning
rather than mechanics and were rewritten rather than repaired: the governance role no longer
*clicks* Issue key and reads the refusal, because the console does not offer it any more; and the
three disabled navigation tabs are gone, so the property they encoded (the console follows the
roles in the token) moved to a chip per role in the header, carrying `data-role` so it stays
assertable without depending on the wording. Three more were the same story a level down: the
model editor's Save moved into a window footer and reaches its form by `form=`, so the tests
address it that way — which is also what proves the association still works; and the
consumption-hidden message changed wording deliberately, so the assertion follows the new
requirement (name the Keycloak group *and* say it is not the member list on the same page) rather
than the old sentence.

Two demo-seed defects fell out of asking the *running* stack who could manage what, rather than
reading the declaration: `itgov` was still administering `personalwesen` and `itsec` still belonged
to `kundenservice`, both from declarations long since changed. **A membership left behind is not a
stale row, it is live permission on a use case** — the seed now reconciles to what it declares and
revokes what it removes. And `personalwesen` no longer belongs to an oversight role at all: it was
there so the demo could show a use case `ucadmin` cannot touch, at the cost of teaching the opposite
of what IT Steuerung *is*.

**Found while running the gates: `make ci` was already red**, and not because of anything in this
change. `ruff` is declared as `>=0.9` and `uv.lock` had moved to 0.16.1, whose formatter targets
`py314` and applies **PEP 758** — `except A, B:` without parentheses. Eight committed files were
therefore unformatted against the very tool the gate runs. Reformatted, and `Z19`'s anchor moved
with the line it points at. Worth knowing for next time: a lock refresh can redefine a *format*
gate across the whole tree without a single source line being edited, and nothing announces it
except the gate itself.

---

## 2026-08-07 — a demo somebody can walk through

`FRD-130`. `seed_demo` created five roles and one user each, which lets you log in as every role and
look at five empty screens. This gives each of them something to see, and picks the content so the
differences between the roles are **visible rather than described**.

Three use cases, each making one governance decision concrete: `kundenservice` stores prompts with
the shortest retention that still supports an incident review and runs the heuristic injection
filter; `entwicklung` is higher-volume and carries rate limits instead of a tight budget;
`personalwesen` has **storage switched off** — the figures are still collected, the prompts are not.
`ucadmin` deliberately administers only two of the three, because switching to that account and
finding two instead of three is the fastest way to show the scoping is real rather than a filter in
the frontend.

Budgets across every axis the UI offers — cost, tokens, requests; use-case and member scope; day and
month — and `tools/demo_traffic.py` drives **real** requests through the gateway against the local
model, including one prompt-injection attempt that the filter refuses. Inserted rows would have been
consistent; they would also have been a story about the product rather than the product.

Ollama joins the `demo` profile with a separate pull step, so the server's health check stays honest
— a container reported "healthy" only after a multi-hundred-megabyte download makes every restart
look like a hang.

### Four things the first run got wrong

**The seed declared no models.** `local_models` gated on `AIRA_OLLAMA_URL` while this stack is
configured with `AIRA_OPENAI_SERVERS` — the named-server form `FRD-123` moved to when a self-hosted
fleet turned out to be several machines. The catalog came up empty and the use cases pointed at
nothing. Either form counts now.

**801 use cases.** A demo database accumulates the fixtures of every test run that ever pointed at
it, and a global administrator opening a list of `burst-3i6g5l` and `dryrun-xkroyc` learns only that
the list is long. `--fresh` now means *every* use case, not just the ones the seed made.

**And then `--fresh` killed the keys for ever.** Deleting a use case revokes its API keys, and
revocation is **terminal** in the read model on purpose — `api_key.created` must never resurrect
one. Announcing a delete for a slug the same run then recreates therefore permanently revoked the
deterministic demo keys: three use cases, three keys, 401 for ever. Recreating the same slug is a
**reset, not a retirement**, and the events have to say so. The rule is right; the seed was wrong
about which event it was sending.

**The budgets showed nothing.** A plausible-looking €0.50 monthly cap against a local model priced
in fractions of a cent per million tokens sits at 0.02% after a walkthrough — technically correct
and useless. They are calibrated against what the demo traffic actually costs: after one run the
bars sit between a third and two thirds, and two more runs reach a limit.

`make showcase` starts the lot and prints who to log in as.

---

## 2026-08-07 — a developer round against the running model

`FRD-129`. After two structural changes to the request path and three defect fixes, a walk through
the system the way somebody using it would: both surfaces, ordinary journeys, dropped connections
on every path, and every figure checked **in the database** rather than in the response body.

**47 live cases**, and the shape of them is the point. Nothing asserts on the *content* of an
answer — `qwen3:0.6b` is a real model and a poor one, and asserting its accuracy would be testing
somebody else's work and flaking. What is asserted is what the gateway promises: that a request is
recorded, weighed, priced and bounded, and that the **two surfaces leave the same facts behind** for
the same work. That last one is checked by comparing audit rows rather than by reading two code
paths, because a step skipped on one surface is invisible in its own tests.

Everything the last days built held: the tokens in the row equal the tokens in the response, the
budget counter equals the sum of the rows, a batch of five is counted as five, an exhausted budget
refuses without paying for a classifier, a dropped connection leaves a row on every path, and both
surfaces are rate-limited.

### Two findings

**A declaration nobody had measured.** The catalog said this model offers the `minimal` thinking
mode. The server accepts `none`, `low`, `medium`, `high`, `max` — and refuses `minimal` **by name**.
The seed file had deliberately declared no thinking at all, with a comment saying to add it "when
the integration run says so"; a hand-written entry had filled it in from the enum instead. The run
has now said so, and the seed carries the measured set. Declaring from the vocabulary rather than
from a measurement is the mirror image of the mistake `FRD-114` was written to prevent.

**And the error it produced was worse than the error itself.** The caller received
`502 UNAVAILABLE`, "Upstream returned 400." The provider had said, precisely, *invalid reasoning
value: 'minimal'* — and that was discarded. An operator reading `UNAVAILABLE` checks a status page;
the fault was in their own catalog.

An upstream **400** now answers `400 FAILED_PRECONDITION` and carries the provider's reason. Same
argument as `NoCapableModel`: "the provider refused the body we built" is operator-fixable, an
outage is not. The test that encoded the old rule had a comment giving the reason to change it —
*"a 400 from the upstream reflects our config"* — and it does, which is exactly why calling it an
outage misleads.

**Only 400**, though. The old test was right about the other half: a 401 or 403 is about *our*
credentials, the caller cannot act on it, and the provider's message may name the credential. Those
stay masked, and a test now pins that too.

Mutations `Z21`/`Z22`. **231 properties.**

---

## 2026-08-07 — the third surface was a thought experiment, and it is withdrawn

`FRD-106` — an OpenAI-compatible surface exposed to callers — is **not wanted**. It was raised to
push a question about generalisation, and it did its job: *if a third surface were added, would it
write those six steps a third time?* Yes. That answer produced `FRD-126` and `FRD-128`.

Worth separating two things that share a name. The OpenAI **wire dialect** stays and is untouched:
Azure Foundry and the self-deployed fleet speak it, as an *upstream* (`ADR-0011`). What is
withdrawn is an OpenAI-shaped **API surface** pointed at callers. Only one of those was ever
deferred rather than declined, and the ROADMAP now says which.

The consolidation does not need the surface to justify itself, and the docs now say so rather than
leaving a reader to infer that two structural changes were speculative work for a cancelled
feature. What they actually fixed was already in the code:

- four of six paths lost the audit row when a caller hung up mid-answer,
- the KIRA surface had no rate limiting at all after one control moved one function over,
- the KIRA streaming path never received the disconnect fix the Gemini one earned.

None of that needed a third surface to be real. The hypothetical was the lens, not the reason.

---

## 2026-08-07 — a request the caller abandoned is still a request that happened

`FRD-128`, the second of the three steps, and it started with a question rather than a failure:
*have all the paths been tested with a dropped connection?*

No. Streaming had been — Gemini's by closing the iterator and by a live client walking away,
KIRA's the day before (`FRD-127`). **Every non-streaming path had not, and all four lost the audit
row.** A caller who went away while the model was still answering made a request that reached the
upstream, spent tokens and spent money vanish from the record.

Six paths, each with its own copy of `hold → dispatch → check → price → settle → record`, and the
guarantee is the *order*. Two of the six were right. `accounting()` owns it now, shielded, and the
surfaces went from **twelve** direct calls to **zero**.

A caller who abandons a request is recorded with status **499** and outcome `client_gone` — nobody
is sent that status, it exists so the audit can tell that case from a served one, and it is its own
outcome because "clients keep hanging up" is a different thing to investigate from "the provider
keeps failing".

### Three things that cost a draft each

**The accounting has to run inside `hold`, not around it.** Outside, `hold` sees an unresolved
reservation on the way out and gives it back — then the settle books it again. One request, settled
once and released once.

**`hold` owns the release.** An explicit release in the exit counted the give-back twice.

**An embedding produces vectors and reports no tokens**, which is not the same as producing
nothing. Conflating them would release a whole batch's reservation and leave batched traffic
invisible to a request limit.

### Two tests that were asserting the wrong thing

Both were coupled to *where* something happens rather than to *whether* it happens, and both went
quiet instead of failing when it moved. One monkeypatched `routes.record_request` and stopped
intercepting the moment the write moved into the shared sequence. The other counted calls to
`release` through a delegating stand-in — which `hold`'s internal `self.release(...)` never passes
through, so it was testing the wrapper. Both now read the row and the counter.

### And two of my own mistakes worth recording

A string replacement removed `'    reservation = ...'` as a **substring** of the eight-space
version, leaving four stray spaces that silently re-indented the next line. Then a heuristic
"repair" pass made it worse by de-indenting an `except`. The lesson is not subtle: line-based edits
need line-based matching, and a repair driven by a guess about what broke is a second break. Both
were caught by the syntax check within a minute, which is the only reason this paragraph is about
drafts.

Mutations `Z17`–`Z20`, with `Z17`/`Z18` re-anchored onto the shared sequence — the hand-written
finisher they pointed at is gone. The shield still has **no** mutation, and the reason is
`FRD-110`'s, re-verified by `FRD-127`: no hermetic test can tell a generator close from a socket
drop, so a harness claiming to guard it would claim a proof nobody has.

---

## 2026-08-07 — the fix the second surface never got

`FRD-127`, and the first of the three steps that came out of assessing what a third API surface
would cost.

The Gemini streaming path wraps its accounting in `finally` + `asyncio.shield`, with a long comment
explaining that a caller dropping a real socket **cancels the response task**, so a bare `await`
there loses the settle and the audit row. It was found as a 1-in-8 integration flake. **The KIRA
streaming path had neither** — no `finally` at all.

That is what duplication does, and where it leaves it: the surface written second did not receive
the fix the first one earned.

The window is different here, and the difference is why copying the Gemini test would have proved
nothing. This surface's "stream" delivers **one terminal event carrying the whole answer**, so the
accounting happens *before* anything is yielded — hanging up after the first chunk finds the work
already done. What is exposed is the long await in the middle: a caller who goes away while the
model is still thinking. The upstream was called; the request then vanished from the record.

Now one shielded exit accounts for every way out — served, refused, or cancelled — and a stream
that produced nothing chargeable is **released** rather than settled, because booking a request
against somebody who received nothing would spend a request limit on a caller who hung up. The
status for that row is `499`: nobody is sent it, because there is nobody to send it to, and it
exists so the audit can tell that case from a served one.

### The test that had to be corrected before it could be trusted

Written first, it reproduced the defect exactly — model reached, no row. Then it turned out to pass
**with and without the shield**. It proves the `finally` exists, which was the real gap here, and it
cannot prove the shield matters: in-process cancellation and a dropped socket are not the same
event, which is precisely what `FRD-110` recorded when it declined to add a mutation for Gemini's
shield.

So there is no mutation for this one either, and the reason is written next to it. A harness that
claimed to guard the shield would be claiming a proof nobody has — worse than a harness with a gap
that says so. `Z17` guards the row, `Z18` guards the release, and the shield is the integration
layer's to check.

An earlier draft of `Z17` added a `_no_shield` passthrough to the production module for the harness
to swap in. Production code shaped by its own test harness is the wrong direction; the mutation is
a one-line edit now.

---

## 2026-08-07 — a surface parses; the layer decides

`FRD-126`. Prompted by a question rather than a failure: *why are there two pipelines with six
steps each — would emulating an OpenAI interface spawn another six?* It would have.

There were never two pipelines. There is one, and there were **two hand-written choreographies
around it**. `api/serving.py` was extracted precisely so both surfaces could share everything below
the wire format, and its docstring says a surface owns "parsing its own wire format, rendering its
own error envelope, and its own routes". It shared the *steps*. Nobody noticed it had not shared
the *order*:

    Gemini                          KIRA
    check_not_empty                 check_not_empty        ⎫
    guard_before_work               guard_before_work      ⎪ _prepare()
    run_pipeline                    run_pipeline           ⎪
    check_declaration               check_declaration      ⎪
    resolve_thinking                resolve_thinking       ⎭
    enforce_pre_dispatch            enforce_pre_dispatch   ← written out in three handlers

That distinction is the whole story of the last two days. **Every guarantee this layer makes is a
guarantee about the order** — rate limit before the pipeline or a refusal is paid for; declaration
and thinking after routing or they are checked against a model that never serves the request;
reservation last or it is made against the model the caller *named*. None of that can be expressed
by a function that knows only its own step, which is why the same gap kept coming back wearing
different names: `:embedContent` bypassing the gate, then the KIRA surface losing rate limiting
entirely when one take moved one function over.

`prepare_for_dispatch` owns the order. The KIRA surface went from six of these calls to **zero**.
And the rule is now a test — `test_surface_layering.py` parses each surface and fails on a direct
call to any step, the same shape as the vendor assertion in `test_vertex.py`, for the same reason: a
layering rule only a reviewer enforces is a rule the *next* surface breaks, and the next surface is
the one nobody is watching yet.

The evidence that this was a move and not a rewrite is that **no test changed**: 887 hermetic and
316 live, green before and after.

**Four mutations came back `STALE`** — not "survived": the harness distinguishes "this property is
undefended" from "this mutation no longer applies", and all four pointed at lines this change moved
into the shared sequence. Re-anchored there. A fifth (`Z13`) was **removed**: it claimed "the
compatibility surface takes the same early gate", which was a distinct property only *because* each
surface took the gate for itself. Now its anchor and `Z11`'s are the same line, and two mutations
on one line measure one thing twice. What it really claimed is enforced structurally by
`test_surface_layering.py` — the same call `X3` got, for the same reason.

### And the honest limit of this change

Asked what a third surface would now cost, the answer turned out to be *half of it*. The
pre-dispatch order is shared; the **post-dispatch** order — hold, dispatch, check, price, settle,
record — is still written out **six times**, three verbs in each surface. Same shape, one step
later.

It has already cost a defect. Gemini's streaming path wraps its accounting in `finally` +
`asyncio.shield`, with a comment explaining that a client dropping a real socket *cancels* the
response task and a bare `await` loses the settle and the row — found as a 1-in-8 integration flake.
**The KIRA streaming path has neither.** The surface written second never got the fix the first one
earned, which is precisely what duplication does and precisely where it leaves it.

---

## 2026-08-06 — the filter that was configured, displayed, and doing nothing

`FRD-125`. Third finding of the same live round, and the worst of them.

A use case configured the LLM prompt-injection filter to **block**. An injection was sent. The
gateway answered **200**, and the model complied with it — it printed a system prompt.

The cause is one line, and it is the same line as the day's other findings wearing a third face.
The classifier asks the model for a one-word answer inside a four-token allowance, and it dispatches
**straight to the provider**, bypassing the catalog-based thinking resolution the serving path
performs. So it never says "do not think". A reasoning model thinks by default. All four tokens went
on reasoning, the answer came back empty, and the verdict was a `bool`:

    "INJECTION" in ""   →   False   →   clean

The same bug had silently disabled `model_route`, which returned "no category matched" for every
request — indistinguishable from a router whose categories genuinely never fit.

**A verdict now has three values.** `undetermined` covers an upstream failure, an empty reply, a
reply containing neither word, and a reply containing *both* — "SAFE, no injection attempt here"
was asked for one word and gave two, and picking a winner would be a precedence rule nobody could
predict from outside (the argument `FRD-111` already makes about two `thinkingConfig` spellings).

**And it blocks by default**, which reverses the old "fails open". That reversal deserves its
sentence: the old behaviour was defended as "a classifier outage must not take down legitimate
traffic", which is a real concern and the wrong answer — `FRD-405` settled the identical question
for rate limits with *the moment a control stops working is the worst moment to stop applying it*. A
filter that passes everything while the builder shows it as active is not a degraded control, it is
an absent one wearing the badge of a present one. `on_undetermined: allow` restores the old
behaviour for anyone who wants it, as a choice, on the audit row.

Two smaller things fell out. A filter that ran and **passed** now records that it did — "the filter
found nothing" and "no filter was configured" used to look identical afterwards and call for
opposite conclusions. And mutations `P1`/`P2` were **re-anchored**: they pointed at a line this
change moved, and a mutation whose anchor has moved protects nothing.

### An operational finding that is not a defect

Against `qwen3:0.6b` the LLM filter answers `INJECTION` to everything, including "What is 2 + 2?".
The gateway is correct; the model is not a usable security classifier at that size. Worth saying
because the builder makes the LLM mode look like the stronger option: **it is exactly as good as
the model behind it**, and the heuristic — which cannot be undetermined, because a regex either
matches or does not — has no such failure mode.

### And a test lesson

Two live assertions had to be rewritten because they were testing the *model*, not the gateway. A
seed reproducibility check that failed one time in three (this server's first generation after a
cold context differs — its prompt cache, not our seed), and a router check asserting that a 0.6B
model picks the right category. Both replaced by the property that is actually ours: the classifier
gets an answer at all. The second one asserts the **old** call shape still returns nothing, so if
that ever stops being true the test says so rather than passing for a new reason.

### The other half: it was not being paid for either

Counting model calls rather than reading code again. One caller request with an LLM step makes
**two** model calls and left **one** audit row. The classifier's tokens were invisible three ways at
once: `FRD-601` reported a spend they were not part of, `FRD-403`'s *"unpriced traffic is counted
apart, never as zero"* was broken by counting them as **nothing at all** — the one thing that rule
exists to forbid — and `ADR-0013`'s auditable model access had a model call in it that nothing
recorded.

Each pipeline call now leaves its own row, named `pipeline:<step>` so reporting can separate what a
use case *asked* from what *governing it* cost, and is booked against the budget with
**`requests=0`**: the caller made one request, and counting the classifier as a second would inflate
every request figure and could trip a request limit for traffic nobody sent.

The hook lives in `run_pipeline`, in a `finally`, and the collector is **passed in** exactly as
`decisions` already is — so a step that *blocked* still reports what deciding to block cost, and
both surfaces get it because both call that function. A hook per surface boundary is the shape that
let `:embedContent` slip past the pre-dispatch gate.

The number is the part worth keeping: against the real model, **the classifier's call costs roughly
as much as the answer it guards**. A use case running an LLM filter was reporting a little over half
its actual spend.

### The refusal that was billed for

Follow-up question from the owner — *do the filter costs count against the budget?* — and then a
measurement of what "over budget" actually did. The pipeline ran **before** the budget guard, so a
use case one request past its limit kept running its LLM injection filter on every subsequent
request: all refused with a 429, all billed for the classifier. A 20 000 cost limit, one served
request, seven refused, **72 400 spent and still climbing**. A client with a retry loop spends
without bound. That is a denial-of-wallet wearing a budget's name.

`guard_before_work` runs the two controls that need no model — the rate limit, and *is this use case
already over* — before the pipeline. The reservation stays where it was, because it is made against
the model routing chooses. Same probe now: spend stops at **25 600** and does not move across six
further refusals.

The owner's decision, asked and recorded: a bounded overshoot is an acceptable price for the
security step running. What was never acceptable was the unbounded one.

Two drafts died on old lessons. The gate belongs **before the verb branch**, not inside
`run_pipeline` — embeddings have no pipeline, so the tidier placement would have left
`:embedContent` unlimited, the same verb and the same way as `FRD-405` B3. And `units` has to be
computed before the gate: the first draft took one unit early and *commented* that the batch weight
was taken again later. It was not. A batch of 500 metered as one request, by a comment asserting a
rule the code did not have — caught by a test that already existed, which is the only reason this
paragraph is about a draft rather than about production.

Also: four budget stand-ins in the test suite had to inherit the new method rather than stub it.
A stand-in more permissive than the thing it replaces is how a control comes to be tested against
something that cannot refuse — `CLAUDE.md` §3 names it, and adding a method to the real service is
exactly when it bites.

### Recording it is not enforcing it

Asked afterwards: *do the filter costs actually count against the budget?* They were being written
to Postgres — the system of record — so reporting was right. `FRD-405`'s guard reads the **shared
counter**, and a Postgres-only write reaches it only when the counter expires and rebuilds, up to
`COUNTER_TTL_SECONDS` later. A small cost cap and four requests: the counter read 41 000 against a
limit of 40 000 and the next request was served.

Both stores now. The live re-run refuses the third request at 40 200, naming the cost budget.

The test written for this **passed against the broken code** on the first attempt, and the reason is
worth more than the fix: on a *cold* counter the guard seeds from Postgres, so a Postgres-only write
is visible anyway. The test never reached the path it was named after — the exact trap `CLAUDE.md`
§3 already lists — and it now warms the counter before it measures anything.

### And a stale number, which is the same defect in prose

`CLAUDE.md` claimed the harness guarded **124** properties. It guarded 220. Every update to that
figure across this release was a string replacement whose anchor did not match — so each one
changed nothing, reported success, and moved on. Six no-ops in a row, none of them checked.

That is precisely the failure this release has been about, arriving in the documentation instead of
in a request: an operation accepted, apparently successful, and not performed. It gets the same
answer. `tools/tests/test_documented_counts.py` compares the stated figure against the harness and
fails when they disagree, and `tools/tests` is now in the default `testpaths` — a check nobody runs
is a check nobody has.

### Two survivors, and what each of them was

The harness reported two properties undefended on the first full run, and both were my own doing.

`Z8` — *a pipeline call is booked against the budget* — survived because every accounting test
asserted the **audit row**, and the app under test had no budget configured. Booking zero tokens
changed nothing anybody was looking at. The fix is a test that configures a budget and counts;
without it, an unbudgeted classifier is not a rounding error, because measured against a real model
it costs about as much as the answer it guards, so a use case at its limit would keep spending past
it.

`Z2` — *an upstream failure is undetermined, never clean* — survived because **its anchor had
moved**: part (b) lifted that `return` out of `verdict` and into `classify_text`, and a mutation
whose anchor no longer matches cannot break the property it names. This project already knew that
rule; what is new is that the harness now demonstrates it rather than asserting it, because it
reported the property as *undefended* instead of quietly passing. Re-anchored.

Chasing `Z2` also turned up a second copy of the router's logic — `classify` had been left
re-implementing what `classify_text` does, and its `except UpstreamError` branch was already the one
no test reached. Two copies of one rule, about an hour old. It now delegates.

Mutations `Z1`–`Z10`. **220 properties defended.**

---

## 2026-08-06 — the refusal that ran before the boundary

Same live round, second finding. `FRD-122` §12.

`FRD-122`'s rule is that the audit log records what was **asked**, not only what was served, and it
was closed at the route's exception boundary — one site, deliberately. One refusal never reaches
that boundary: the request-body ceiling is pure ASGI middleware and answers **before any route
runs**. A 20 MB body was refused with a 413 and left no trace at all.

Found by posting one and counting rows. Not by reading the code, which is entirely consistent about
this rule everywhere the code can be read — the gap is in a place the rule was never applied.

The fix is small and its shape is the point: both exits from the decision (a declared
`Content-Length` over the ceiling, and a body that declared none and was cut off mid-read) record
through **one** function. A new closed-vocabulary outcome, `request_too_large`, rather than folding
it into `invalid_request`: "somebody keeps posting 20 MB" and "somebody sent malformed JSON" are
different operational facts and a shared bucket hides the first inside the second.

**The row carries no identity.** The credential in the header has not been verified at that point,
and recording it would let anybody write another system's name into the audit trail by sending one
oversized request. An unverifiable claim is not evidence — the same rule as "unpriced is not free"
and "undeclared is not permitted", pointed at identity. The body is not stored either: it is over
the ceiling, and keeping what we refused to read would undo the reason for refusing it.

And what stays unrecorded, said out loud rather than left to be discovered: **a 401 leaves no row**.
That is a decision. A request that never presented a valid credential is a security event and
belongs with anomaly detection and incident response (`FRD-500`/`501`/`503`), not in a usage log
where it would surface in spend reports as a refusal attributed to nobody. Written into `FRD-122`
so whoever builds those finds the question already asked.

Mutations `Y9`–`Y11`. **210 properties defended.**

---

## 2026-08-06 — twelve fields, eleven silences

A local model made this findable. `FRD-124`.

Twelve fields a legitimate Google client can send were posted at the running gateway. **Eleven came
back 200 and did nothing.** `stopSequences` — unbounded output. `seed` — a different answer every
call, which is the exact failure a seed exists to rule out, presented as the model being creative.
`tools` — prose where a function call was expected. `safetySettings` — a governance control applied
nowhere. `candidateCount: 3` — one candidate, and one answer where three were asked for does not
look like a partial failure, it looks like the model had one thing to say.

The project has a rule for this and has had it since `ADR-0012`: **a chain must not be able to
degrade a request silently.** A model that cannot read the PDF is skipped, never sent the prompt
without it, because a dropped attachment produces a fluent wrong answer with a 200 and the caller
blames the model. That rule was pointed at the *model*. It was never pointed at the *surface* — and
a field the surface drops is the same defect one step earlier.

### The one that started it

`thinkingConfig: {mode: "disabled"}`. The dialect mapped `disabled` to an **absent**
`reasoning_effort`, with a comment saying "there is no 'off' value; the absence of the parameter is
off, as with Anthropic." Measured against a real reasoning model: sent no `reasoning_effort` it
thinks anyway — absence selects the *model's* default, not off — and it spent the whole 600-token
allowance doing it. Empty answer, `MAX_TOKENS`, 200. The reasoning is stripped from the response by
design, so the caller sees a model that failed to answer, not a setting that was ignored. The same
server sent `"none"` answers in twelve tokens.

There was a unit test asserting `"reasoning_effort" not in body`. It was green because the code and
the test came from the same wrong idea. **Off has to be said out loud.**

### What was built

Three answers instead of two:

    portable and supported     → carried to the dialect       topP, seed, stopSequences, …
    known but out of scope     → refused, saying why          tools, safetySettings, cachedContent
    the dialect cannot say it  → the candidate is skipped     top_k on OpenAI, seed on Anthropic

The third reuses the requirement mechanism that already carries region, media types, schemas and
thinking. `SamplingExpressible` is the fifth to share it — and the first that is a property of the
**dialect** rather than the model, because no catalog entry can say whether `top_k` exists. That
depends on the wire format the request will travel over, and no dialect has all six:

    Gemini      top_p  top_k  seed  presence  frequency  stop
    OpenAI      top_p    —    seed  presence  frequency  stop
    Anthropic   top_p  top_k    —       —         —      stop

Refusal rather than best effort, for the reason that decides every one of these: `seed` on a Claude
candidate produces a perfectly good answer that simply is not reproducible, and **nothing in it
differs from a correct one**.

### Reversing FR-7, on evidence

`FRD-100` FR-7 had the request models ignore unknown fields, so real Gemini clients sending extra
keys were not rejected. Both halves of that argument turn out to be wrong: Google's own API rejects
unknown fields, so leniency was never the compatible choice; and the fields clients actually send
are ones that change the answer. Strictness is one-directional — **responses keep ignoring extras**,
because a provider adding a field must never break a caller.

### Two test lessons, one of them repeated

The hermetic tests for `SamplingExpressible` exercised it directly, never through the route. A
mutation removing it from the route's requirement list left every one of them green: two correct
halves and no wire between them. **That is the second time in one day** — the CSV export's scope
test had the identical shape, built the file itself instead of downloading it, and survived the
mutation that made the endpoint ignore the caller's scope. Both are fixed by driving the real
endpoint; both were invisible to coverage, which saw every line run.

And the integration tests here assert **behaviour, not wire bodies**: a seed makes three identical
requests return one answer, a stop sequence truncates the output, thinking off produces an answer.
None of that can be established by inspecting a dict — which is exactly how the thinking defect
survived a suite that appeared to test it.

38 hermetic tests, 9 against the real model, mutations `Y1`–`Y8`. **207 properties defended.**

---

## 2026-08-06 — the usage export, and the same dependency lesson twice
`FRD-602`. CSV is a **renderer on the existing reporting endpoint**, chosen by `Accept` — never its
own endpoint, because `FRD-601`'s visibility rule is one function and a second entry point is a
second chance to forget it. That is how an export comes to return more than the screen: a
governance failure delivered as a *file*, forwarded, saved, impossible to recall. The test asserts
on the file's bytes, and a second one checks by source inspection that `visible_scope` is resolved
exactly once.

The format details are small and none of them are obscure: a BOM so Excel reads `süd` as a name,
CRLF because RFC 4180 says so, quoted keys because a use case called `vertrieb, süd` would
otherwise shift every figure on its row one column left — a spreadsheet that is *wrong* rather than
broken. Commas rather than semicolons, and the download panel says Excel may ask about the
separator, which is the honest alternative to picking the other surprise.

### The lesson this project has now learned twice

`aira_common.secrets` imports `httpx`. Every hermetic test passed, a live Vault read worked, and
the **management migration container died on `ModuleNotFoundError`** — because `httpx` was a
*gateway* dependency and a workspace `uv sync` installs everything into one environment.

The line directly above it in `libs/pyproject.toml` is a comment explaining that `pyjwt` was added
for exactly this reason, after exactly this failure. **A shared library's dependencies cannot be
validated by any environment that also installs its consumers** — and this repository's dev
environment, its test runner and its coverage gate are all such an environment.

So `libs/tests/test_declared_dependencies.py` now parses every module in `aira_common` and fails on
any third-party import the package does not declare. Shown to fail with the declaration removed. It
costs milliseconds and replaces a failure that costs a deploy.

---

## 2026-08-06 — diagnostics, and a probe that would have proved nothing
`FRD-117` FR-1 to FR-6. The design centre is one sentence from §5.2: **a health check must not be
able to take down a healthy service.** The predecessor's `/health` probes every registered model on
every call, which makes readiness as slow as the slowest upstream — so one degraded provider evicts
pods that were serving perfectly well, and against a paid endpoint it bills for the privilege.

So the probe runs in the background and `/readyz` reads the last verdict. A live test asserts ten
readiness probes finish in under five seconds, and another asks the model server directly whether
probing loaded anything — because "the probe never generates" is exactly the kind of claim that
decays into a convenient call somebody added later.

**The first draft would have proved nothing.** It probed by calling `provider.models()`, which is
*local configuration* evaluated once when the registry is built: it cannot fail later and says
nothing about the network. Every verdict would have been a confident green describing nothing —
worse than no probe, because a green board gets acted on. It surfaced while writing a test with a
provider that raises, discovering such a provider cannot be registered at all, and following that
back. Adapters now implement an optional `ping()`, a GET of a listing; one without it is reported
`probed: false, "not checked"`, because *we did not look* and *it is fine* are different answers.

The case that mattered most could only be staged live: stop the model container and watch. `/readyz`
stayed **200 `ready`** with `degraded: true`, and cleared when it came back. A load balancer keeps
the instance — the signal is for an alert, not an eviction, and that distinction is the feature.

`x-trace-id` is pure ASGI, mounted outermost. `BaseHTTPMiddleware` would run the app in a separate
task and lose the span context, so the header would be absent exactly when a span exists; and
outermost because the responses that most need correlating are the ones an exception handler
produced. Confirmed on a deployed gateway: the 401 carries one.

CORS refuses `*` with credentials **at startup**. The predecessor ships that combination; browsers
reject it, and a server implementing it by reflecting the origin lets any site a user visits call
the API with their credentials. A misconfiguration that only appears under a browser is one that
ships.

**FR-7, the second OpenAPI 3.0 document, is not built** — it serves a legacy portal this deployment
does not have, and a generated document nobody reads silently stops matching the routes. Said
rather than quietly skipped.

---

## 2026-08-06 — Vault, finally reading from the thing that was already running
`CLAUDE.md` §2 has said "secrets only in HashiCorp Vault" since Phase 0, and Vault has been in the
Compose stack for as long — with **no code reading from it**. Every credential this system holds
was an environment variable, which is exactly the state the policy exists to prevent.

`aira_common.secrets` does the AppRole login and the KV-v2 read; a pydantic `VaultSource` puts it
above the environment for both planes. **A settings source rather than an injection into
`os.environ`**, and that is the security half rather than a style choice: values in the environment
are readable from `/proc`, inherited by every subprocess, and reach any library that dumps the
environment on a crash.

Fail closed is the whole design. A configured Vault that cannot be reached stops the process,
because the alternative turns a broken secret store into a *silent downgrade* — the environment in
that scenario usually holds a stale or development value, so the service starts, looks healthy, and
is wrong. `ADR-0007` established the principle for `SECRET_KEY`; this extends it to every
credential. "Vault is down" and "nobody wrote that key" are **different exceptions**, because they
call for different actions by different people.

Tested against the Vault in the stack, with a **real AppRole** the suite creates — its own policy,
scoped to its own path, removed afterwards. That is what makes the least-privilege case rest on
Vault's decision rather than on our mock: the same credential that reads this test's path must fail
on another one, and it does.

### The test that could not fail

"No value ever reaches a log" was written first with pytest's `caplog`. It passed. It would also
have passed against a loader that printed every secret in full — these logs go through structlog
and never reach the stdlib handler `caplog` watches. For the one property whose failure is a
career-ending incident, a green that means nothing is worse than no test at all. It captures
through `structlog.testing.capture_logs` now, and the same trap is worth remembering anywhere else
this project asserts on log output.

One mutation survived and it was **my test's fault, not the code's**: `V5` says a secret-id file
that cannot be read is *named* rather than fallen through, and the assertion matched only on the
variable's name — which the "no secret-id anywhere" message also contains. It passed against a
version that silently gave up. Matching on what *distinguishes* the two messages catches it, and
the harness earned its keep again by pointing at an assertion rather than at a line of code.

Rotation is a restart, and that is written down as a decision rather than left as a gap: live
re-reading needs a refresh loop, lease renewal and a story for in-flight work, and it would put
back exactly the availability dependency FR-5 removes.

---

## 2026-08-06 — Foundry, and the claim ADR-0011 was making
The third platform, and it cost a routing axis. `FoundryTransport` (endpoint, credential,
api-version) × the **unchanged** OpenAI dialect × `AzureRoutes`. The dialect gained nothing; the
mappers gained nothing.

`ADR-0011` claims transport × dialect × model identity is enough structure for a third vendor.
**The diff does not leave `upstreams/`**, so the claim survives its first real test — and the
architecture assertion caught the first draft, in which `AzureRoutes` had been written into the
*dialect's* package. A dialect that names a platform is one the next platform cannot reuse, so it
moved to `upstreams/foundry/`. The assertion now refuses "azure" above the platform packages, with
one stated exemption: `residency.py` names every cloud's regions on purpose, because a list that
could not name Azure's would be the per-cloud list `ADR-0012` §6 rejected.

The addressing is the part with money in it. Azure puts a **deployment** in the path — a name
chosen by whoever created the resource, saying nothing reliable about the model. If that name were
allowed to be the model name, every use case's pipeline config would embed Azure resource naming,
and pricing would break *quietly*: `FRD-403` prices by model, a deployment called `production` has
no price, and unpriced traffic is counted apart rather than as zero. Nothing would fail; the spend
figure would simply stop being complete. So the response is attributed to the model the caller
named, and `F1` is the mutation that says so.

Two smaller decisions. **One adapter per region** rather than one adapter carrying a region:
provenance is per model, and flattening a fleet would put a residency claim on a row the request
did not satisfy — worse than recording none. And `headers()` became **async**, so an Entra token
can be minted and refreshed rather than read once at construction; the captured version works for
an hour and then fails for the life of the process, which is a failure only a long-running
deployment ever sees.

Not verified against a real subscription — there is none here, and saying so is the honest half of
"done". 18 hermetic tests, mutations `F1`–`F6`.

---

## 2026-08-06 — 174 edge cases against the running API, and four defects
A sweep of everything a caller can get wrong: malformed bodies, unusual text, every shape of bad
credential, impossible options, attachments that are not what they claim, both surfaces' error
vocabularies, wrong HTTP methods, a burst of fifty bad requests at once. Each case asserts three
things rather than one — **never a 500**, a status a caller can act on, and a message that *names*
the problem. The third is the half most suites skip, and it is what "understandable" means in
practice: "validation failed" is a correct answer and a useless one.

Four defects, all reaching a deployed gateway, none visible to a suite that only sends requests it
already believes in.

**A malformed body became a 500 on the KIRA surface.** Its `details` array is pydantic's
`errors()`, and whenever a *custom* validator raised — ours does, for "a part carries either text
or data" — that list carried the original `ValueError` **object** in `ctx`. Not JSON serialisable,
so rendering the refusal raised, and the framework turned the caller's mistake into our error, on
the one surface whose contract *is* its error shape.

**The same surface could not render a shared control's refusal at all.** `api/serving` is
deliberately surface-agnostic and raises its own error type; the KIRA renderer had no branch for
it, so every one of those refusals fell through the catch-all and became a 500. A control that
works but cannot be *reported* on one of the surfaces it protects.

**A non-positive output cap was accepted.** `maxOutputTokens: -1` returned 200 — and `words[:limit]`
with a negative limit does not mean "no limit", it drops the end of the answer. A truncated
response, a 200, and no explanation.

**A request that asks nothing was served and billed.** `parts: []` → 200. `FRD-113` FR-7 already
refuses an empty *embedding* input and names the reason — it prevents a class of accidental no-op
billing — and the argument had simply never been applied to generation.

Plus a consistency finding: an unroutable path answered with the framework's own
`{"detail": "Not Found"}`, a different shape from every other error the same API produces, handed
to the caller least equipped to deal with one. Each surface now renders routing errors in its own
envelope.

### And one thing the harness would not let me claim

`X3` — "a validation detail carries nothing unserialisable" — was written as a mutation and never
went red. The reason is that the fix is **doubly enforced**: a flag on `errors()` *and* a
comprehension that copies two named fields, either sufficient alone. No single-line edit reproduces
the 500. So it was removed rather than kept, and the harness's notes gained the rule: a property
guarded twice cannot be expressed as a mutation, and that is not a reason to weaken the guard.

The other two survivors (`X4`, and `T10`/`E8` before them) were the same too-narrow test selection
for the third and fourth time. That warning has earned a concrete rule now: **name the files whose
tests you expect to fail, not the file the code lives beside.** They are unrelated, and the second
is the one that comes to mind.

18 hermetic tests hold the four defects, because a defect found at the outer layer belongs in the
innermost one that can hold it.

---

## 2026-08-06 — fallback, limits, retention and KIRA, against the running thing
Eleven more live cases (`tests/integration/test_controls_live.py`), and the fixture for the first
group is worth stating: **two named servers against one endpoint**, `gpu-a` offering a model that
is not pulled and `gpu-b` offering one that is. `gpu-a` therefore returns a real 404 over a real
socket, so the chain crosses two adapters and two transports to reach an answer. It is the closest
a single machine gets to a second one, and it exercises the part that matters.

What that showed: a dead candidate is passed over and the next one answers; the audit keeps
`requested_model` beside `model` with `model_selection = fallback:1`, and the **provenance follows
the model that answered** — `gpu-b`, not the server that failed. Without that pair, "why did the
spend on that box triple" has no answer in any report.

Retention runs as what it is — a `docker exec` into the separate process, not an in-thread call —
and the property is the one the feature turns on: the **content** expires and the **evidence**
does not. The payloads become NULL, the row and its token counts stay. The other half is tested
too, because a pruner that cannot tell "expired" from "recent" deletes everything the first time it
runs and nothing about the run says so.

The KIRA surface reaches the same real model through an integer id, in the predecessor's shape,
with `Deprecation` on the response — and `test_both_surfaces_record_the_same_request_the_same_way`
sends one request through each and compares what the audit kept. Same outcome, model, provider,
tokens, use case, credential. That is the only way to know the shared controls were *run* rather
than merely present. A KIRA caller meets the same budget and gets the 429 in the predecessor's
vocabulary (`EXTERNAL_KI_API_TOO_MANY_REQUEST`), which is exactly what a compatibility surface
should do: same control, its own words.

**Two mistakes of mine, each made more than once, worth recording because they are the failure
modes of this *kind* of test rather than of this system.**

A helper asked `/v1beta/models` without a credential, got a 401, read it as "nothing is
registered", and skipped the suite — silently. A skip that fires for the wrong reason is worse than
a failure, because the summary line reads the same whether the system works or the test never
looked. Both helpers assert on the status now instead of shrugging at it.

And three separate times I read the audit table too early. The write is deliberately off the
request path, so a test that sends two requests and then queries once will sometimes see one row.
Every one of those failures reads exactly like a **lost audit row** — one of the most serious
things this system could do — which is precisely why the imitation is intolerable. The helper now
waits for the number of rows the test actually expects, which is the only version that cannot lie
in either direction.

A third, smaller one: a test asserted `provider == "ollama"`. It went red the moment the servers
were renamed for the fallback fixture, because it was asserting somebody's `.env` rather than the
system's behaviour. What matters is *that* a machine is identified.

**A fourth test mistake, and the most instructive.** The full integration run — which finishes
long after a file run does — failed on the streaming case that passed in isolation. The helper
looked up its audit row by **model** and not by use case, which seemed sufficient because each test
has its own use case and cleans up after itself.

It is not sufficient, for a reason that is the system working correctly. The audit writer runs
beside the request path, so it can flush a row *after* the fixture teardown has deleted rows. The
row survives as an orphan — 493 of them in this database — and the next test reads it as its own.
Those orphans are **right**: an audit row must not vanish because somebody deleted a use case, and
that is `ADR-0013`'s whole point. The test had assumed the opposite of a deliberate property.

All four of this session's test mistakes had the same shape: **a test failure that looks exactly
like a system failure**. For a missing audit row that is the most expensive confusion this project
can have, which is why they are written down rather than quietly fixed. Scoping the query to the
test's own use case also cut the suite from 11 minutes to 94 seconds — the old version spent its
time waiting for rows that were never going to be its own.

**173 mutations, all defended.** The nine added this round (`O1`–`O8`, `B8`) were caught on their
first run — and one *older* entry surfaced as undefended: `B3`, "unknown cost is counted apart, not
summed as zero", whose anchor had been absorbed into the new upsert. Repointed at the line that now
carries the rule and shown to fail before being accepted, because an entry that has never been red
claims a protection it has not demonstrated. That is the second time this session an anchor moved
with a refactor; it is the harness's most common false report and the reason it names them rather
than skipping.

---

## 2026-08-06 — the first real requests, and three defects
Ollama attached as **systems, plural** — `AIRA_OPENAI_SERVERS` takes a list of named servers, each
with its own URL, models and region, because a self-hosted fleet is several machines and "which box
served this request" is exactly what an audit exists to answer. Every server's name reaches the
audit row as the provider; with one endpoint setting they would all have logged as `ollama`.

Then a live suite (`tests/integration/test_governed_path.py`): a real API key bound to a real use
case, real HTTP through the deployed gateway, and the database read afterwards. Fourteen cases —
served-and-stored, payloads-off, budgets, budget exhaustion, refusals recorded, the tenant
boundary, revocation, and concurrency. It found three things.

**1. A model name may contain a colon.** `model:method` was split at the *first* one, which was
correct for as long as Google was the only vendor. A self-hosted model is called `qwen3:0.6b`, so
the split produced the model `qwen3` and the method `0.6b:generateContent`, and the answer was
**"Model 'qwen3' not found"** — a message naming a model nobody asked for, pointing at the catalog
instead of at the parser. The verb never contains a colon and the model may, so it splits from the
right.

**2. A comment claimed a rule the system did not have.** `build_openai_upstreams` said a locally
declared region was "recorded, not checked" — and the first real request came back *"runs in
'on-premises', and this request may only be processed in [...]"*, because `RegionAllowed` quite
correctly checks every model that declares one. The comment described an intention; the code had a
rule; the rule was right. So a server now declares **no** region unless the operator names one —
no claim, nothing to enforce, a laptop keeps working — and naming one opts in to both the evidence
and the check, which happens **at startup** rather than as a 400 on every request.

**3. The budget counter was racy in two ways, and one of them was silent.** Twenty concurrent
requests against a fresh budget produced two **500s**: `record` read the counter, inserted it when
absent, and committed, so two requests arriving as the *first* of a period both inserted and one
lost on the primary key — a 500 for a request that had already been served and charged for.

The quieter half has no error at all. `record.tokens += n` reads the loaded value and writes an
**absolute** one, so two overlapping writes discard an increment. The counter that is supposed to
be the system of record drifts *below* the truth, in the direction that spends money, under exactly
the load that makes a budget matter. Both are closed by moving the arithmetic into an upsert, where
the row is locked for the statement — dialect-dispatched, because `ON CONFLICT` is spelled the same
by Postgres and SQLite and by nobody else.

Two hermetic tests were written for it and **both were shown to fail against the old code** before
the fix went in; `B8` is the mutation. Worth noting what this says about the layers: 955 hermetic
tests, 164 mutations and a 96% coverage gate all passed over this defect for months, because a
single-threaded SQLite suite cannot express "two requests at once" and the mock never produced one.

Still open: the model blobs come from `*.r2.cloudflarestorage.com`, which the sandbox denies, so
`FRD-111` FR-6 and `FRD-112` FR-6 remain unanswered against a real model. Everything up to the
upstream call is now exercised end to end.

---

## 2026-08-06 — a real model in the stack (FRD-123)
The mock agrees with us by construction: it reports the token counts we tell it to, truncates when
we say so, and produces documents matching the schema because the same person wrote both sides. A
green suite against it proves the gateway is *self-consistent* — which is the failure the mutation
harness exists to warn about, one level up.

So Ollama joins the stack behind a `verify` Compose profile. **Built as the OpenAI dialect, not
against Ollama's native API**, and that is the whole reason it was worth doing now: `ADR-0011`
already said the OpenAI wire format arrives regardless of `FRD-106`, because `FRD-120` (Azure
OpenAI) needs it. Building against the native API would have been a fourth dialect serving only us.
This way `FRD-120` shrinks to a transport, and the deferred OpenAI *surface* gets cheaper too.

The dialect turned out to have its own version of a trap the other two already taught us. Anthropic
splits usage across two events, so a last-event-wins mapper reported zero input tokens for every
stream. Here, **usage arrives in a final chunk with an empty `choices` array** — a mapper indexing
`choices[0]` loses it — and the vendor reports no usage on a stream *at all* unless
`stream_options.include_usage` is sent. A stream that reports no usage is *released* rather than
settled (`FRD-405`), so forgetting that one field would have made every streamed request silently
free. Both are pinned.

`FRD-111` §5.2 predicted the other one before this dialect existed: the vendor takes an abstract
`reasoning_effort` and **no token budget at all**, so a `limited` request has no faithful mapping.
It is refused rather than rounded — rounding 20 000 tokens to "high" spends a different amount than
was asked for and nothing about the answer would show it.

### The architecture assertion did its job

`test_no_code_above_the_adapters_knows_the_vendor` failed, because the new dialect imported
`to_json_schema` from the Anthropic one. The lazy fix is to widen the test's allow-list. The right
one is that the translation was never Anthropic-specific — it is canonical → JSON Schema, two of
the three dialects want it, and it now lives in `core/schema.py`. A dialect importing from another
dialect is exactly how "the canonical core is provider-agnostic" quietly stops being true.

### What is *not* verified yet, and why that is written here

The container runs; the model registry (`registry.ollama.ai`) is denied by this sandbox's default
network policy, and so is the Hugging Face fallback. So the adapter is complete and hermetically
tested (38 tests) and **the two questions it exists to answer are still open**: whether thinking
and structured output are reachable through the compatibility surface, and where thinking tokens
are counted (`FRD-111` FR-6). The catalog seed therefore declares **neither capability** — absence
of information is not permission, and declaring one on a guess is the single thing `FRD-114` says
the catalog must never do.

Five integration tests are written and skip with a reason naming `make verify-up`. The first is the
one that motivated this: send a request with a marker in it, then assert the marker is in the
stored `request_payload`. `FRD-103` has always claimed the prompts are stored, and every test that
checked it compared our own bytes with our own bytes.

Prices for local models are **invented, and say so in their own display name** — a local model
costs nothing, an invented price is what makes `FRD-403` demonstrable end to end, and the
distinction has to survive being pasted into a report, so it lives in the data rather than in a
comment.

---

## 2026-08-06 — Stufe 5+6: thinking, structured output, embedding options
`FRD-111`, `FRD-112`, `FRD-113` — and, in the same change, `FRD-107` **Stage B**, because building
a capability and then continuing to refuse it at the compatibility surface helps nobody. The KIRA
wire format did not move; the fields Stage A refused by name are simply served.

**Thinking** is the one with money in it. Budgets reach 32 768 tokens, billed as output, which is
an order of magnitude more than a typical answer — so the resolution and the reservation have to
produce the *same number*, and they do: resolved after routing against the model that will serve
the request, then handed to `enforce_pre_dispatch` as `extra_tokens`. `None` and `disabled` stayed
distinct on purpose: the first means the model was never going to think, the second means it
*would have* and this request is switching it off, and collapsing them lets a declared default
quietly win over a caller who asked for none.

**Structured output** turned out to be the clearest case of `ADR-0011` rule 3. One flag,
`structured_output`, over three unrelated mechanisms: Gemini has a schema parameter, Anthropic has
none and needs a forced tool call read back out of a `tool_use` block, Azure has a third. The flag
says *whether*; the dialect owns *how*. The schema itself is parsed rather than passed through, so
an unknown field is an error **naming the field** — and then forwarded, never executed, because
re-validating would mean running caller-supplied regexes over provider output on the hot path,
which is the exposure `ADR-0007` already refused by a different door.

§5.3 is the part that justifies the design and it is the test that had to be written to fail first:
with a fallback chain, checking the capability against the model the *caller named* protects
nothing. The primary declares it, the primary fails, the fallback answers in prose, and a caller
calls `JSON.parse` on it — surfacing days later as a bug in somebody else's code.

**Embedding** carried a control bypass. `FRD-405`'s bucket took one token per request, so a batch
of 500 admitted as one request would have turned a limit of 10 per minute into 5 000 texts per
minute: intact on paper, gone in practice. The bucket now takes a `cost`, in the same all-or-
nothing Lua pass, and the budget books n requests. A batch too large for the bucket's *capacity* is
refused with a message naming which of the two said no, rather than a `Retry-After` that would
still be wrong an hour later.

### Three things the tests found

The suite caught a **regression in my own design**: the plan had the predecessor's default task
type filled in by the mapper, which meant every embedding against a model nobody had declared task
types for was refused as though the caller had asked for something impossible. The default is a
*surface's*, applied only where the model declares it — and an explicit undeclared type is still
refused. Naming a type we cannot verify is a request; naming none is not.

`check_declaration` compared `method == "embedContent"`, so the new batch verb demanded the
*generation* capability — refusing every batch against an embedding-only model and accepting one
against a model that cannot embed at all. The same shape as the `:embedContent` bypass, one verb
later, and now a `frozenset` for exactly that reason.

The mock never truncated a schema-constrained document, so FR-6 — refuse an incomplete document
rather than return it as data — was exercised by nothing. A mock that always finishes cleanly is a
mock that makes a check look tested.

Eleven test doubles implemented the old `embed(model, text)` signature. They were widened rather
than left permissive: a stand-in more permissive than the thing it replaces is how a real defect
hid behind a green suite here before.

### And two the mutation harness produced

Fourteen mutations survived the first run. Nine were **anchors that had moved with the refactor** —
a mutation whose anchor no longer applies protects nothing, which is why the harness reports one
rather than skipping it. Repairing them is not bookkeeping: `M1`, `M2` and `M7` all describe the
rate limiter, and all three had quietly stopped being checked the moment the bucket learned to take
a cost.

Two were real, and both are worth stating:

**`C4` survived because the rule was enforced twice.** "A model that declares no embedding refuses
one before dispatch" lived in `check_declaration` *and* in `embedding.validate`, so removing either
changed nothing observable. That is what redundancy looks like from the outside, and it is a defect
in the making — two places deciding one rule drift, and the one that drifts is whichever is not
under test. The duplicate is gone; `validate` owns it.

**Two survived only because their test selection was too narrow** (`T10`, `E8`). The harness's own
docstring already warns about this and it has now cost time twice, so the warning has earned a
second sentence.

Mutations **T5–T10, S1–S7, E1–E8** — 21 new, 164 total, all defended. 896 hermetic tests, 96% coverage.

**Owed, and said rather than assumed:** `FRD-112`'s audit digest (the function exists and is
tested; the column needs a migration) and `FRD-111` FR-6's verification against a real upstream —
whether the provider folds thinking into reported output tokens or reports it apart is not
knowable hermetically, and the recorded cost is understated if we guessed.

---

## 2026-08-06 — Stufe 4: the predecessor's contract, served by AIRA
`FRD-107` Stage A. `/kira/api/external` with `chat`, `streaming-chat`, `embed`, `models`, `health`,
`version-info` and `ki-usage`; the predecessor's error envelope and codes; integer model ids;
attribution; deprecation headers on every response including the refusals.

**Stage A carries documents.** The plan had attachments in Stage B, `FRD-110` landed first, and
refusing a capability we have would be silly. Only `thinking` and `responseSchema` are refused —
plus one case the FRD singled out that turned out to matter: **a model whose catalog declares a
non-`disabled` default thinking mode is refused**, because the predecessor *applies* that default.
Serving such a model with no thinking at all would answer differently for a reason nobody could
see, which is the same failure as a dropped attachment one level up. A model with no thinking
declaration, or one whose default is `disabled`, is unaffected — sending nothing is what it asked
for.

`/embed` refuses a list and a `task_type` by name rather than approximating: embedding a batch one
at a time would silently cost N requests of quota against a limit of one, and the wrong task type
produces vectors that retrieve measurably worse with nothing in the response to show it.

**The real work was the extraction.** §5.1 says the surface shares the pre-dispatch gate, the
pipeline, the dispatch chain and the audit writer. Sharing them means extracting them, so they now
live in `api/serving.py` outside any surface and both routers use it. The alternative — a second
copy — is the `:embedContent` failure in a larger costume: a control that lives inside one branch
instead of on the path every branch takes, except the branch is now a whole API.

What holds that is one test: send a request through each surface, compare the audit rows. Same
outcome, same model, same tokens, same latency recorded, same degradation snapshot. It is the only
way to be sure no step was *skipped* rather than merely present.

Seven existing mutation anchors followed their functions into the new module and were repaired.
A mutation whose anchor has moved protects nothing, which is exactly why the harness reports a
missing anchor instead of skipping it — one of them (`M23`, "every verb passes the pre-dispatch
controls") also had to have its *text* corrected, and it briefly survived until it did.

Not in Stage A, and said rather than approximated: `ki-usage` reports per **user** with a model id
of `0`. The predecessor keys usage by (user, model); `FRD-601` aggregates the two separately, and
inventing a cross-tabulation would be a fabricated figure.

143/143 mutations, 766 hermetic tests, 78 integration, 46 browser.

---

## 2026-08-06 — Stufe 3: documents, and the rule that a refusal beats a fluent wrong answer
`FRD-110`. `CanonicalMessage` carries ordered parts; the Gemini surface takes `inlineData`; both
dialects map it; the mock sees it; the reservation counts it; the audit row keeps a description.

The owner stated the requirement in one sentence and it is the one everything here serves:
**if the model cannot read the document, throw an error — do not try anyway, or the model
hallucinates and the user thinks something else is broken.** That is exactly right, and it is worth
spelling out why it is not merely tidy: a dropped attachment produces *no error*. It produces a
fluent, confident answer about a document the model never saw, returned with a 200, and the caller
reports that "the model is hallucinating" and looks for the fault everywhere except where it is.

So a model that cannot read what was sent is refused **by name**, with the types it lacks, and the
message distinguishes *undeclared* (a catalog gap somebody closes in a minute) from *declares no
attachment support* (a fact about the model). Checked after routing at every hop, on the mechanism
built for exactly this last commit.

Four decisions worth keeping visible:

- **`text=` still constructs and `.text` still reads.** The whole existing suite passed unmodified
  against the reshaped model — which is what turned a change that "reaches everything" into a
  change to one file. The care needed is elsewhere: `.text` was total and is now **lossy**, so the
  injection filter and the routing classifier see the prompt and not the document. That blind spot
  is a property with a test rather than a comment.
- **Stripping is not redaction.** Attachment bytes are removed before the redactor runs, and
  unconditionally, because a deployment that swaps the redactor must not be able to turn it off.
- **The mock sees attachments.** One that ignored them would let every hermetic test pass while the
  real path was broken, and the feature would be exercised only against a cloud nobody has in CI.
- **Embedding refuses an attachment** rather than embedding the prompt without it — the same rule
  one level down (`FRD-113`: chunking a document is the consumer's decision).

**And then the integration layer earned its keep again.** Running the suite repeatedly to check for
flakiness turned up a failure at roughly one in eight: a client dropping the socket mid-stream
sometimes **vanished from the audit log**.

Nothing to do with documents. Closing a generator from inside the process raises `GeneratorExit`,
and awaits in a `finally` run normally — which is why the hermetic disconnect test passes
deterministically and has since the day it was written. A real socket dropping **cancels the
response task**, and a bare `await` in that `finally` re-raises `CancelledError` at its first
suspension point: the settle and the row were simply lost. `FRD-405` B4 promised this path is
accounted for. It was — in-process only.

Shielded now, and verified over 15 consecutive runs after rebuilding the container (the first
"fix" appeared not to work because the container was still on the old image, which is its own
small lesson). Deliberately given **no** mutation entry: no hermetic test can distinguish the
shielded version from the unshielded one, so an entry would be a false claim, and a harness that
makes one is worse than no harness. `tools/mutation_check.py` now says that in its own docstring,
and the integration test carries the explanation so nobody re-runs the flake away.

135/135 mutations, 738 hermetic tests, 73 integration, 46 browser.

---

## 2026-08-06 — The fallback chain learns to say no
A question from the owner — *is the region set for the whole gateway, or can it be bound to a use
case?* — turned into a smaller and more urgent finding than the one it asked about.

**The honest answer to the question:** deployment-wide. The allow-list is global and the region is
a property of the model; a use case can only influence it indirectly, through `allow_check`, which
it configures itself and can therefore widen. That is self-service, not governance. Per-use-case
residency is a real requirement and a **governance extension** — it is not built, and it should be
its own FRD rather than smuggled in here.

**What was actually broken** is one level down: `dispatch_with_fallback` had no notion of a
condition at all. It tried candidates in order and returned the first success. Nothing could
express "not that one", so nothing could enforce it — not residency, and not the attachment rule
`ADR-0012` §3 already states for documents.

So the mechanism is built now, before the feature that needs it: the chain takes conditions, a
candidate that fails one is **skipped with its reason kept**, and an exhausted chain fails. The
reasons are on the audit row, which is what somebody actually needs when they ask why an answer
came from the model it did.

Two present-day defects fell out of it:

- **A model no provider serves was a silent `continue`.** A typo in a fallback chain was
  invisible: the chain simply behaved as though the entry were not there, and nothing said so.
- **An exhausted chain raised `UpstreamError`**, which the route mapped to a **502**. So a
  configuration mistake read as "the provider is down" and sent whoever looked at it to the wrong
  place. It is now `NoCapableModel` → **400 FAILED_PRECONDITION**, naming each candidate and why it
  was excluded. `Outcome.NO_CAPABLE_MODEL` — declared in `FRD-122` and until now unreachable —
  finally has a producer.

Residency is the first condition. It cannot refuse anything a correctly configured gateway offers
today, since every model was already checked against the allow-list at startup — and it is built
anyway, because the *per-hop* check is the part that would otherwise be got wrong when residency
becomes a per-use-case property. Media types (`FRD-110`) and the schema capability (`FRD-112`) plug
into the same mechanism.

One existing test had to change, and the change is the point: it asserted that an exhausted chain
raises `UpstreamError` — it encoded the misleading behaviour. It now pins both halves: a chain with
nothing to offer is not an outage, and an upstream that *was* tried and failed still is.

**A follow-up question found the real version of the same mistake.** *"Wird es auch für Azure
`westeurope` funktionieren?"* — and the honest answer was: the mechanism yes, the configuration no.
`RegionAllowed` was always generic (it reads whatever the adapter declares), but the allow-list sat
behind a **`vertex_`-named setting with Google-only defaults**. The first Azure model would have
failed a check named after Google, and an operator widening `AIRA_VERTEX_ALLOWED_REGIONS` to admit
`westeurope` would have had a setting named after one cloud governing two.

`ADR-0012` §6 had already decided "one allowed-region list across every transport". The
implementation had quietly not done that. Moved to `aira_gateway.residency` with
`AIRA_ALLOWED_REGIONS`, and the default now covers the EU regions of **both** clouds — Azure's
listed before Foundry exists, on purpose, because the alternative is learning that a policy list was
written for one cloud by watching the first model of the other be refused.

The names stay flat (`eu`, `europe-west1`, `westeurope`) rather than qualified per provider: they do
not collide, and an operator thinks in "which EU regions may we use", not in a matrix.

124/124 mutations, 714 hermetic tests, 69 integration, 46 browser.

---

## 2026-08-06 — Stufe 2: the EU, and the first vendor that does not speak Google
`FRD-115` and `FRD-119`. One `VertexTransport` — URL, OAuth, region, error mapping — with two
dialects above it: Gemini bodies unchanged from `FRD-304`, and the Anthropic Messages API.

**Residency is enforced rather than intended.** A configuration that *can* express a non-EU region
is a configuration in which somebody eventually adds one, because that is where a preview model
launched, and nothing objects. So the allowed regions are a list, a model outside it makes the
gateway **refuse to start**, and provider/publisher/region land on every audit row. "The
configuration says EU" is a claim; "this request went to `eu`" is evidence, and `FRD-601` can now
break spend down by it.

**An ambiguous routing table is a startup failure.** `ProviderRegistry` assigned by iteration, so
the last provider registered silently won. With one adapter that was harmless. With three —
Generative Language, Vertex Gemini, Vertex Anthropic — it becomes a silent decision about which
region and which credential handled a request, invisible in every log and every report.

**The token holder is shared, not Google's.** Cache, refresh at 80% of lifetime, collapse
concurrent refreshes into one, keep serving a valid token through a failed refresh, back off rather
than retry every request. Identical for Vertex, Foundry and a static key; only the acquisition
differs (`ADR-0011` rule 1). Written once because getting that race right per platform means
getting it wrong on the second one.

Anthropic's differences are each a mapping: `max_tokens` **required** (always sent, from the
catalog's per-model default), the system prompt as a top-level parameter with several messages
concatenated rather than reduced to the last, cache tokens counted as input because they *were*
input, streamed usage **accumulated** across `message_start` and `message_delta` where Gemini puts
everything in the last chunk — and **thinking blocks dropped**, which with Gemini was free (we
simply never ask) and here is an active obligation.

**The architecture assertion is now a test.** `FRD-100` has claimed since Phase 1 that the
canonical core is provider-agnostic, and until today "two upstreams" meant two spellings of
Google's format — the claim had never been tested.
`test_no_code_above_the_adapters_knows_the_vendor` parses every module outside `upstreams/vertex/`,
strips docstrings, and fails if a vendor name appears in code. **It passes.** What did change
outside `upstreams/` changed for FR-6 and FR-10 — refusing an ambiguous table, recording where each
request went — which are platform requirements, not the dialect leaking.

**One mutation survived, and it is the one worth writing down.** `V4` guards "the model's reasoning
never reaches the caller". The test put the reasoning in the vendor's own `thinking` field — so
removing the block-type filter changed nothing, because the *field name* differed too. It passed
for a reason that had nothing to do with the property it was named after. Rewritten to put the
reasoning in a `text` field, which is what actually holds the selection to being by **block type**;
a second test pins that an unknown future block type is dropped too.

The integration layer also caught a race it had always had: the test asserted that *its own* relay
published the event, and on a full stack the `management-relay` container usually wins. It now
asserts the outcome — the row reaches the gateway — rather than who delivered it.

Verified: 699 hermetic tests (97.1%), **116/116 mutations caught**, 69 integration tests, 239
frontend and 46 browser tests. Not built here, deliberately: the thinking, structured-output and
attachment mappings, because the canonical core does not carry those fields yet and a mapper for a
field that does not exist is a guess.

---

## 2026-08-06 — Stufe 1: the model catalog becomes a runtime authority
`FRD-114`. The catalog held prices; it now holds what a model may be *asked to do*, and the gateway
decides from it. One shared vocabulary in `aira_common.models` — two copies of "which capabilities
exist" would drift, and the drift would surface in whichever plane was not tested.

The rule everything turns on is **undeclared means the baseline and nothing more.** The tempting
default is the opposite: let an undeclared model accept everything and let the provider complain.
That is wrong for the same reason "unpriced is not free" is wrong — absence of information is not
permission, and an undeclared model would otherwise accept a 32 768-token thinking budget the
pre-dispatch reservation has nothing to estimate against.

Management validates a declaration **where it is written**, because the catalog is a runtime
authority and a self-contradictory declaration would otherwise be discovered as a vendor error on
every request against that model. The rule with teeth: a thinking maximum at or above the output
cap describes a model that could never answer, since Anthropic draws thinking tokens from
`max_tokens`. A PATCH is merged over the row before validating, or a change to `max_output_tokens`
alone would be checked against a thinking block it cannot see — each half valid, the row not.

Enforced today: the output cap, the per-model **default** cap (which sharpens the reservation for
every vendor, not just the one that requires it), `generate`/`embed`, and a deprecation `Warning`
header. Deprecation **warns**; revocation blocks. Conflating the two removes the ability to
announce a retirement before performing one.

**`model_prices` is now `model_catalog`.** A table that decides whether a thinking budget is
accepted must not be called *prices*. That cost four raw-SQL integration tests an update, which is
what a rename costs — and it turned up something that had nothing to do with this FRD:

> During the rolling rebuild, the **consumer** container was still on the old image, and its
> `create_all` **recreated `model_prices`** — then failed every model event against a table Alembic
> had renamed. Nothing crashed. The declarations simply never arrived, which presents as "the
> feature does not work".

`create_all` alongside Alembic means a partially-deployed stack can undo a migration, silently.
Written up in `DEPLOYMENT.md` §6a with the upgrade procedure; the durable fix — stop calling
`create_all` outside tests — needs the demo and CI paths to build from migrations and is on the
backlog rather than smuggled into this release.

The frontend edits the flat fields with real controls and leaves the nested thinking/embedding/
attachment blocks to the FRDs that give them meaning. A bespoke editor for a feature that does not
exist yet is a guess about what it will need.

Verified: 663 hermetic tests (98.4%), **104/104 mutations caught**, 64 integration tests — including
the declaration travelling the real outbox → relay → Kafka → consumer route into the migrated
schema — 239 frontend tests and 46 browser tests. The browser suite found one of its own
assertions had become positional: it picked "the first warning badge", and there are now two.

---

## 2026-08-06 — Stufe 0: the audit trail now records what was refused
First stage of the delivery order, and the one that makes every later stage testable. `FRD-122`
implemented: `aira_gateway/audit.py` (closed `Outcome` vocabulary + the `AuditTrail` a route fills
in as it goes), migration `0012` (six nullable columns, indexed on `outcome` and `credential`), and
the recording site.

**Refusals are written at the route's exception boundary, once.** The obvious alternative is a
`record_request` beside each `return _error(...)`; there were half a dozen of those and the next
verb would add more. That is not a hypothetical concern — it is exactly how `:embedContent` came to
bypass the pre-dispatch gate, because the gate lived inside one branch instead of on the path every
branch takes. So the branches now **raise** and the boundary records.

Two things the work found that the plan did not have:

- **A full writer queue turned a correct 429 into a 500.** The test for FR-7 was written expecting
  to pass; it failed. The audit write was propagating out of the refusal path, so a client that hit
  a rate limit got a server error — and would have retried straight into the limit it had just hit.
  Guarded now, and deliberately **only** on the refusal path: on the success path a failed write
  means a *served* request went unrecorded, and failing loudly is the defensible answer to that.
- **A refusal was naming the model the caller typed, not the one attempted.** A request routed
  elsewhere by the pipeline and then refused blamed a model that was never called. Found by a
  mutation surviving (`T3`), which is the harness doing precisely what it exists for: the property
  looked covered and was not.

Also repaired the `M23` anchor — the pre-dispatch gate lost its `try/except` when refusals began
raising, so the mutation that guards "every verb passes the controls" no longer applied. A mutation
whose anchor is gone protects nothing, which is why the harness reports missing anchors rather than
skipping them.

One design point worth keeping visible: pipeline decisions are persisted through an **allow-list**,
not a deny-list. A step that starts recording the classifier's explanation would otherwise begin
persisting model output about a caller's prompt the day it is added — silently, in a column
redaction cannot process.

Verified at four layers: 620 hermetic tests (99.2% coverage), **96/96 mutations caught**, 60
integration tests against the migrated Postgres schema (asserted separately, because the hermetic
suite builds its schema with `create_all` and would pass with an empty migration), 232 frontend
tests and 46 browser tests. The Reporting screen now shows refusals beside successes, so a use case
grinding against its budget wall is a figure rather than a log search.

---

## 2026-08-06 — A delivery order, and the one place the priorities fight the dependencies
The owner set the priority: **KIRA compatibility first, then the Google and Microsoft model
connections (*easily extensible*), then document handling, then the review findings** — with the
instruction to record my findings as features so they are not forgotten. PRD gains §1.2 (seven
additional features from the code review) and §1.3 (the priority).

**Priority 1 depends on priority 3.** `FRD-107` §5.2 is explicit: a KIRA surface built before the
capabilities exist would accept fields it silently ignores, and a caller cannot tell that their
document or their thinking budget was dropped. That is worse than a refusal.

The resolution is a stage boundary rather than a wait. **`FRD-107` Stage A** ships the text contract
— chat, streaming, embed, models, health, version-info, ki-usage, the error vocabulary, the integer
model ids, attribution, the deprecation headers — and **refuses**, in the predecessor's own error
vocabulary, any request carrying a field it cannot yet honour. **Stage B** moves those fields from
refused to served with no change to the contract, because refusing was always the correct behaviour
for a field we could not serve. Every consumer sending plain text — the majority — migrates months
before the ones sending PDFs, and nobody is misled in between. The one thing Stage A must not do is
*approximate*: KIRA applies a model's default thinking when the caller sends none, so Stage A either
applies the real default or refuses; quietly sending no thinking at all would make answers differ
for reasons nobody can see.

The full order is now in ROADMAP Phase 8. Two deviations from a naive reading of the priority list,
both written down so they can be overruled rather than discovered:

- **`FRD-122` (audit) goes first, not last.** It is one of "my points" and it is also the cheapest
  item in the programme — additive columns, one recording site, no request-path change. Every stage
  after it produces traffic that ought to be evidenced, and retrofitting the audit once four vendors
  and two API surfaces are live is strictly harder than doing it while there is one of each. It also
  changes what every later test can assert.
- **Documents come after the EU connection**, not before. Without a document-capable model reachable
  in the EU, document support could only be exercised against the mock — which is not the capability
  that was asked for.

One feature named at the owner's own emphasis and worth repeating: **extensibility, as a measurable
property.** "So dass es einfach erweiterbar wäre" is a claim until something checks it, so it has a
test rather than an intention — adding a model family must not change anything above `upstreams/`
(`FRD-115` §10). If a diff does, the canonical core is vendor-shaped, and the core is what gets
fixed rather than the adapter. `FRD-120` (Foundry) is where that gets proved, which is part of why
it sits after the first two vendors rather than being deferred indefinitely.

---

## 2026-08-06 — The feature list, and what it makes visible
The owner restated what AIRA Gateway *is*, as seventeen features. It now sits in **PRD §1.1** with an
honest status column, because a list like that is only useful if the gaps are in it too.

Three things the table makes visible that prose was hiding:

**The governance features are largely built; the evidence features are not.** Budgets, limits,
routing, fallback, self-service pipelines, roles — done. Auditability, incident response, anomaly
detection, model smoke tests — missing. Those four are what make a governed system *defensible after
the fact*, which is the half you need on the day something goes wrong.

**Feature 5 is more specific than "store requests and responses".** *"Welches System wann was womit
aufgerufen hat"* — and checked against the code, the **system** is exactly the part we cannot
answer. An API key has a `prefix` (its identity) and a `subject` (the person who issued it); the
audit row records the subject and never the prefix. Five keys issued for one use case by one
administrator are one identity in the log. The consequence lands precisely where it hurts most: a
leaked key can be revoked, but the blast radius cannot be assessed — which requests came from it,
what they asked, over what period, none of it separable from its siblings' traffic. Added to
`FRD-122` as FR-5.

**Feature 3 settles `ADR-0010`.** Naming KIRA-API compatibility as a *central feature* is the
decision that ADR was waiting for. Accepted as Option C — the compatibility surface is built, with a
sunset date and its usage visible in reporting, because that is the half that keeps a compatibility
layer from becoming permanent. `FRD-107` is unblocked.

New: **`FRD-504` — model smoke tests and jailbreak batteries.** IT Security's question is not "was
this request allowed" but "does the model we approved for the whole organisation still refuse what
it should". Two design points I want to keep visible:

- **A rate, not a verdict.** Models are sampled: the same jailbreak prompt can be refused nine times
  and answered on the tenth, and *that is the finding*. A single-run boolean would show green nine
  times out of ten and the tenth would look like a flake to re-run away. So each case runs *n* times
  and the result is "3 of 20", never "failed".
- **Two modes, because the pipeline would block the test.** `FRD-300`'s injection filter exists to
  block exactly the prompts a jailbreak battery sends — run through it, most cases never reach a
  model and the run says nothing about the model. So: *through the pipeline* (does our filter catch
  it — the first honest measurement of whether that filter earns its place) and *direct to the
  model* (does the model resist it), reported side by side. The interesting cell is the one where
  the filter misses **and** the model answers.

Runs go through the gateway's own path under a dedicated internal use case, so their spend is
attributed and bounded rather than exempt — and the result page states that it is one battery on one
day and not a safety statement, for the same reason unpriced traffic is counted apart: a figure that
reads as complete when it is not causes worse decisions than an absent one.

---

## 2026-08-06 — "Auditierbares Hirn": a scope sentence, and four places we do not earn it yet
Direct model access confirmed — the Vertex publisher and endpoint APIs, not the platform's agent
surface. That closes the last open question in `FRD-115` §11 and `FRD-119` §11.

What came with it is worth more than the answer: *the gateway's job is to provide **auditable
brains** for AI use cases.* That is a scope sentence, and it settles questions that had not been
asked yet. `ADR-0013` records it with a test for future requests — **does this make model access
better governed and better evidenced, or does it make the gateway think for the use case?** The
second kind always arrives disguised as the first ("just let the gateway keep conversation history,
every team is reimplementing it"), individually reasonable and collectively turning a control point
into an application platform.

Out, explicitly: agent surfaces, retrieval and vector storage, conversation state, tool execution,
workflow orchestration, content understanding. `FRD-121`'s document conversion sits on the far side
of that line and is now marked as such — which is the reason its own recommendation is to not build
it first.

**Then I took the word "auditable" literally and reviewed against it.** Four gaps, and the first is
not small:

1. **A refused request leaves no record at all.** Rate-limited, over budget, unknown model, failed
   validation — the route returns before `record_request` is ever reached. `request_logs` therefore
   contains **what was served, not what was asked**. So "who was throttled, how often, starting
   when, and was that why the application misbehaved on Tuesday" is unanswerable from the audit
   trail, and `FRD-601`'s `failed_requests` can only ever show upstream failures — a use case
   hitting its budget wall all day reports as perfectly healthy. A control that leaves no trace
   when it fires is a control nobody can review.
2. **Only the served model is recorded, never the requested one.** With cross-vendor fallback
   (`ADR-0012`) a request asking for Gemini can be answered by Claude and nothing durable says a
   substitution happened. "Why did the Anthropic spend triple" has no answer in the data.
3. **Pipeline decisions live on a span, not on the row.** `aira.pipeline.model` is a span
   attribute — and spans are **sampled**. So *why* a model was chosen, for the one component that
   makes a judgement about a caller's prompt, is durably recorded nowhere.
4. **Degradation is global, not per-request.** `DegradationLog` says what is broken *now*; an audit
   needs what was broken *then*. A request budgeted on the racy Postgres fallback is
   indistinguishable from one with the atomic guarantee.

`FRD-122` closes all four: one recording site at the route's exception boundary (not one per
`return _error(...)` — that is the shape that let `:embedContent` bypass the pre-dispatch gate);
`requested_model` alongside `model` so existing reports and indexes keep their meaning; decisions
but **never the classifier's reasoning text**, which is model output about a caller's prompt and
inherits every question the prompt has; and the degraded set frozen onto the row.

One thing decided rather than deferred: recording refusals means a caller in a retry loop writes a
row per attempt. That is the right increase — a retry storm is *precisely* the event the audit
trail should show. If a deployment finds it excessive, the answer is a shorter retention for
refusal rows, not recording nothing.

Until `FRD-122` ships, "auditable" is a claim the data does not fully support. Worth saying plainly
in the DEVLOG rather than only in an FRD.

---

## 2026-08-06 — Four model families, and the one thing that does not generalise
Bringing Gemini Enterprise / Model Garden and Microsoft Foundry together over Gemini, Claude, GPT and
Nemotron. Two findings while thinking it through, and the second is the one that matters.

**The transport × dialect grid is a matrix, not a diagonal.** Model Garden is two things under one
name: publisher-managed models (Gemini, Claude) addressed as `publishers/{vendor}/models/{model}`,
and **self-deployed** models — NIM containers such as Nemotron — running on our own capacity,
addressed by a **numeric endpoint id** and speaking an **OpenAI-compatible** API. So the OpenAI
dialect is needed on the *Vertex* transport, not only on Foundry. `ADR-0011`'s separation starts
paying for itself before the third platform is built, and `ADR-0011` rule 2 (the caller names a
model, the catalog holds the addressing) turns out to have been necessary rather than prudent — a
fourth addressing mode arrived within a day.

Self-deployment also brings failure modes that managed models do not have, and treating them alike
would produce two surprises rather than one: an endpoint scaled to zero **cold-starts for minutes**
(a budget reservation held open that whole time, a rate-limit token already spent, a fallback chain
burning its primary timeout instead of failing over), and its **429 means no free replica**, not
quota — so retrying the same endpoint cannot help. `hosting` becomes a declared property that the
dispatch timeout, the retry decision and the readiness probe read; and the probe must **not** wake a
scaled-to-zero endpoint, or it spends GPU minutes to answer a question about availability.

**Documents are where unification would do real harm.** The predecessor's callers send PDFs — "here
is a document, answer questions about it" is a large share of what KIRA is used for. Across the four
families that capability is genuinely not uniform: **Gemini and Claude read PDFs natively, including
layout; a text-only GPT deployment and a NIM-hosted Nemotron cannot see one at all.**

The tempting behaviour is to let a fallback chain drop the attachment and carry on. It must not.
Dropping it does not produce an error — it produces a fluent, confident answer about a document the
model never saw, returned with a **200**, indistinguishable from a correct answer to everyone
including the caller. So `ADR-0012` §3: a chain **skips** a candidate that cannot read the
attachment, and **fails** if none qualifies. Failing is recoverable; being quietly wrong is not.

The practical shape is better than it sounds: Gemini and Claude are both document-capable and both
sit on the same transport and the same credential, so a document-capable chain with a genuine
fallback already exists without any conversion at all.

`ADR-0012` also fixes the governing principle for all of this, which was implicit until now:
**hide the plumbing, declare the semantics.** Clouds, credentials, URL shapes, streaming vocabularies
and structured-output mechanisms are plumbing and belong behind the canonical core. Anything that
changes *what comes back* — an attachment a model cannot see, a thinking mode it cannot honour, a
schema it cannot enforce — is declared, visible in the builder, and enforced after routing.

`FRD-121` specifies the opt-in conversion path (extract text or render pages) for the cases where
capability gating is genuinely too strict. Three constraints decided up front: never default, never
silent, and **never in the gateway process** — a PDF parser is a large attack surface on
caller-supplied bytes and this process holds the cloud credentials, so it belongs behind a managed
document service or an isolated worker. The recommendation in its own §11 is to **not build it
first**; ship the gating, run with it, and let a concrete blocked use case justify it.

`ADR-0012` and `FRD-121` written; `FRD-110` (chain homogeneity), `FRD-114` (`hosting`) and `FRD-115`
(self-deployed endpoints, the matrix) amended.

---

## 2026-08-06 — A third platform decides the shape of the second
Microsoft Foundry is wanted for the future: Azure OpenAI models and Microsoft's own. Not urgent —
and precisely because it is not urgent, it is the right moment to let it settle the upstream
architecture, since it is the third vendor and the third is where the abstraction is decided. With
two you can always absorb the difference in a conditional.

Foundry brings three things Vertex did not: the **OpenAI Chat Completions** wire format, **Entra ID**
authentication, and a different notion of what a model *is* — Azure addresses a customer-named
**deployment** in a resource in a region, and the same deployment name in two resources can be two
different models.

`ADR-0011` records the resulting shape: **transport × dialect × model identity.** A transport owns
reaching the vendor's cloud (endpoint, credential, retries, quota errors); a dialect owns the API
shape (bodies, streaming events, usage, capability mechanisms); an upstream composes them. Vertex is
one transport with two dialects; Foundry is one transport with the OpenAI dialect — which is then
reusable by any platform that speaks it, and that reusability is most of the justification.

Three rules came out of it, each fixing something that would otherwise have been decided by
accident:

- **Credential acquisition is one abstraction.** All three platforms need identical behaviour —
  cache, refresh ahead of expiry, single-flight, serve through a failed refresh — and differ only in
  how the token is obtained. `FRD-115`'s token holder becomes a shared `TokenSource`. Writing that
  refresh race three times means getting it right three times, and the second one is always the one
  that is subtly wrong.
- **A caller names a model; the platform's addressing is catalog configuration.** No use case's
  pipeline config may contain an Azure deployment name. The failure mode if we got this wrong is
  the interesting part: `FRD-403` prices by model name, a deployment called `production` has no
  price, and unpriced traffic is *counted apart rather than as zero* — so the spend figures would
  not break, they would quietly stop being complete.
- **Capability flags say whether, never how.** Three vendors, three unrelated structured-output
  mechanisms (a schema parameter, a forced tool call, a `json_schema` response format) and two
  reasoning shapes (token budget, effort level). The flag stays a boolean; the mechanism lives in
  the dialect.

Two pleasant confirmations. `FRD-111`'s canonical thinking model — `mode` + optional `tokens`, taken
from the *predecessor's* vocabulary — turns out to cover Azure's `reasoning_effort` levels, a vendor
it was not written for. And Azure reports reasoning tokens separately, which finally answers
`FRD-111` FR-6's open verification for at least one vendor.

One planning consequence: the **OpenAI wire format now arrives as an upstream whether or not
`FRD-106` is ever built.** Once canonical ⇄ OpenAI exists in one direction, the deferred OpenAI
*inbound* surface is largely that mapping reversed plus a router. The decision to defer it stands;
the estimate behind that decision does not, and should be revisited when it next comes up.

`ADR-0011` and `FRD-120` written; `FRD-114` (model identity and addressing), `FRD-115` (shared
token source, region allow-list generalised), `FRD-111` and `FRD-112` (the third mechanism) amended.

---

## 2026-08-06 — Model Garden answers one question and opens another
Two facts landed after the parity FRDs were written, and both change them.

**EU residency applies.** `FRD-115` moves from "worth doing" to required: our current adapter calls
a global endpoint and cannot make a residency statement, so it is not a production candidate no
matter how complete the rest becomes. The FRD now also *enforces* it — an allowed-region list, a
model configured outside it refuses to start, and provider, publisher and region recorded on every
audit row. Configuration alone would not hold: someone adds a model in `us-central1` because that
is where a preview launched, and nothing objects.

**Access is through the Gemini Enterprise platform's Model Garden — Gemini *and* Anthropic**, one
project, one credential. That is a governance win and a technical complication, because the two
vendors do not share a wire format. Anthropic models on Vertex are called through `:rawPredict` and
speak the Anthropic Messages API:

- `max_tokens` is **required**, and our canonical field is optional. A caller who omits it — most
  of them, since it is optional today — would get a vendor error about a field they never set. So
  `FRD-114` gains a **per-model default output cap**, which sharpens the budget reservation for
  both vendors anyway.
- **Thinking blocks come back in the response.** With Gemini, "we do not return chain-of-thought"
  was cheap: we simply do not ask. With Anthropic it becomes an active obligation — the adapter
  must drop them, and they must reach no response, log, span or audit row. A mapper that
  concatenates all content blocks is the obvious implementation and the wrong one, so it gets its
  own test. Their token *count* still reaches usage, because they were billed.
- Anthropic's thinking budget is drawn from `max_tokens`, so `budget < max_tokens` becomes a
  validation rule and the catalog must refuse to hold a combination that cannot work.
- **There is no `responseSchema`.** Structured output is a forced tool call — one tool whose
  `input_schema` is the caller's schema. So `FRD-114`'s `structured_output` flag means "by some
  mechanism", the adapter refuses schema fields it cannot express faithfully rather than dropping
  them, and `FRD-112` §5.3's post-routing capability check stops being defensive and becomes
  load-bearing.
- **No embeddings at all**, so `FRD-113` is Gemini-only and the capability declaration is what
  enforces it — before dispatch, not by an adapter raising deep in the stack.

New `FRD-119` for the dialect; `FRD-115` rewritten as the *platform* (transport, OAuth, region,
registry) with the two dialects above it. The seam matters: put authentication in the adapters and
it is written twice, put body mapping in the transport and a third vendor rewrites it. `FRD-110`'s
media-type allow-list becomes an intersection of what AIRA accepts and what the target model
accepts, checked after routing for the same reason the schema capability is.

This is also the first honest test of `FRD-100`'s claim that the canonical core is
provider-agnostic — until now "two upstreams" meant two spellings of Google's format. `FRD-115` §10
carries an architecture assertion for it: if the diff reaches outside `upstreams/`, the core is
Gemini-shaped and we should fix the core rather than smuggle a vendor field through it.

One question deliberately left open in `FRD-115` §11: whether "Gemini Enterprise" here means Model
Garden *raw model access* (assumed throughout) or the agent platform's own API, which is not a
model API and would model grounding and server-side conversation state that our canonical core does
not have. One authenticated `curl` against the project's `publishers/anthropic` endpoint settles
it, and getting it wrong is a rewrite rather than a correction.

---

## 2026-08-06 — KIRA parity: the programme, and where the gap actually is
The predecessor's requirements (`kira_api.md`, KIA-KIRA-API v0.1.2) arrived with the instruction
that AIRA must carry all of them. Reviewed against the code rather than against our own
documentation, the result was not what the phase history would suggest.

**In breadth we are well ahead.** Use cases with object RBAC, self-service keys, budgets down to
spend, cross-instance rate limits, the pipeline, Kafka config distribution, the management UI,
retention, cost reporting — the predecessor has none of it.

**In the core request path we are behind, and further than it looks.** `CanonicalMessage` carries
exactly one field, `text: str`, and the Gemini surface's `Part` requires `text` — so a request with
`inlineData` is not merely unmapped, it is **rejected with a 400**. The predecessor accepts
documents and images in fourteen MIME types, controls the thinking budget, and forces JSON output
against a schema. None of that exists here. Its embedding path takes eight task types, batches and
two dimensionalities; ours takes one string.

Two findings I had not expected to matter as much as they do:

- **Vertex AI, not the Generative Language API.** The predecessor calls `europe-west1` and the
  `eu` multi-region with a service account. We call the global endpoint with an API key. If a data
  residency requirement sits behind that configuration — and an `eu` endpoint in a production file
  is decent evidence — then no amount of feature parity makes our adapter a replacement.
  `FRD-115`, and it may be the most schedule-critical item in the programme.
- **Vault is in the stack and nothing reads from it.** `CLAUDE.md` §2 has said "secrets only in
  Vault" since Phase 0; every secret actually comes from an environment variable. `FRD-116`. This
  becomes pressing rather than untidy the moment a service-account *private key* is involved.

Eleven documents written: `ADR-0010` plus `FRD-107`, `FRD-110`–`FRD-118`, `FRD-602`.

**The one open decision** is in `ADR-0010`: does AIRA also serve the predecessor's *wire contract*,
so clients migrate by changing a URL, or do the clients move to the Gemini surface? My
recommendation is the compatibility surface **with a stated sunset date and its usage visible in
reporting**, because the alternative couples our decommissioning date to the slowest consuming
team, and until they migrate their traffic is ungoverned — which is the whole thing the budgets and
limits exist for. Recorded as *Proposed*; `FRD-107` stays blocked until it is decided. Everything
else is contract-independent and can start immediately.

Three places where the FRDs deliberately **do not** copy the predecessor, each written down so the
deviation is a decision rather than an omission: TLS verification stays on (`kira_api.md` sets
`verify=False`); CORS is an origin allow-list, not `*` with credentials; and `GET /models` requires
authentication. A fourth is close to it — the predecessor resolves group membership from the
UserInfo endpoint on **every request**, which would make each authenticated call depend on Keycloak
being up and fast; `FRD-118` §11 asks whether that requirement even applies to us before anyone
builds it.

Three design points inside the FRDs are worth repeating here because they are the ones most likely
to be got wrong quietly:

- **An attachment costs tokens no character count predicts** (`FRD-110` §5.3). The pre-dispatch
  reservation would estimate a 20 000-token PDF request as a sentence, reopening under documents
  precisely the race `FRD-405` closed for text.
- **A batch must not be a way around a rate limit** (`FRD-113` §5.3). One token per request means a
  caller limited to 10 requests a minute can embed 5 000 texts a minute. A batch of *n* takes *n*.
- **The structured-output capability must be checked after routing** (`FRD-112` §5.3). With a
  fallback chain, the model that answers is not the model that was asked for, and returning prose
  to a caller that will `JSON.parse` it is a failure that surfaces days later in someone else's
  application.

The OpenAI-compatible surface (`FRD-106`) is deferred by decision so parity is not competing with a
second new contract.

---

## 2026-08-06 — Reporting: the data has been collected since Phase 1, and is finally readable
Every dispatched request has been recorded since `FRD-103` and priced since `FRD-403`. Nothing
showed any of it. The only figures anywhere were the consumption bars beside a budget — one use
case, the current period, three numbers — so "what did last month cost, and which use case is
responsible" was a question answerable only with `psql`. That is most acute for **IT Steuerung**,
the role the PRD defines around exactly this oversight and which until today had a read-only list.

`FRD-601` closes it: `GET /v1beta/reporting?from=&to=` on the gateway (the request log lives in
its database), and a **Reporting** screen in the SPA. Totals plus breakdowns by use case, by model
and by member — requests, the prompt/completion token split, spend, failures, latency.

Three things were decided rather than defaulted:

- **The visibility rule lives at the edge, in one function.** Governance sees every use case;
  anyone else sees the use cases their token puts them in; a caller with neither gets an **empty
  report, not a refusal** — having nothing to see is not a failure. `None` (everything) and `()`
  (nothing) are deliberately distinct values rather than one falsy scope, because confusing them
  is the single mistake here that would show an installation's whole spend to somebody entitled
  to one use case. Both halves are pinned by mutations `N1`/`N2` and by the browser test.
- **Latency is an average and a maximum, and is called that.** A percentile is the figure an
  operator actually wants, but `percentile_cont` is Postgres-only and the hermetic tests run on
  SQLite. A dialect-dependent query would leave the production expression exercised only by the
  integration suite — precisely the shape of thing that breaks quietly. The compromise is
  documented in the FRD rather than papered over by calling an average a median.
- **Unpriced stays unpriced.** A request on a model with no price counts toward
  `unpriced_requests` and toward nothing else, in every breakdown row, and the screen says the
  spend is a lower bound whenever there is any. Same rule as the budget bars, same reason.

The index assertion is against `pg_indexes`, not `EXPLAIN`: on a test database of a few hundred
rows the planner correctly prefers a sequential scan whatever the schema says, so an `EXPLAIN`
here would have been measuring how much traffic the stack happened to have.

**What the layers caught, again in that order.** The unit and mutation passes were green before
anything else ran. The e2e layer then failed on two things that had nothing to do with reporting:
a helper that decided "this use case does not exist" from a `count()` taken while the list was
still a spinner — latent in an existing test since it was written, and only exposed once the row
it looked for actually existed — and `demo-uc`, which another test deliberately caps at five
requests a month, answering **429** to the traffic this test wanted to generate. The first was a
real race and is fixed in `support.ts`; the second was a bad fixture choice, and the visibility
test now makes its contrast on the same screen and the same period with two different users
instead of borrowing a use case another test owns.

Also, an environment trap worth writing down: recreating the Keycloak realm gives every user a
new `sub`, and Management binds users to `sub` (ADR-0007). The old rows keep the plain usernames,
so the new identities get provisioned as `ucadmin-dedf235d` and the e2e login assertion fails on a
name it has never seen. That is the binding working exactly as designed — but it means **a realm
recreation orphans the Management users**, and the fix is to drop the stale rows, not to loosen
the binding. Noted in `deploy/compose/README.md` next to the realm-import caveat.

---

## 2026-08-05 — The browser layer finally ran, and immediately earned its keep
The Playwright download had been blocked by network policy since the e2e suite was written, so
36 browser tests had never executed here. Allowed at last: **38 passed, 4 failed**, all four in
`gateway.spec.ts` — everything that needs the browser's own session token to reach the gateway.

The cause was not in either service. The SPA's container serves through nginx and proxies `/gw`
to the gateway; nginx was connecting to `172.19.0.4` while Docker's DNS had been answering
`172.19.0.10` for some time. **nginx resolves a literal hostname in `proxy_pass` exactly once**,
when the configuration loads, and keeps that address for the life of the process. Every restart
of the gateway container since then had been invisible to it.

This is a production defect, not a test artefact: in any orchestrator a redeploy gives the
container a new address, so every gateway redeploy silently breaks the dry-run and consumption
screens until somebody thinks to restart the frontend too. The symptom — "the gateway could not
be reached" — points at the wrong service, which is the part that would have cost the most time.

Both upstreams now go through a variable with a `resolver`, which defers resolution to request
time. Proved rather than assumed: the gateway was forced onto a different address (a placeholder
container took its old one) and `/gw` kept answering 200 with nginx untouched — `172.19.0.5` →
`172.19.0.14`, no restart.

Two guards added to the integration suite: that the SPA reaches both services through its own
origin, and that the rendered config still passes its upstreams through variables. The second
asserts the *shape* of the config, because the behaviour it protects only appears after a
container has actually moved. Verified to fail against the old form.

Also, and worth saying plainly: the six rate-limit e2e specs written earlier without ever being
run all passed. That was luck as much as care — writing tests one cannot execute is not a
practice to repeat.

---

## 2026-08-05 — The older phases under the same standard (74 properties)
The mutation check covered FRD-405 and the tombstone work; auth, budgets, the pipeline, retention
and the management control plane had never faced it. Two samples in older code had already turned
up one real defect each, so the odds of the rest being clean were not good.

The properties were derived **from the requirement documents**, not from the code — that is the
whole discipline, and reading in the other direction is what let the earlier defects hide. Four
parallel passes over FRD-101/102, FRD-200/201/202/204, FRD-400/401/403 and FRD-300/303/404 plus
ADR-0006/0007 produced ~50 candidate properties; 45 became mutations, for 74 in total.

**4 of 74 survived**, all of them missing tests rather than defects:

- **A key bound to one use case could act on another** — nothing defended the tenant boundary for
  API keys. Verified against the running gateway before deciding: the code is correct (403 on a
  foreign selector), so this was a hole in the suite, not in the product. It is the one that would
  have hurt most if it ever regressed.
- The half-price rule was tested in **one direction only** — an output-only model would have been
  accepted, billing nothing for the prompt.
- A model published **without any price** was never exercised through the consumer, so turning
  those nulls into zero would have made its traffic silently free.
- The default for whole-row deletion (`log_retention_days = 0`) was unpinned; a drift to non-zero
  would have given every installation a reporting horizon nobody chose.

One prediction was wrong in a useful way. I expected `JSON(none_as_null=True)` to survive, on the
grounds that SQLite cannot distinguish SQL NULL from the JSON literal — it is caught, by the
retention *idempotence* test, because the SQL-level `is_not(None)` still sees the difference. The
mechanism was not what I assumed, and checking which test failed is what showed that.

Deliberately excluded: the constant-time hash comparison. It is a timing property, and no
hermetic test can defend it honestly. Staging one that appears to would be exactly the
self-deception this exercise is against, so it is recorded as knowingly undefended instead.

---

## 2026-08-05 — Deleting a use case did not withdraw access
Found while looking for the next piece of work, and verified against the running stack before
being believed: **24 active API keys were bound to use cases that no longer existed**, and a
request with such a key answered **HTTP 200**.

Management cascades a use-case deletion in its own database — the foreign keys see to that — but
publishes only `usecase.deleted`. The gateway's handler removed the use case and its members and
nothing else, so keys, budgets, rate limits, pipeline configs and usage counters were left
pointing at nothing. Two consequences, and the first is the serious one: whoever deleted a use
case believed access had ended when it had not, and a slug created again later silently inherited
the deleted one's budgets, limits and pipeline.

The handler now cascades. Two asymmetries are deliberate. Keys are **deactivated, not deleted** —
delivery is at-least-once, so a re-delivered `api_key.created` would otherwise resurrect one, the
same reason revocation is terminal elsewhere (ADR-0007). And `request_logs` are **kept**: the
audit trail and the spend history are what a later question about what was spent, and by whom, is
answered from, so they outlive the use case on purpose (FRD-404 §4.1).

Migration `0011` clears what earlier deletions already left behind, since those never get a second
`usecase.deleted` event. Applied to the running database: 24 orphaned active keys → 0, orphaned
budgets and pipelines gone, all 73 request-log rows untouched.

One thing deliberately *not* done: refusing a key at authentication time because its use case is
unknown. It looks like cheap defence in depth and is not — keys and use cases arrive on different
Kafka topics with no ordering between them, so a freshly issued key can legitimately reach the
gateway before the use case it belongs to, and the check would refuse it.

Proved end to end over the real event path: the key answers 200, the tombstone is applied, the
same key answers 401. Three mutations added (`make mutants` is now 29), including one asserting
that the request log is *not* deleted — with a local import inside the mutation, so it fails on a
test rather than on a NameError, which would have counted as caught for the wrong reason.

---

## 2026-08-05 — Proving the tests can fail (`make mutants`)
Prompted by the obvious question after the review: the suite was green, coverage was 99%, and
seven real defects were in there anyway. How?

Three different mechanisms, not one.

1. **The tests were written from the code, not the requirement.** A test named "both scopes apply
   and the stricter wins" asserted *alice is refused* — which is what the code did. The
   requirement said more: *and it must cost the use case nothing*. Test and code came from the
   same mental model, so they agreed. Agreement is not evidence.
2. **Coverage measures lines, not properties.** `embedContent` was neither rate limited nor
   budgeted while its lines were fully covered — by the happy-path test. A missing requirement is
   invisible to a coverage tool.
3. **Two tests never reached the path they were named after.** `TestClient` buffers a streamed
   body before the test can hang up, and SQLite enforces no column lengths, so the "failing
   write" test's write always succeeded.

The response is `tools/mutation_check.py` (`make mutants`): 26 properties, each expressed as the
one-line defect that would break it. It applies each in turn and checks that something goes red.
The first run found five survivors — four real gaps (the failing-write test that could not fail,
the untested circuit-breaker reopening, an unasserted `maxOutputTokens` estimate, and an
unasserted `enabled` flag), and one false gap caused by too narrow a test selection, which the
tool now warns about because a false gap costs as much time as a real one.

The harness is crash-safe for a concrete reason: the first run was interrupted and left
`writer.py` mutated. Undetected, that would have looked like a genuine defect to whoever ran the
suite next. It now journals the original before each edit and restores from it on the next start.

After the four gaps were closed: **all 26 properties are defended**. The convention is in
`CLAUDE.md` — when you fix a bug, add the mutation that reintroduces it.

---

## 2026-08-05 — Review of FRD-405: seven defects found and fixed
A structured review of the freshly written FRD-405 code and its documentation, run as four
parallel audits (docs-vs-code, correctness, extensibility/readability, test quality) with every
serious finding re-verified by hand before being acted on. It found real defects in work that had
been reported as verified the same day — the verification had covered the paths that were tested,
not the edges.

**The worst one was the opposite of a promised property.** `FR-4` says a member's own burst may
not consume the whole use case. The code took a token from the wide use-case bucket *first* and
only then tested the narrow member bucket, keeping the token when the member was refused.
Measured: use case 5/burst, alice 1/burst — after alice's one allowance and four refusals, bob
got **0** of the remaining 4. One throttled member starved everyone else, which is a denial of
service rather than a rounding error. The decision is now all-or-nothing across every bucket a
request must pass, expressed in the interface (`take()` takes the whole set) rather than as a
rule callers must remember.

**Reservations leaked on several exit paths.** Only `UpstreamError` released, so any other
failure — a malformed upstream body, a database hiccup in the pricing lookup, an outright bug —
left the reservation behind. The streaming path settled instead of releasing on failure, charging
a request-limited budget for a request that produced nothing, and its settlement and audit write
sat after the loop with nothing guarding them, so a client hanging up skipped both: the request
vanished from the log despite having reached the upstream. `BudgetService.hold` now makes the
guarantee structural, and the streaming finish runs in a `finally`.

**`embedContent` was neither rate limited nor budgeted** — the controls sat inside the
generateContent branch, so a caller only had to pick the other verb. The handler now parses per
method and runs one shared gate, which is the actual fix: a control that applies to some verbs and
not others has to be impossible to write by accident.

**Two Redis edge cases.** A failure between two budgets left the first reservation unreachable;
it is now handed back. And a correction that could not reach Redis left the estimate in place for
the rest of the period — my first attempt at a fix was to delete the key, which is nonsense,
because the store holding the stale figure is the store that is unreachable. The real fix is a
lifetime, not a repair: counters expire in five minutes and are rebuilt from Postgres, which costs
nothing since every reservation already reads that figure to seed with.

**And the audit writer dropped rows during shutdown** — `stop()` awaits the worker, and a request
landing in that await queued against a worker already being cancelled.

Documentation: `DEPLOYMENT.md` still listed "No rate limiting" as a known gap and its topic table
omitted `aira.rate-limits`, which fails **silently** — Management writes its outbox, the relay
cannot publish, and a setting appears saved while doing nothing.

Test quality mattered as much as the code. Three integration tests caught `except Exception`
around the guards, which would have counted a database error as "correctly refused". The
config-cache test only exercised manual invalidation, which production never calls. And the
disconnect test I wrote first passed against the *old* structure — going through `TestClient`
buffers the whole body, so it never reached the path it was named after. Each fix was proved by
restoring the defect and watching the test fail.

---

## 2026-08-05 — Rate limiting, atomic budget reservations, and the audit write off the hot path
`FRD-405`, decided in `ADR-0008`. Three defects with one cause: the gateway acted on state it had
already stopped being sure about.

**Nothing limited how fast a caller could consume.** Measured on the running stack, one request
opened six to seven separate database sessions — so a client in a retry loop exhausted the
connection pool, and the first casualties were the *other* use cases. A budget states how much
may be spent, never how fast.

**A budget could be exceeded by a multiple.** `guard` read the period's usage, dispatch ran, then
`record` booked it. Requests in flight were invisible to each other's guard, so twenty concurrent
requests all passed a limit with room for one. Since `FRD-403` that limit is a sum of money, which
made it an accounting defect rather than a cosmetic one.

**The audit write blocked the answer.** `record_request` was awaited before the response
returned, contradicting `CLAUDE.md` line 55 — *persistence must not block the request path*.

Added **Redis** as the shared counter store. The argument was the access pattern, not raw speed:
these counters are high-frequency, tiny, contended and worthless once their window closes, which
is the one shape a row-locking MVCC database handles worst — and pointing the hottest path at
Postgres would have loaded the component that is already the throughput ceiling.

- **Rate limits** per use case and per member: a token bucket, refill-test-take in one Lua script,
  so instances behind a load balancer enforce one limit rather than one each. A bucket rather than
  a fixed window, which permits twice the limit across a boundary and cannot tell a short
  legitimate burst from sustained flooding. Over the limit is a 429 carrying `Retry-After`.
- **Budget reservation**: `guard` reserves an estimate before dispatch, `settle` corrects it to
  the real figure, `release` gives it back when the request failed — otherwise a provider outage
  would look to a use case exactly like having spent its month. Postgres stays authoritative and
  seeds the counter on a miss, so a Redis restart costs the in-flight reservations and never the
  period's accounting.
- **Persistence** moved to a bounded queue drained by a worker. Bounded, because an unbounded one
  only moves the exhaustion from the connection pool to memory; drained on shutdown; and a full
  queue writes inline rather than dropping, since the rows lost under pressure would be exactly
  the ones from the incident someone later has to reconstruct. Also removed a `session.refresh()`
  that re-selected every inserted row for nothing.

**Degradation is decided, not accidental** — a new dependency on the request path must not turn a
cache outage into a product outage. Rate limiting falls back to a per-instance bucket,
deliberately *not* to allowing everything: Redis being down is when infrastructure is already
strained, the worst moment to stop bounding a runaway caller. Budgets fall back to the old
Postgres path — enforcing but racy — because refusing traffic would be an outage and skipping
enforcement would be free money. `/readyz` reports `degraded: true` and still returns 200.

Verified against the live stack: two independent limiter instances allowed 4 of 6 requests against
a burst of 4 rather than 4 each; 25 concurrent guards against a budget with room for one admitted
exactly one; 20 concurrent guards against a 1.00 cost budget with a 0.40 estimate admitted exactly
three; the real gateway answered 429 with `Retry-After` after its burst; and with Redis stopped,
requests kept being served in ~6 ms while `/readyz` reported the degradation and recovered by
itself when Redis came back.

The proof that the race was real is a pair of tests sharing one harness: twenty concurrent
requests pass 20/20 on the old path and 1/20 on the new one. The reservation tests run the real
Lua against `fakeredis` rather than a Python reimplementation — the defect lives exactly in the
gap the script closes.

Counts: 22 integration tests (from 14), 199 frontend tests (from 191), Python coverage 99%.
The e2e specs for the new tab are written but were **not executed** here: the Playwright browser
download is blocked by network policy in this environment.

---

## 2026-08-05 — Inline forms were a staircase
Reported from looking at the running app: the hint under the slug field ("Used in the gateway URL
and in API keys.") pushed that input upwards, so the controls in the row no longer lined up.

`.form-inline` was `align-items: flex-end`. Bottom alignment looks right only while every field
is equally tall — the moment one carries a hint under its input, that field grows and its control
rises. Measured before the fix: the slug input started at y=371 and the name input at y=394, a 23
pixel step. Four of the five inline forms in the app were affected.

The row now aligns at the **top**, every label reserves exactly one line, and children that are
not fields (the submit button, an inline error) skip the label row explicitly. Verified across all
five forms and at a width where the budget form wraps: every control in a row starts within a
pixel of its neighbours.

`expectFormControlsAligned()` in the e2e suite now groups a form's controls by the row they landed
in and fails on a step of more than 2px. Confirmed by putting the old CSS back: it reports
"row 10 is a staircase — uc-name@394, button@397". Neither the unit tests nor a DOM assertion can
see this — jsdom has no layout at all.

**Gates**: 454 unit + 14 integration + 36 e2e + 177 frontend tests green.

---

## 2026-08-05 — Payload storage can be switched off per use case
Follow-on to FRD-404. Retention answers "how long"; this answers "at all". Until now the only
control was the installation-wide `AIRA_STORE_PAYLOADS` env var — not per use case, not in the
database, not in the UI.

- **`UseCase.store_payloads`**, default on, next to the retention period on the use-case
  overview. Off means no prompt or response is written for that use case.
- **The installation setting is a kill switch above it**: a use-case admin may decline storage,
  but cannot re-enable it where the operator forbade it.
- **Switching off purges**: it is treated as a period of zero, so what is already stored goes on
  the next pruner run instead of lingering for the remainder of the old period.
- Requests without a use case follow the installation setting; a use case the gateway has not
  heard of yet keeps the previous behaviour rather than silently dropping the audit payload.

**Verified on the live stack**: with storage off for `demo-uc`, a request containing a personnel
number returned 200, its tokens and cost were recorded — and the number appears **nowhere** in
`request_logs`.

**Two UI bugs the browser tests found, neither visible in jsdom.**

1. A `<label for="x">` that also *wraps* its input makes a real browser forward the click twice:
   the box toggled back instantly and the switch looked dead. jsdom does not reproduce label
   forwarding, so the unit test was green.
2. Then the same race as the pipeline builder: the settings form was interactive while the GET was
   still in flight, so the arriving response reset the switch. It intermittently appeared to work.
   The overview panel now renders only once the use case has loaded, and a unit test asserts it.

A one-way `[ngModel]` on a checkbox inside an `NgForm` also writes the old value straight back;
the switch uses plain `[checked]`/`(change)` instead.

**Gates**: 454 unit + 14 integration + 33 e2e + 177 frontend tests green.

---

## 2026-08-05 — FRD-404: stored prompts now expire, per use case, a week by default
The least defensible property this product had: FRD-103 stored every request and response body,
`store_payloads` is on by default, the redaction hook is a no-op — and **nothing ever deleted
them**. Prompts routinely contain personal data.

- **`UseCase.retention_days`**, 1–3650, **default 7**, editable by a use-case admin and shown on
  the use-case overview so it is visible to whoever is accountable rather than buried in config.
  Distributed with the existing `usecase.upserted` event.
- **`python -m aira_gateway.retention`** (`make prune`) applies it; the reference stack runs it
  hourly in a container. It is a one-shot process like the relay: **if nothing schedules it,
  nothing is deleted** and the period in the UI is a promise nobody keeps.
- Requests with no use case follow `AIRA_DEFAULT_RETENTION_DAYS` (7) — not exempt just because
  nobody claimed them.

**Two clocks, deliberately.** Payload retention (per use case, 7 days) removes the request and
response bodies; the row and its metadata stay. A seven-day *row* retention was the obvious first
design and is wrong: `request_logs` is where per-request cost lives (FRD-403), so it would leave
the spend reporting able to see one week and no further. Whole-row deletion is a separate,
installation-wide switch (`AIRA_LOG_RETENTION_DAYS`), **off by default** because the reporting
horizon is an organisational decision, not something a release makes silently.

**A bug the idempotency test caught.** SQLAlchemy's `JSON` type writes `None` as the JSON value
`null`, not SQL `NULL`. "Has no payload" and "has a payload that is null" were therefore
indistinguishable, so the pruner rewrote the same rows on every run and its reported count meant
nothing. Columns are now `JSON(none_as_null=True)`; migration 0008 normalises existing rows and
adds the `(use_case, created_at)` index the scan needs.

**Verified on the live stack**: rows aged 10 and 2 days on a use case with the default period →
the older payload gone, the fresher kept, both rows retaining tokens and cost; a second run
cleared nothing; lowering the period to 1 day then removed the second payload too.

**Still open, and stated in the FRD**: content redaction. Retention decides *when* a payload
goes; nothing yet masks sensitive values *inside* one while it is kept.

**Gates**: 448 unit + 14 integration + 31 e2e + 171 frontend tests green.

---

## 2026-08-05 — CI: the gates are enforced, not merely available
`.github/workflows/ci.yml`. Until now every quality gate — three test layers, two coverage
thresholds, ruff, mypy, Prettier — only ran when somebody remembered, while `CLAUDE.md` claimed
CI enforced them. It does now.

- **Three jobs**: Python (lint, format, mypy, unit tests with the coverage gate); frontend
  (Prettier, a **production** build, unit tests with their thresholds); and the stack, which
  builds the three images, runs `make up-full`, waits for health, then runs the integration
  suite and the Playwright end-to-end suite against it. On failure it uploads the Playwright
  report and dumps the container logs.
- **The workflow is deliberately thin**: every step calls the same `make` target a developer
  runs locally, so CI and a local run cannot drift, and switching CI systems is a rewrite of one
  file. `make ci` reproduces the hermetic half; `make wait-healthy` is the readiness gate, useful
  by hand too.
- **The frontend job builds for production on purpose** — that is where the CSP silently disabled
  the entire stylesheet; a development build would not have caught it.

**Verified by breaking it on purpose**: a stray import, a wrong return type, and a deliberately
miscalculated cost each made `make ci` exit non-zero; the clean tree exits 0. A gate that cannot
fail is theatre.

Also fixed on the way: **Node was never pinned**, although ADR-0003 requires it — added
`.nvmrc` (26) and an `engines` block, which CI now reads instead of hardcoding a version.

**Caveat**: the workflow runs on push, but a green run only *blocks* a merge if branch protection
requires it, and this repository has no remote configured yet — the workflow starts working the
moment it is pushed to GitHub.

---

## 2026-08-05 — FRD-403: budgets in money, not tokens
Driven by the observation that budgeting in tokens is not cost control. A token differs in price
by more than an order of magnitude between models, and every provider bills **output** tokens
several times higher than input — so even a known price cannot be applied to a single
`total_tokens` figure.

- **Model catalog with prices** (the price half of FRD-307): per model, the cost of 1M input and
  1M output tokens, maintained by a **Global Administrator** only — a price follows the provider
  contract, not a use case. Distributed over the new compacted topic `aira.models`.
- **`Budget.limit_cost`** alongside the existing token/request caps, enforced pre-dispatch with
  the same `429 RESOURCE_EXHAUSTED`.
- **Per-request cost in `request_logs`**, so spend can be *reported*, not only capped.
- **UI**: the budget tab leads with a spend limit and a spend bar; a new **Models & prices**
  screen (read-only unless global-admin).

**Two decisions worth recording.**

*Money is an integer, never a float.* Amounts are nano-units (10⁻⁹ of the currency) in `BIGINT`,
via the new `aira_common.money`. Floating point cannot represent 0.1 exactly and a spend figure
is the sum of millions of small charges; `NUMERIC` would be exact on Postgres but SQLite — which
the tests run on — stores it as a float, so the tests would not exercise production behaviour.
Amounts therefore also cross API boundaries as decimal **strings**, never JSON numbers.

*Unknown is not zero.* A request on an unpriced model did cost money; AIRA just cannot say how
much. Booking it as `0` would make the spend figure silently too low — the worst failure mode for
a number somebody is accountable for. It is counted under `unpriced_requests`, excluded from the
cost total, does not consume the cost budget, and is named in the UI. In the same spirit, the
display never renders a non-zero amount as `0.00`; it widens its precision until it is truthful.

Also fixed along the way: adding a positional `cost_nanos` to `BudgetService.record` made the
existing callers pass their *timestamp* as an amount. Both extra arguments are keyword-only now —
an amount of money and a timestamp side by side as positionals is exactly how a wrong figure gets
booked with nothing failing.

**Verified on the live stack**: `mock-1` priced at 1.00/10.00 per 1M; three requests of 5 input +
8 output tokens each priced at exactly 85 000 nanos, accumulating to exactly 255 000 — no drift;
lowering the limit below that produced `429 Cost budget exhausted`.

**Gates**: 429 unit + 12 integration + 28 e2e + 165 frontend tests green; ruff, mypy and both
coverage gates unchanged.

---

## 2026-08-05 — Containerised: `make up-full` brings the whole system up
Three images (`gateway/Dockerfile`, `management/backend/Dockerfile`,
`management/frontend/Dockerfile`) and a compose overlay
(`deploy/compose/docker-compose.apps.yml`). A cold start — volumes removed — reaches a working
demo in **42 seconds**, with all 23 e2e tests green against it.

- **One image per component, several roles each.** The gateway image also runs the config
  consumer and `alembic upgrade head`; the management image also runs `manage.py migrate`, the
  outbox relay and (in the `demo` profile) `seed_demo`. Both are multi-stage, ship only the
  resolved virtualenv, run as uid 10001 and carry a healthcheck. The SPA is served by nginx,
  which takes over the `/api` and `/gw` proxying the dev server does in development.
- **Ordering is expressed, not slept on**: migrations and topic creation run to completion before
  the services that need them (`service_completed_successfully`).
- **The relay runs as a loop container** (`AIRA_RELAY_INTERVAL`, default 10s), so configuration
  propagates without anyone remembering `make relay`.

**Three defects the containers exposed**, none of which could show up in the dev setup:

1. **The CSP broke the production stylesheet.** Angular's build defers the global stylesheet with
   `<link media="print" onload="this.media='all'">`; that inline handler is script, and the CSP
   added in ADR-0007 allows scripts from `'self'` only — so it never ran and **the entire design
   system was missing from any production build**. The dev server injects styles differently, so
   everything looked right locally. Fixed by disabling `inlineCritical`; a new e2e test asserts
   no stylesheet is left deferred and that `.card` actually paints.
2. **`aira_common` did not declare PyJWT.** `aira_common.oidc` imports `jwt`, but the dependency
   was declared on *aira-gateway*. The shared dev virtualenv hid it; the isolated management
   image failed to import at startup. Declared where the import is.
3. **`up-full` produced an empty demo.** The Keycloak realm has the five accounts, but their
   Django counterparts only appear on first login, so "add member ucuser" had nobody to add.
   Seeding now runs in a `demo` profile — a real deployment omits it, and `seed_demo` refuses
   outside local/demo mode anyway.

Also: management had **no production-capable server** — only Django's `runserver`, which Django
itself excludes for production. `uvicorn` is now a declared dependency and serves the ASGI app.

Docs: `docs/DEPLOYMENT.md` §1/§2 rewritten around the images, "no container images" removed from
the gaps table, README and the compose README updated.

---

## 2026-08-05 — Deployment documentation (`docs/DEPLOYMENT.md`)
Written from the code, not from intent — every variable and command in it was read out of the
settings classes and the Makefile, and the setup sequence was re-run against the live stack.

- **New `docs/DEPLOYMENT.md`**: what actually runs (five processes, not two), the standalone
  quickstart, integration with an existing Postgres / Keycloak / Kafka / OTel collector / upstream
  provider / reverse proxy, a complete reference of all **28 gateway and 21 management settings**,
  what has to be prepared in Keycloak (client, the five realm roles, the groups mapper, the
  `/use-cases/<slug>` groups), and a production checklist.
- **Root README** no longer claims "Planning phase" — Phases 0–4 are delivered. It now carries a
  quickstart that matches reality and links the deployment guide.
- **`deploy/compose/README.md`** corrected: it still advertised SigNoz (superseded by ADR-0004),
  described the realm directory as "empty until Phase 2", and did not mention that topic
  auto-creation is off.
- **`make help` was hiding targets**: its grep had no digits in the target-name character class,
  so `test-e2e` never appeared. Fixed, and `e2e` demoted to a plain alias.

**Stated plainly in §7 rather than glossed over**: there are no container images for the two
services (Compose is infrastructure only); Vault and the Schema Registry run in the reference
stack but **no code reads from them**; the SPA's issuer and client id are compiled in, so
retargeting it needs an edit and a rebuild; Kafka has no auth/TLS settings; the relay is a
one-shot command that must be scheduled or configuration never propagates; `request_logs` has no
retention; there is still no CI.

---

## 2026-08-05 — Verified against the live stack: e2e (Playwright) + integration tests
Point 1 of the plan: stop trusting the hermetic suites and actually run the thing. Three defects
surfaced that no unit test could have caught — two of them in the security pass itself.

**What the run found**
- **The hardened Keycloak realm broke Keycloak's boot.** The client `description` added in
  ADR-0007 was 259 characters; the `CLIENT.DESCRIPTION` column is `varchar(255)`, so the import
  aborted and the container refused to start. Also: `--import-realm` skips existing realms, so a
  running stack silently kept the old wildcard `redirectUris`/`webOrigins` — the hardening was
  never actually applied. Both now documented in `deploy/compose/README.md`.
- **The dev realm had none of the five AIRA roles** and one user with no roles. Keycloak is the
  source of truth for roles (FRD-201), so the documented demo acceptance in FRD-203 §5 could not
  pass: you could log in and do nothing. The realm now carries the roles and one user per role,
  usernames matching the Django seed so a login adopts the seeded account.
- **The pipeline builder discarded early edits.** Adding a step before the config GET resolved
  let the response clobber it: the graph stayed empty while the header claimed "Unsaved changes".
  The builder is now rendered only after the config has arrived; unit-tested as a regression.
- `make kafka-topics` really did need its fix — the three previously missing topics were created
  on this run.

**Consequence of ADR-0007 that was not thought through**: the gateway authorizes `usage` by
Keycloak group membership, but use cases are administered in Management, so a use case created in
the SPA has no group and its consumption stays hidden. The strict check is kept (it matches how
the data plane authorizes); the UI now distinguishes "refused" from "unreachable" and names the
missing group. Proper fix recorded as a follow-up in the ADR addendum.

**New test layers**
- `e2e/` — Playwright, **22 tests**, real Chrome: the Keycloak code flow incl. PKCE, role-aware
  nav, layout at 360/768/1280/1920 px (measured as `scrollWidth <= clientWidth`, which jsdom
  structurally cannot do), the sticky-inspector reachability fix, key issue/reveal-once/revoke
  with confirmation, the ADR-0007 rule that a governance role cannot mint a key, and the
  gateway dry-run driven with the browser's own token.
- `tests/integration/` — **12 tests** against the live stack, moved out of `gateway/tests/` into
  a top-level folder: the gateway's HTTP contract (401s, 413 body ceiling, `/readyz` not naming
  hosts, API-key auth + `request_logs` attribution over real HTTP) and the full config
  distribution round trip management outbox → relay → Kafka → consumer → gateway read-model.
- `make test-e2e` and `make run-gateway-oidc` added; `make test-integration` unchanged.

**Gates**: 392 unit + 12 integration + 22 e2e + 139 frontend unit tests, all green; ruff, mypy
and the coverage gates unchanged.

---

## 2026-08-05 — Management UI: usability, layout, and a frontend coverage gate
No new screens — a pass over the existing SPA for the things that made it feel unfinished.

- **Two silent-failure bugs (zoneless).** The app runs without zone.js, so a plain component
  property changed from *code* schedules no re-render. Clearing the create-use-case form and the
  member/key/budget forms from their success callbacks therefore left the submitted text sitting
  in the inputs, and switching a budget to member scope did not reveal the username field. All
  form state moved to **signals** with explicit `[ngModel]`/`(ngModelChange)` binding; regression
  tests assert the DOM, not just the model.
- **Nothing failed silently any more.** Every load and every mutation now reports its outcome:
  a new `errorMessage()` helper unwraps the shared `{"error": {...}}` envelope (including DRF's
  per-field `details`) so the server's own wording is shown. This mattered most right after the
  ADR-0007 pass: a use-case viewer clicking "Issue key" got a 403 and *no feedback at all* — the
  button looked broken. Loading states replace the misleading "No use cases yet." shown while the
  request was still open, and forms stay open (keeping input) when the server rejects them.
- **Width overflow.** Wide tables now scroll inside their card (`.table-wrap`) instead of dragging
  the page sideways; `min-width: 0` on flex items stops one long name from widening the layout;
  long identifiers break; header username truncates; nav and tab strips scroll on a phone; inputs
  no longer overflow narrow columns; forms stack below 640px.
- **The builder.** The sticky inspector had no height cap — taller than the viewport, it pinned
  its top and left the lower fields (e.g. "Default model" with several categories) permanently
  unreachable. It now caps and scrolls, and is only sticky where there is a second column. On
  ≥1200px screens the builder breaks out of the 960px reading column.
- **Destructive actions ask first** (remove member, revoke key, delete budget, delete step) via a
  stub-able `ConfirmService`; invalid submits are disabled with the reason shown inline instead of
  doing nothing; the clipboard fallback explains itself when the browser blocks the write.
- **Accessibility**: tablist/tab/tabpanel semantics with `aria-selected`, a label for every
  control, accessible names on icon-only buttons, `aria-expanded` on disclosures, progressbar
  roles on the budget bars, Space as well as Enter on graph nodes, visible focus rings.
- **Tabs are deep-linkable** (`?tab=keys`) and survive a reload.
- **Coverage gate**: frontend coverage went **53.8% → 92.3% statements (95.6% lines)** across
  30 → **134** tests, and `angular.json` now enforces 90/92/93/75 — verified to fail when unmet.

---

## 2026-08-04 — ADR-0007: security hardening pass (gateway, management, frontend)
Full-codebase security review with no new features — see **ADR-0007** for the findings, the
options weighed, and the trade-offs.

- **Closed authorization gaps.** `POST /v1beta/pipeline:dryRun` and `GET /v1beta/usage/{use_case}`
  now require an authenticated principal (usage additionally authorizes the use case) — the
  dry-run runs real LLM steps with caller-supplied prompts, so open access was a free relay to
  the upstream. Issuing an API key now requires **membership**, not mere visibility: the
  oversight roles see every use case and could previously mint a data-plane key for any of them.
  Django users are bound to the Keycloak `sub` (`OidcIdentity`, api migration 0001, trust-on-
  first-use for existing accounts) so a re-issued username cannot inherit someone's permissions.
- **Safe defaults.** Management refuses to boot outside `local` with the dev `SECRET_KEY`,
  wildcard `ALLOWED_HOSTS`, or `DEBUG`; security headers on by default. `X-Forwarded-For` is
  only honoured behind a declared proxy (`AIRA_TRUST_FORWARDED_FOR`) — the audit trail was
  client-forgeable. `?key=` and friends are redacted before the query string reaches a span.
  Revocation is terminal in the gateway read-model (a replayed `created` no longer resurrects a
  revoked key). `seed_demo` only runs locally / in demo mode.
- **Input bounds.** Request-body ceiling (`AIRA_MAX_REQUEST_BYTES`, 8 MiB, enforced before
  buffering, with or without `Content-Length`); use-case selector validated against the slug
  charset; pipeline configs bounded and **nested-quantifier regexes rejected** at authoring time,
  with independent execution bounds in the gateway and the browser preview (ReDoS on the hot path).
- **Frontend.** `requireHttps: 'remoteOnly'` + strict discovery validation + PKCE explicit,
  bearer token scoped to first-party prefixes only, every user-supplied URL segment encoded,
  CSP shipped in `index.html` (scripts same-origin), bounded live preview.
- **Infra.** Keycloak dev realm: redirect URIs / web origins pinned to the dev hosts (were `*`
  on a public client) and the password grant disabled. `make kafka-topics` now creates all five
  compacted topics (three were missing, so api-key/pipeline/budget distribution silently failed
  on a fresh stack with auto-create off).
- **Operational note:** the SPA's dry-run and consumption views now need a token the gateway
  accepts — enable `AIRA_OIDC_ENABLED`/`AIRA_OIDC_ISSUER` (see `.env.example`). Without it both
  degrade gracefully; nothing else changes.
- **Gates green**: backend 389 tests / 99.83% coverage, ruff + mypy clean; frontend 30 Vitest
  tests, Prettier clean, `ng build` OK.

---

## 2026-08-04 — FRD-402: budget UI (closes Phase 4)
- Gateway `BudgetService.usage()` + unauthenticated `GET /v1beta/usage/{use_case}` return
  current-period consumption per budget (used tokens/requests).
- Angular use-case detail gains a **Budgets tab**: set use-case / per-member budgets (scope, period,
  token/request limits) and **see consumption** as progress bars (warn ≥80%, full ≥100%); admins
  edit, members read. Consumption fetched from the gateway via `/gw`; limits from Management.
- **Gates green**: backend 328 tests / 99.85%; frontend 26 Vitest tests, Prettier clean, `ng build` OK.
- **Phase 4 (Budgets & Quotas) complete.**

---

## 2026-08-04 — FRD-401: budget enforcement + usage accounting
- Gateway `BudgetService`: **pre-dispatch `guard`** loads the budgets applicable to the request's
  use case + subject, checks the current period's usage, and **rejects with `429 RESOURCE_EXHAUSTED`**
  when a limit is met; **post-dispatch `record`** increments the counters (generate + streaming).
- Usage accounting table `budget_usage` keyed by `(scope_key, period_key)` — `uc:<slug>` /
  `member:<slug>:<subject>` × `YYYY-MM` | `YYYY-MM-DD` — so it **resets at day/month boundaries**
  (gateway migration 0006). Request-count limits block the request itself; token limits block once
  exceeded. `enforce_budgets` toggle (default on); no budgets configured → zero overhead.
- **Gates green**: 326 tests / 99.85% (budget service + routes 100%), ruff + mypy --strict clean.
  Next: `FRD-402` budget UI.

---

## 2026-08-04 — FRD-400: budget model + distribution (Phase 4 start)
- New Management `budgets` app: `Budget` per use case — `scope` (use_case | member), `period`
  (day | month), `limit_tokens` and/or `limit_requests`, `enabled`; unique on
  (use_case, scope, subject, period). Nested endpoints `GET/POST /use-cases/{slug}/budgets` (POST
  upserts) + `DELETE …/budgets/{id}` (members read, admins write); validation (member needs subject;
  at least one positive limit).
- Distribution: `budget.upserted` / `budget.deleted` via the transactional outbox → Kafka
  `aira.budgets` → gateway idempotent consumer → `budgets` read-model (gateway migration 0005).
- Enforcement + usage accounting is FRD-401; UI is FRD-402 (both planned).
- **Gates green**: 314 tests / 99.85% (budget modules + views 100%), ruff + mypy --strict clean.

---

## 2026-08-04 — FRD-306: pipeline rework — LLM routing, explainable filter, dry-run
- Reworked the pipeline after feedback that routing was length-only and the builder was opaque.
- **Routing** is now an **LLM classifier**: it reads system + user text, picks one of the configured
  `categories` (`{name, description, model}`) and routes to that model (`default_model` fallback).
- **Injection filter**: built-in patterns are **shown**; operators add **custom patterns** (invalid
  regex → literal); `use_builtins` toggle; `scope` user | system+user; LLM mode takes model +
  instruction.
- **Transparency**: `engine.dry_run()` + `POST /v1beta/pipeline:dryRun` return a full per-step trace;
  the builder gains a **test panel** with an instant **live preview** (deterministic steps,
  client-side) and a **Dry-run** button (full trace incl. LLM via gateway, `/gw` dev proxy).
- Inspector redesigned with inline help per step + a categories editor.
- **Gates green**: backend 299 tests / 99.8% (pipeline modules ~100%), ruff + mypy --strict clean;
  frontend 23 Vitest tests, Prettier clean, `ng build` OK. `FRD-306` done.

---

## 2026-08-04 — UI usability: tabbed use-case detail
- The use-case detail page was overloaded with stacked lists (members + keys + forms). Split into
  **tabs** (Overview / Members / API keys) so one section shows at a time; add/issue forms moved
  behind **disclosure** toggles; Overview shows quick **stat tiles**. Added `.tabs`/`.tile`/
  `.disclosure` to the design-system. `ng build` + 21 Vitest + Prettier green.

---

## 2026-08-04 — FRD-300/303: pre-dispatch pipeline (filter · routing · fallback) + graph builder
- **Gateway engine** (`aira_gateway/pipeline/`): per-use-case, config-driven pipeline runs before
  dispatch on the canonical request. Steps: `injection_filter` (heuristic **or LLM-backed**, fails
  open; action block|flag), `allow_check` (model allow-list), `model_route` (rule-based incl.
  cost/length rerouting). Dispatch follows a `fallback_models` chain. Default (no config) =
  pass-through, so prior behavior is unchanged. Decisions logged + traced (`aira.pipeline.*`).
- **Distribution**: `aira.pipelines` topic; idempotent consumer → `pipeline_configs` read-model
  (gateway migration 0004). Management `pipelines` app + `GET/PUT /use-cases/{slug}/pipeline`
  (members read, admins edit) publishes `pipeline.upserted` via the outbox.
- **Angular graph builder** (`features/pipelines`): route `use-cases/:slug/pipeline` renders the
  pipeline as a **clickable node graph** (Request → steps → Dispatch → fallback) with a per-step
  inspector; zoneless-safe signal state. Entry from the use-case detail.
- **Gates green**: backend 285 tests / 99.8% (pipeline modules ~100%), ruff + mypy --strict clean;
  frontend 21 Vitest tests, Prettier clean, `ng build` OK. `FRD-300`/`FRD-303` done. **Phase 3 core
  (pipeline) delivered.**

---

## 2026-08-04 — FRD-205: self-service API-key issuance + UI redesign (closes Phase 2)
- **Backend (Management → Gateway)**: Management is now the source of truth for API keys
  (ADR-0006). New `apikeys` app (model + serializers) with nested endpoints on the use-case
  viewset: `POST/GET/DELETE /api/v1/use-cases/{slug}/api-keys[/{prefix}]`. A member issues a key
  **bound to the use case**, plaintext returned **once**, only the hash stored. `api_key.created`/
  `api_key.revoked` flow through the transactional outbox to a new `aira.api-keys` compacted topic.
- **Gateway**: idempotent consumer upserts/deactivates the `api_keys` read-model; `ApiKey` gains
  `use_case` (migration 0003); a verified api_key `Principal` carries its bound use case, so
  requests need **no `/uc` selector** and a mismatched selector is rejected (403). Shared key
  format/hash extracted to `aira_common.apikeys`. CLI stays as break-glass.
- **Frontend**: use-case detail gains an **API-keys panel** (issue with one-time reveal + copy,
  list masked, revoke) and a members table. Typed `UseCaseService` methods + tests.
- **UI redesign**: global design-system (`styles.scss`) — tokens, cards, buttons, tables, badges,
  callouts; polished app shell (brand header, active-state nav, constrained content). Templates
  restyled with the shared classes.
- **Gates green**: backend 241 tests / 99.95%, ruff + mypy --strict clean; frontend 16 Vitest tests,
  Prettier clean, `ng build` OK.
- **Phase 2 (Management Foundation) is complete.**

---

## 2026-08-04 — Upstream status passthrough (gateway hardening)
- `UpstreamError` now carries the upstream HTTP `status_code` (`None` for transport failures).
- Gemini routes map it: **429 → `429 RESOURCE_EXHAUSTED`**, **503 → `503 UNAVAILABLE`**,
  **504 → `504 DEADLINE_EXCEEDED`**; everything else (upstream 4xx from *our* key/config, upstream
  5xx, transport errors) is masked as a generic **502 UNAVAILABLE** so a broken upstream is never
  mistaken for a client error. Streaming still logs + terminates cleanly (status already sent),
  now including the upstream status.
- Rationale: a client (e.g. opencode) hitting a real Gemini rate-limit should see `429` and back
  off, not a misleading `502`.
- **Gates green**: 225 tests / 99.9% (routes + gemini modules 100%), ruff + mypy --strict clean.

---

## 2026-08-04 — FRD-304: real Google Gemini upstream adapter (Phase 3)
- **Async provider protocol**: `Upstream` (`upstreams/base.py`) is now `async` (`generate`/`embed`
  coroutines, `stream_generate` async-iterator); added `UpstreamError` for upstream failures.
  `MockProvider` updated accordingly.
- **`GeminiUpstream`** (`upstreams/gemini.py`): calls the Generative Language API
  (`generativelanguage.googleapis.com/v1beta`) with an **injectable `httpx.AsyncClient`** so tests
  drive it via `MockTransport` — fully hermetic. API key sent as `?key=` query param, **never
  logged**. Non-2xx / transport errors → `UpstreamError`. `build_gemini_upstream(settings)` returns
  `None` when no key is set; the app registers `[MockProvider(), *gemini]`.
- **Pure mappers** (`upstreams/gemini_mapping.py`): canonical ⇄ Gemini request/response/stream-chunk,
  incl. `systemInstruction`, `generationConfig`, `usageMetadata`, `finishReason` normalisation.
- **Routes**: `generateContent`/`embedContent` return **502 `UNAVAILABLE`** on `UpstreamError`;
  streaming logs the error server-side and terminates the stream cleanly (headers already sent).
- **Config**: `GOOGLE_API_KEY`, `GEMINI_MODELS` (`gemini-2.0-flash,gemini-1.5-flash`),
  `GEMINI_BASE_URL`. `httpx` promoted to a gateway runtime dependency.
- **Gates green**: **222 tests / 99.9%** (new `gemini` modules 100%), ruff + `mypy --strict` clean.
- Enables binding **opencode** (Google provider + custom baseURL) to a use-case with real responses.
- See `docs/features/FRD-304-upstream-adapters.md`.

---

## 2026-08-04 — FRD-203: Angular management shell
- **Auth** (`core/auth`): `angular-oauth2-oidc` code-flow+PKCE against the `aira` realm; `AuthService`
  facade; functional `authInterceptor` (bearer on `/api` calls) + `authGuard` (redirect to login);
  `provideAppInitializer` runs OIDC discovery on startup.
- **API** (`core/api`): typed `MeService` + `UseCaseService` (list/get/create/update/remove +
  members) with models.
- **Shell**: header + **role-aware navigation** (Security/Governance/Administration shown by role
  from `/api/v1/me`), logout.
- **Screens** (lazy-loaded): use-case **list** (+ create form) and **detail** (edit context, member
  add/remove) wired to FRD-202 endpoints.
- **Dev proxy** (`proxy.conf.json`): `/api` → management `:8002`; `make run-frontend` uses it.
- **Gates green**: `ng build` OK (lazy chunks), **13 Vitest tests** pass (interceptor/guard/service/
  list/shell, browserless), Prettier clean. Python side unchanged (202 tests / 100%).
- **Next: FRD-205** (self-service API-key issuance) closes Phase 2.

---

## 2026-08-04 — FRD-204: config distribution over Kafka (Management → Gateway read-model)
- **Transactional outbox** (management `outbox` app): use-case/membership change events are written to
  an `OutboxEvent` row **inside the same transaction** as the change (mutations wrapped in
  `transaction.atomic`; subscriber wired via `events.subscribe` in app-ready). A `relay` command
  publishes pending rows to Kafka and marks them — at-least-once (crash-safe; consumer idempotent).
- **Shared Kafka** (`aira_common.kafka`): `Producer` protocol + `InMemoryProducer` (tests) +
  `AiokafkaProducer` (real; `# pragma: no cover` I/O); topics `aira.usecases`/`aira.memberships`;
  W3C trace context on headers.
- **Gateway consumer**: `apply_event` (idempotent upsert/delete) into read-model tables `use_cases`
  + `use_case_members` (Alembic 0002); `worker` (aiokafka) + `decode_event_type`. `make kafka-topics`
  creates compacted topics; `make relay` / `make consume`.
- **Gates green**: 202 tests, **100% coverage** (pure logic; Kafka I/O pragma-excluded, integration-
  tested); ruff + mypy --strict clean (aiokafka untyped import ignored).
- **End-to-end verified**: created `kafka-uc` in management → outbox rows → `relay` published to Kafka
  → gateway consumer applied → read-model shows `use_cases: kafka-uc` and `use_case_members:
  kafka-uc/demo-user/admin`. Failed publish (missing topic) left rows pending (nothing lost).
- **Next: FRD-205** (self-service API-key issuance, distributed via this backbone) or **FRD-203** (UI).

---

## 2026-08-04 — FRD-202: use-case CRUD + membership
- **`usecases` app**: `UseCase` (slug/name/description/processing_notes) + `UseCaseMembership`
  (unique per user). CRUD at `/api/v1/use-cases/` (DRF ModelViewSet); slug validated to the gateway
  selector charset (`[a-z0-9-]`).
- **RBAC applied** (FRD-201): list is scoped (governance sees all, others see permitted); create needs
  the use-case-admin/global-admin role and makes the creator the use-case admin; edit/delete needs
  `change_usecase` (or global-admin); membership needs `manage_members` (or global-admin). Adding a
  member grants **`django-guardian`** object perms (view; +change/manage for admins).
- **Membership actions**: `POST/GET /use-cases/{slug}/members/`, `DELETE …/members/{username}`.
- **Change hook** (`events.emit`): in-process subscribers on usecase/membership changes — the Kafka
  publisher subscribes here in FRD-204. Migrations excluded from coverage.
- **Gates green**: 190 tests, **100% coverage**; ruff + mypy --strict clean (DRF generics typed).
- **End-to-end verified**: as global-admin `demo-user` created `live-uc`, listed it, added a member,
  and an invalid slug → 400.
- **Next: FRD-203** (Angular shell) or **FRD-204** (Kafka distribution).

---

## 2026-08-04 — FRD-201: RBAC (roles + object-level use-case perms)
- **`aira_management.rbac`**: `sync_user_roles` maps a token's realm roles onto Django groups (the
  five AIRA roles) on every auth — Keycloak is the source of truth. DRF permission classes
  (`IsGlobalAdmin`, `IsITSecurity`, `IsITSteuerung`, `IsUseCaseAdmin`, `IsUseCaseUser`; global-admin
  implies all). `scope_queryset` narrows lists: governance roles (global-admin, it-steuerung) see all;
  others are limited to their **`django-guardian`** object-level permissions.
- **Wiring**: `django-guardian` added (INSTALLED_APPS + object-perm backend; `ANONYMOUS_USER_NAME=None`).
  The auth class calls `sync_user_roles` after provisioning.
- **Gates green**: 174 tests, **100% coverage**; ruff + mypy --strict clean (guardian import ignored).
- **End-to-end verified**: assigned realm role `global-admin` to `demo-user` in Keycloak → token
  carries it → `/api/v1/me` shows `roles:[global-admin]` and the Django group membership is synced
  (`demo-user | global-admin` in `aira_mgmt`).
- **Next: FRD-202** (use-case CRUD + membership, using these RBAC mechanics).

---

## 2026-08-04 — Phase 2 begins · FRD-200: management DRF API + OIDC
- **Shared OIDC** (`aira_common.oidc.JwtVerifier` + `build_jwks_client`): extracted JWT/JWKS
  verification so the gateway **and** management use one implementation. Gateway `OidcValidator`
  refactored to wrap it (behaviour unchanged, tests green).
- **Management DRF foundation**: `api` app with `KeycloakJWTAuthentication` (verifies the bearer JWT,
  auto-provisions a Django user from claims, attaches claims as `request.auth`), a consistent DRF
  **error envelope** (`{"error":{code,message,details}}`), and `GET /api/v1/me` (subject, username,
  email, realm roles, use-case groups). `IsAuthenticated` default; 401 via `authenticate_header`.
- **Gates green**: 167 tests, **100% coverage**; ruff + mypy --strict clean. Hermetic tests use a
  self-signed RS256 + fake JWKS (no Keycloak needed).
- **End-to-end verified**: management backend with `AIRA_OIDC_ISSUER=…/realms/aira` — no token → 401;
  a real Keycloak `demo-user` token → 200 `me` with username/email/groups; user auto-provisioned.
- **Next: FRD-201** (RBAC: realm roles → Django groups + `django-guardian` object-level use-case perms).

---

## 2026-08-04 — Quality: error-safety + test-tier separation (Jenkins-ready)
- **Confirmed the pytest suite is hermetic**: 154→156 tests pass with the **entire Compose stack
  stopped** (in-memory SQLite, fake JWKS, mock provider). The earlier curl checks were *manual*, not
  part of the suite.
- **Two test tiers** for CI: unit tests run by default; stack-dependent tests are marked
  `@pytest.mark.integration` and **excluded** (`-m 'not integration'`). Added `make test-integration`
  and an example integration test; documented in **`docs/TESTING.md`** with a Jenkins pipeline sketch
  (unit stage needs no Docker; integration stage brings the stack up).
- **Error-safety**: added a global exception handler — any unhandled error now returns a
  **Gemini-shaped 500 (`INTERNAL`)** on `/v1beta` (AIRA envelope elsewhere), logs full context
  server-side (path, method, error type/msg, subject, use_case, trace_id), and **does not leak**
  internal details to the client. Tested with a throwing provider.
- **Reviewed**: expected errors already carry contextual messages (model-not-found, missing-method,
  not-a-member-of-use-case, field-located validation errors, unauthenticated). Noted follow-up: OIDC
  fails closed (401) even when Keycloak/JWKS is unreachable — safe, but can't cleanly distinguish
  "provider down" (503) from "bad token" via PyJWT alone.
- **Gates green**: 156 tests, **100% coverage**; ruff + mypy --strict clean.

---

## 2026-08-04 — FRD-104 + FRD-105 — **Phase 1 (Gateway MVP) complete**
- **FRD-104 (mock fidelity + streaming)**: `:streamGenerateContent?alt=sse` now returns
  `text/event-stream` (`data: {json}\n\n`, the google-genai SDK path); the default returns a streamed
  **JSON array** (Gemini REST form). Mock honours `generationConfig.maxOutputTokens` → truncates and
  reports `finishReason=MAX_TOKENS`.
- **FRD-105 (tracing enrichment)**: `aira_common.set_span_attributes(mapping)` sets non-None
  attributes on the current span. `require_attribution` tags `aira.subject/use_case/auth_method`;
  `record_request` tags `aira.model/operation/status/source_ip/total_tokens`.
- **Gates green**: 154 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified**: SSE (`text/event-stream`) + JSON-array streaming + `maxOutputTokens`→
  `MAX_TOKENS`; a trace is **searchable in Tempo by `aira.use_case=demo-uc`** (filter traces by use
  case in Grafana).
- **Phase 1 complete**: FRD-100 (Gemini API) · 101 (auth) · 102 (attribution) · 103 (persistence) ·
  104 (mock/streaming) · 105 (tracing). Every request is authenticated → attributed to a use case →
  authorized → dispatched → persisted → traced. **Next: Phase 2 (Management foundation).**

---

## 2026-08-04 — FRD-103: request/response persistence + Alembic
- **`request_logs`** table + `RequestLogService`: persist each dispatched request/response with
  attribution (subject, auth_method, use_case), source IP, model, operation, token usage, status,
  latency, and **trace_id** (correlates to Grafana). Wired into generate/embed/stream routes via
  `record_request`.
- **Source IP** from first `X-Forwarded-For` hop else socket peer. **Redaction hook**
  (`Redactor`/`NoOpRedactor`) + `store_payloads` toggle (metadata-only when off).
- **Alembic** introduced for the gateway DB (`migrations/`, async env, `0001_initial` = api_keys +
  request_logs); `make migrate-gateway`. Dev/tests keep `create_all` (SQLite/bootstrap).
- **Gates green**: 149 tests, **100% coverage**; ruff + mypy --strict clean. Route persistence tested
  via httpx ASGITransport (hermetic SQLite).
- **End-to-end verified**: alembic migrated Postgres; a `:generateContent` call wrote a `request_logs`
  row with subject=demo, use_case=demo-uc, source_ip=203.0.113.7 (XFF), tokens 3/6/9, trace_id set,
  payloads stored.
- **Next: FRD-104** (mock upstream full fidelity) / **FRD-105** (tracing spans + IP on the span).

---

## 2026-08-04 — FRD-102: attribution & use-case selection (OIDC)
- **Problem addressed**: an OIDC token authenticates the *identity*, not *which use case* — a user
  can be in several. Solution: explicit per-request use-case **selector** + membership authorization
  from Keycloak **groups** (no Management DB/Kafka needed yet).
- **Selector**: `/uc/<use-case>/v1beta/...` path (via `UseCasePathMiddleware`) **or**
  `X-AIRA-Use-Case` header; **header overrides path** (per user's choice).
- **Membership**: `Principal.use_cases` derived from token groups under `/use-cases/<slug>`;
  `require_attribution` dependency authorizes `use_case ∈ use_cases` for OIDC (403 otherwise),
  attaches `Attribution(subject, method, use_case)` to `request.state`. `require_use_case` toggle
  (400 when missing). API-key/demo attributed without the group check (binding comes in FRD-205).
- **Keycloak realm**: added `/use-cases/{demo-uc,other-uc}` groups + a group-membership protocol
  mapper (`groups` claim); demo-user ∈ `/use-cases/demo-uc`.
- **Gates green**: 138 tests, **100% coverage**; ruff (+ FastAPI `Depends` bugbear config) + mypy
  --strict clean.
- **End-to-end verified**: real Keycloak token carries `groups`; `/uc/demo-uc` → 200, `/uc/other-uc`
  → 403 PERMISSION_DENIED, header overrides path → 200, no use case → 200.
- **Next: FRD-103** (persist request/response + attribution), then FRD-104/105.

---

## 2026-08-04 — Decision: API-key issuance belongs in Management (ADR-0006)
- Clarified the control-plane/data-plane split for API keys: **issuance/lifecycle/show-once** →
  **Management** (self-service UI, bound to use case); **validation** → **Gateway** against a local
  **read-model** fed by **Kafka** (`api_key.*` events; never plaintext). Rejected sync-call and
  shared-DB alternatives.
- The Phase-1 gateway-side generation + CLI are a **bootstrap**; issuance moves to Management in
  **Phase 2** (new ROADMAP `FRD-205`). The gateway `api_keys` table becomes the read-model. OIDC
  validation stays in the Gateway. No code change now — documented as `ADR-0006`; updated
  FRD-101/PRD/ROADMAP.

---

## 2026-08-04 — FRD-101 Slice B: OIDC bearer validation — **auth complete**
- **OIDC validation** (`gateway/auth/oidc.py`): `OidcValidator` verifies a Keycloak JWT via the
  issuer's **JWKS** (`PyJWT` + `cryptography`), checking signature, issuer, expiry, and (optional)
  audience; resolves to a `Principal(method="oidc")`. JWKS client is injectable → unit-testable
  without a live Keycloak. `build_oidc_validator` gates on `oidc_enabled`/`oidc_issuer`.
- **Wired** into `resolve_principal`: a non-AIRA `Bearer` token is validated by the OIDC validator
  when configured (`app.state.oidc_validator`); API keys still take the `aira_` path.
- **Keycloak realm**: added `deploy/compose/keycloak/realms/aira-realm.json` (realm `aira`, public
  client `aira-gateway` with direct-access grants, demo user `demo-user`); imported on startup.
- **Gates green**: 123 tests, **100% coverage**; ruff + mypy --strict clean. Hermetic OIDC tests use
  a self-signed RS256 keypair + fake JWKS resolver (valid/expired/wrong-iss/wrong-aud/bad-sig/no-sub).
- **End-to-end verified**: fetched a real access token from Keycloak (password grant) → Gemini route
  returns **200** with the bearer, **401** for a garbage token. Run with
  `AIRA_OIDC_ENABLED=true AIRA_OIDC_ISSUER=http://localhost:8080/realms/aira`.
- **FRD-101 complete** (API key + OIDC). **Next: FRD-102** (attribution: request → user/project/use-case).

---

## 2026-08-04 — FRD-101 Slice A: API-key authentication + gateway DB layer
- **Gateway DB layer** (`gateway/db/`): SQLAlchemy 2.0 async via **psycopg3** (Postgres) /
  **aiosqlite** (tests); `Base`, engine/sessionmaker builders, `create_all` (Alembic deferred to
  FRD-103), and the `api_keys` table. App builds the engine + runs `create_all` in a lifespan.
- **API keys** (`gateway/auth/`): format `aira_<prefix>_<secret>` (hex), only the SHA-256 **hash**
  stored; `ApiKeyService` (create/verify/revoke/ensure_demo_key) with constant-time compare;
  `Principal` (subject + method); credential extraction (`Authorization: Bearer` → `x-goog-api-key`
  → `?key=`); `require_principal` dependency guarding the Gemini routes (Gemini-shaped 401).
- **Toggle & demo**: `auth_required` (default true); demo mode seeds a deterministic demo key.
- **CLI** (`python -m aira_gateway.cli api-key create|revoke`) to mint/revoke keys.
- **Gates green**: 111 tests, **100% coverage**; ruff + mypy --strict clean. Tests hermetic
  (in-memory SQLite; pytest auto-detected).
- **End-to-end verified** against Postgres: CLI minted a real key (persisted in `api_keys`); the
  Gemini route returns **401** without a credential, **200** with the key (header/`?key=`/Bearer),
  **401** for a bad/revoked key.
- **Next: FRD-101 Slice B** — OIDC bearer validation (Keycloak JWKS) + realm import, plugged into
  the same `resolve_principal`.

---

## 2026-08-04 — FRD-100: Gemini-compatible unified API (Phase 1 begins)
- **Decision**: ship the **Gemini** wire format first (existing projects run on it); OpenAI later →
  `ADR-0005`. Updated PRD/ROADMAP/README; added detailed `FRD-100`.
- **Canonical core** (`gateway/core/canonical.py`): provider-agnostic request/response/usage/chunk —
  the single schema every surface and upstream agrees on (so OpenAI/FRD-106 is just another mapper).
- **Upstream abstraction** (`upstreams/base.py`): `Upstream` protocol + `ProviderRegistry`; the
  deterministic `MockProvider` (evolved from FRD-002) is the only provider in Phase 1.
- **Gemini surface** (`api/gemini/`): Pydantic wire schemas, Gemini⇄canonical mappers, and routes —
  `POST /v1beta/models/{model}:generateContent | :streamGenerateContent | :embedContent`,
  `GET /v1beta/models`, `GET /v1beta/models/{model}`. Gemini-shaped error envelope (400/404/500).
- **Gates green**: 88 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified** via curl: list models, `:generateContent` (correct candidates + usage),
  NDJSON `:streamGenerateContent`, and unknown-model → 404.
- **Next in Phase 1**: FRD-101 (auth: API key + OIDC), then attribution/persistence/tracing.

---

## 2026-08-04 — FRD-002: seed & demo mode — **Phase 0 fully complete**
- **Seed framework** (Django, `aira_management.apps.seed`): an extensible registry — each phase
  registers idempotent `SeedContribution`s (run in `(order, name)`); a `seed_demo` management command
  runs them, supports `--fresh` (reset) and refuses production without `--force`.
- **Phase 0 contribution** `roles_and_users`: creates the five roles as Django `Group`s and one
  deterministic demo user each (admin/itsec/itgov/ucadmin/ucuser), idempotently. Roles centralized in
  `aira_management.roles.Role` (reused by Phase 2 RBAC).
- **Mock upstream** (gateway `upstreams/mock.py`): deterministic offline completions/embeddings for
  demo mode (basic; full fidelity in FRD-104).
- **Hermetic tests**: `settings.py` uses in-memory SQLite under pytest (`"pytest" in sys.modules` —
  ordering-robust, replaced a fragile conftest env hack), so the suite needs no Postgres.
- `make seed` / `make seed-reset` wired (migrate + seed_demo).
- **Gates green**: 68 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified**: `make seed` against live Postgres created 5 groups + 5 users mapped to
  roles; re-run created nothing (idempotent); confirmed in the `aira_mgmt` DB.
- **Phase 0 (Foundation & Infra) is complete** (all of FRD-000/001/002). **Next: Phase 1 — Gateway MVP.**

---

## 2026-08-04 — FRD-001: observability baseline (backend switched to Grafana otel-lgtm)
- **Decision change**: SigNoz deprecated its Docker Compose manifests (Foundry-only), so it can't be
  embedded cleanly. Switched the local OTLP backend to **Grafana `otel-lgtm`** → `ADR-0004`
  (supersedes `ADR-0002`). Updated PRD/ROADMAP/CLAUDE.md/FRD-001.
- **Compose**: added `otel-collector` (contrib 0.157) + `otel-lgtm` (0.30) under an `observability`
  profile; collector config forwards OTLP → otel-lgtm (`otlp_grpc`). `make up` now includes
  observability by default; `make up-core` for a lean start.
- **Instrumentation**: new `aira_common.observability` (tracer/meter/logger providers, OTLP/HTTP
  export, gated by `otel_enabled`); structlog `add_trace_context` processor (trace/span ids in
  logs); Kafka header inject/extract helpers for cross-component context. Gateway auto-instruments
  FastAPI, management auto-instruments Django when enabled.
- **Gates green**: 55 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified**: ran the gateway with `AIRA_OTEL_ENABLED=true`; spans for `/healthz` +
  `/readyz` (service.name=aira-gateway, http.route, status) flowed apps → collector → otel-lgtm and
  are **queryable in Tempo**; no export errors. Grafana UI at `http://localhost:3000`.
- **Next:** `FRD-002` (seed & demo mode), then Phase 1 (Gateway MVP).

---

## 2026-08-04 — Phase 0 / Slice 3b: Angular frontend shell — **Phase 0 complete**
- Scaffolded **`management/frontend`** with **Angular 22** (latest; note: Node is 26, Angular is 22).
  Uses the new `@angular/build:unit-test` builder → **Vitest + jsdom** (no browser needed — CI-friendly).
- Replaced the default welcome page with a minimal **AIRA shell** (title/subtitle header, nav
  placeholder, `router-outlet`); updated specs (3 tests) and page `<title>`.
- Wired frontend into `make`: `test`/`test-frontend`, `lint`/`lint-frontend` (Prettier + build),
  `fmt`, `run-frontend`, and `sync` (npm install). `make test` now runs Python + frontend together.
- **Gates green**: `ng build` OK (~216 kB), 3 frontend tests pass, Prettier clean; Python side still
  41 tests / 100% coverage / ruff + mypy clean. `node_modules`/`dist` git-ignored.
- **Phase 0 (Foundation & Infra) is complete**: full local stack (`make up`) + gateway, management
  backend, and frontend skeletons, all tested and observ-ready hooks in place.
- **Next:** Phase 1 — Gateway MVP (`FRD-100` unified API, `FRD-101` auth, `FRD-102` attribution,
  `FRD-103` persistence, `FRD-104` mock upstream, `FRD-105` tracing/IP). Also still pending from
  Phase 0 plan: OTel Collector + SigNoz wiring (`FRD-001`) and seed/demo (`FRD-002`).

---

## 2026-08-04 — Phase 0 / Slice 3a: management backend (Django + DRF)
- Added **`management/backend`** as a third uv workspace member: **Django 6.0 + DRF 3.17 +
  psycopg 3.3** on Python 3.14 (src layout, package `aira_management`).
- Structure: `config` (settings driven by a typed `ManagementSettings`, `runtime.get_settings()`,
  urls/asgi/wsgi), `apps/health` (`/healthz` + `/readyz` mirroring the gateway contract, reusing
  `aira_common`), `manage.py`.
- **Type-checking**: wired **django-stubs** mypy plugin; refactored the dynamic `settings.AIRA`
  access to a typed `get_settings()` accessor so `mypy --strict` stays clean.
- **Quality gates green**: 41 tests total, **100% coverage** across gateway+libs+backend;
  `ruff`, `ruff format`, and `mypy --strict` (25 files) all pass. `make run-backend` added.
- **Smoke test**: `manage.py check` clean; runserver `/readyz` returns `ready` against the live
  Compose stack (postgres+kafka reachable, HTTP 200).
- **Next:** Slice 3b (Angular frontend shell) to close Phase 0.

---

## 2026-08-04 — Phase 0 / Slice 2: gateway skeleton + shared libs
- **uv workspace** at repo root (`pyproject.toml`) with members `gateway` + `libs`; shared tooling
  config (ruff, mypy strict, pytest, coverage gate `--cov-fail-under=90`). Python 3.14 venv via uv.
- **`aira-common`** shared lib: `config` (pydantic-settings base), `logging` (structlog JSON),
  `errors` (AiraError + ErrorResponse envelope), `events` (EventPublisher protocol +
  InMemoryEventPublisher; real Kafka transport deferred to Phase 1), `health` (async TCP checks).
- **`aira-gateway`** skeleton (FastAPI): app factory (`create_app`), `GatewaySettings`,
  `/healthz` + `/readyz` (probes Postgres + Kafka), AiraError exception handler, `main:app` entry.
- **Quality gates green**: 32 tests, **100% coverage**; `ruff check`, `ruff format --check`, and
  `mypy --strict` all pass. Wired `make sync/test/lint/fmt/run-gateway`.
- Note: on Python 3.14, ruff formats multi-type excepts with PEP 758 syntax
  (`except TimeoutError, OSError:` — no parentheses); valid and intended.
- **Smoke test**: ran the gateway against the live Compose stack — `/readyz` returns `ready`
  with postgres+kafka reachable (HTTP 200).
- **Next:** Slice 3 (management backend skeleton: Django + DRF) + Angular workspace shell.

---

## 2026-08-04 — Phase 0 / Slice 1: infra stack + toolchain
- **Toolchain** (ADR-0003): confirmed Python 3.14.4 + uv 0.9.26 present. Installed **Node 26.6.0**
  via nvm; worked around `NPM_CONFIG_PREFIX` (unset in persistent env) and symlinked node/npm/npx
  into `~/.local/bin` (first on PATH); installed system lib `libatomic1` (Node 26 dependency).
- **Monorepo skeleton**: `gateway/`, `management/backend/`, `management/frontend/`, `libs/`,
  `deploy/compose/` created.
- **Docker Compose infra** (`deploy/compose/`): postgres 17, keycloak 26.1, kafka 3.9 (KRaft),
  schema-registry 7.8, vault 1.18 — with healthchecks, `.env.example`, postgres init script
  (creates `aira_gateway`/`aira_mgmt`/`keycloak` DBs), and a root `Makefile`
  (`up/down/destroy/ps/logs` + stub `test/lint/fmt/seed`).
- **Brought up & verified healthy**: postgres (DBs created), kafka (fixed a KRaft
  `advertised.listeners 0.0.0.0` error → use `://:PORT` + `localhost` quorum), schema-registry
  (API responds), vault (unsealed).
- **Keycloak**: initially blocked (quay.io 403); resolved after the host allowed quay.io. Image
  pulled, service healthy, OIDC discovery reachable at `/realms/master/.well-known/openid-configuration`.
- **Slice 1 complete**: all five infra services (postgres, keycloak, kafka, schema-registry, vault)
  up and healthy via `make up`.
- **Next:** Slice 2 (gateway skeleton + shared `libs/`).

---

## 2026-08-04 — Git init + Phase 0 FRDs
- Initialized the Git repository (branch `main`) and added a `.gitignore` (Python, Node/Angular,
  secrets/`.env`, Docker data volumes).
- Wrote the three **Phase 0 FRDs**:
  - `FRD-000-foundation-infra` — monorepo layout, Docker Compose stack (Postgres, Keycloak, Kafka
    +schema-registry, Vault), service skeletons, shared `libs/`, CI + coverage gate, Make targets.
  - `FRD-001-observability-baseline` — OTLP → OTel Collector → SigNoz, app instrumentation, trace
    context propagation over HTTP + Kafka, correlated logs/metrics.
  - `FRD-002-seed-and-demo-mode` — `DEMO_MODE`, mock upstream (basic), idempotent extensible
    seed framework covering all five roles, deterministic data.
- **Next:** implement Phase 0, starting with `FRD-000` (Compose stack + skeletons + CI).

---

## 2026-08-04 — Project kickoff & planning foundation
- Established project vision and scope; created **`docs/PRD.md`** (Project Requirements Document v0.1).
- Created **`docs/ROADMAP.md`** — phased delivery plan (Phase 0–7).
- Added **`docs/features/FRD-TEMPLATE.md`** and **`README.md`**.
- Locked key decisions:
  - Management UI = **Angular + Django REST Framework** → `ADR-0001`.
  - Local observability = **OTel Collector + SigNoz** (alt: Grafana LGTM) → `ADR-0002`.
  - Docs & code in **English**; **Docker Compose** locally; **automated seeding** + demo mode required.
- Created **`CLAUDE.md`** (project guidance) and set up **`docs/adr/`** (ADR process + first two ADRs).
- **Next:** write Phase 0 FRDs (`FRD-000` foundation, `FRD-001` observability, `FRD-002` seed/demo),
  then begin implementation of Phase 0 (Foundation & Infra).

## 2026-08-08 — The security round: every finding fixed, nothing taken away

A full read of the code after four weeks on authentication, roles and group grants
(`ADR-0015`, `FRD-406`). The instruction was to fix every finding **and keep the framework's
functionality**, which turned out to be the harder half and the more interesting one: the demo,
the published demo key, `?key=` authentication, the CLI break-glass key, a laptop's
zero-configuration start and a useful `/readyz` all had to survive their own fixes.

**The one that mattered was found by sending a request, not by reading.** The KIRA surface asked
`if memberships and header not in memberships` — so an **empty** membership list meant "anything
goes" rather than "nothing". A caller belonging to no use case at all could send
`X-AIRA-Use-Case: somebody-elses`, get a real answer, and have the tokens billed to that use
case's budget and written into its audit trail. The Gemini surface refused the identical request
in both of its selector forms. Proven live; `request_logs` showed the row, attributed to the
victim. Cause: the rule existed correctly in one place and was **restated by hand** on the second
surface — the same shape as `FRD-126`'s pre-dispatch order, `FRD-206`'s permission predicates and
`FRD-602`'s export scope. It now lives in `use_case_refusal`, returns a *reason* rather than
raising, and each surface wraps it in its own envelope, which is the only thing that should
differ. The deliberate exception survives inside it: an **unbound** API key stays unrestricted,
because break-glass exists for the moment the control plane is gone.

**A convenience default is a production default, one variable away.** `ADR-0007` made Management
refuse to boot outside `local` with development defaults; the gateway — which serves the traffic,
holds the upstream credentials and writes the audit trail — read `environment` for telemetry and
acted on it nowhere. `aira_gateway.security` now mirrors it: open routes, the published Postgres
password, and OIDC with no audience each stop the process, all reasons named at once. The check is
**environment-shaped rather than stricter defaults**, and `AIRA_DEMO_MODE` exempts outright — a
hardening pass that breaks the demo is a hardening pass that gets reverted.

Six more, each with the same character — a rule that was right in one place and absent in another:

- **A credential was redacted in the trace backend and written verbatim to the access log.**
  `?key=` has been kept out of exported spans since `ADR-0007`; the web server's own request line
  went to stdout intact, which is the *more* widely readable of the two. A logging filter now
  rewrites the arguments (not the formatted message — uvicorn formats after filters run), and
  deliberately redacts any string argument carrying a query rather than the one positional index
  uvicorn happens to use today.
- **A claim that is absent is not a claim that passed.** PyJWT verifies `exp` when present and
  accepts a token carrying none — a credential that never expired. `exp`, `iat` and `sub` are now
  required. The audience stays optional in the verifier and **required by deployment**, so a
  laptop keeps working against a realm with no audience mapper.
- **The verdict is public; the diagnosis is not.** `/readyz` stays unauthenticated (a probe
  carries no credential, and one answering 401 reports every pod unhealthy), but the body naming
  the database host, Kafka host, every upstream and the current fallbacks now needs an
  authenticated caller. Locally, everything is shown.
- **A control that needs a verified identity cannot bound a caller who has none.** Every limit
  `FRD-405` built is keyed by use case or member. Authentication *failures* are now bounded per
  source address — **counting refusals only**, so a working credential never touches the bucket
  and the bound can be low enough to be worth having. Behind an untrusted proxy the whole
  deployment shares one bucket, and that is tolerable *because* of the refusals-only rule: the
  worst case is somebody else's typo answered 429 instead of 401.
- **A key with no end date has to be inventoried.** First shipped as an *optional* expiry, on the
  argument that "an expiry which cannot be omitted is one somebody sets to the year 3000" — and
  that argument is about the **maximum**, not the default. Corrected the same day: every key is
  bounded, `AIRA_API_KEY_DEFAULT_DAYS` (30) applies when nobody names one and
  `AIRA_API_KEY_MAX_DAYS` (180) is refused past rather than truncated. Neither plane can mint an
  unbounded key, the break-glass CLI included — a credential minted by hand during an incident is
  precisely the one that outlives its reason. Keys issued before the change keep working and are
  marked "no end date" in the console: expiring them would be an outage chosen on the operator's
  behalf, and a silent one.
- **`create_all` beside Alembic** let a partially-deployed stack undo a migration (`FRD-114`
  recorded it happening). It now runs for SQLite only, and the consumer — which is where it bit —
  does not call it at all, since it already waits for `alembic upgrade head`.

**`FRD-406` finally does something.** The `Redactor` hook has existed since Phase 1 and was a
no-op, so a stored prompt was a verbatim copy of whatever a caller sent. The redaction is
deliberately **narrow**: credential shapes only (AIRA keys, `AIza…`, `sk-…`, `Authorization:`,
JWTs, PEM private key blocks), because names, customer numbers and prose are *the work* — a
redactor that mangles them produces payloads nobody uses, and the deployment then switches storage
off entirely, which is strictly worse than storing them. An unusable pattern **stops the gateway**
rather than silently matching nothing, which is the `FRD-125` failure exactly: an absent control
wearing a present one's badge. Deployment patterns are **additive**, never replacing, or the first
organisation to name its own token format stops redacting Google keys.

Two test notes. The redaction requirement is proved twice on purpose — once against the class and
once by posting a prompt at the route and reading the stored row back, because `FRD-124` and the
CSV export both recorded on one day that a requirement exercised only against the class leaves the
wiring undefended and coverage cannot see the difference. And the authentication-bound tests run
with `redis_url=""`: the first version failed at the *first* request because a Redis left running
on the machine still held the bucket from a previous run — a bound that is a property of the
process must be tested as one.

Mutations `H1`–`H20` (the `S` prefix was taken; the harness refuses a duplicate id, which is how
that was caught). Two self-inflicted findings worth recording, both about *where* a thing was
written: the new mutation block was inserted into `survivors = [...]` in `main()` instead of into
`MUTATIONS`, so all seventeen were reported "undefended" **by construction** and none had ever
run; and a `validate_<field>` method does not execute for a field the caller **omitted**, which is
exactly the case that had to end with a date — every existing test issued an unbounded key straight
past it. Both were caught by a suite that disagreed with the code, which is the point of having
one. `A4` was **re-anchored**: adding the required-claims list turned the JWT
options into a multi-line dict, and a mutation whose anchor has moved protects nothing.

## 2026-08-08 — Agents and coding assistants: the gap, measured against the code

The named use cases are a RAG chatbot, semantic search over embeddings, and **connecting coding
assistants such as OpenCode**. Checked against the code rather than the documentation, two of the
three already work and the third does not work at all.

- **Semantic search**: covered. `:embedContent` with batching and task types (`FRD-113`), budgeted,
  priced, audited. Vector storage is the caller's by `ADR-0013`.
- **RAG chat**: covered for the generation half — documents (`FRD-110`), structured output,
  streaming, `systemInstruction`. Retrieval is the caller's.
- **Coding assistants**: blocked on one field. `tools` and `toolConfig` are refused with a **400**,
  and an assistant's entire loop *is* tool calling — it asks which file to read, gets a function
  call rather than prose, executes it itself, and sends the result back as the next turn.

**The refusal is right and its stated reason is wrong**, which is the finding worth keeping.
`api/gemini/schemas.py` cites `ADR-0013` — but that ADR says, in the same words it has always had:
*"The gateway may pass a tool definition through … but never executes anything."* Passthrough is
explicitly in scope; execution is not. The message conflates them, so a reader arriving at that
error concludes the whole area is closed by decision. The real reason is different and was written
down nowhere: **`CanonicalRequest` has no field a tool declaration could travel in.** That is a
capability gap, and a capability gap gets built; a boundary does not. `ADR-0013` now says so
explicitly, and the same clarification was needed for caching: a cache *handle* (Google's
`cachedContent`) is provider-side state and stays refused, while a cache *marker* on content the
caller sends in full every time leaves the request self-contained and is a price, not a boundary.

Three FRDs, in an order the owner set:

- **`FRD-131`** — tool calling carried through the canonical core, never executed. **Per use case,
  default off**: a use case that summarises documents has no business declaring functions, and the
  smallest set that needs it is the right set to have it. Catalog capability, checked **per hop** so
  a fallback skips an incapable candidate rather than answering without tools — a 200 that a client
  will then parse as a function call. Two traps written down before they are hit: on Anthropic,
  structured output is *already implemented as a forced tool call* (`FRD-119` §5.5), so tools plus a
  response schema is refused by name rather than silently losing one; and in the OpenAI dialect a
  tool call's arguments arrive **fragmented across chunks**, so a naive mapper emits several half
  calls.
- **`FRD-132`** — which surface an assistant needs, **measured before it is chosen**. Stage A points
  OpenCode at the running gateway and records what actually breaks; stage B builds a surface only if
  stage A says so. Reviving `FRD-106` is much cheaper than when it was withdrawn, because
  `FRD-123` built the OpenAI dialect as an upstream and a surface is largely its inverse. This
  project has repeatedly learned that a contract chosen by reading is a contract maintained forever.
- **`FRD-133`** — prompt caching, written now and **built last, by owner decision**: the assistant
  work must stand at full price so the saving is decided from `request_logs` rather than from an
  estimate. The FRD keeps open the possibility that the measurement says don't build it.

Two governance consequences recorded before they surprise anybody: an assistant makes **many model
calls per human instruction** — the `FRD-125b` shape at scale, except the calls are genuinely the
caller's — so limits and budgets calibrated for a chatbot trip immediately and "requests" means
something different on the reporting screen; and **a tool result is content the model reads**, which
the injection filter cannot see. Same blind spot `FRD-110` recorded for PDFs, one step sharper,
because the content comes from the caller's own machine and the model is about to propose the next
command.

**Five stale tests, each stale for a different reason, and each found by a layer above the one
that could have prevented it.**

*A shared database accumulates other people's global rules.* Three anomaly tests asserted on
`events[0]` after a tick. A tick evaluates every rule that applies to the scope — and a **global**
rule (`use_case IS NULL`) applies to every scope there is. Sixteen `alert` rules left behind by
earlier e2e runs meant `events[0]` was somebody else's finding, so the assertions read `alert`
where they expected `blocked`, `throttled` and `detected_not_enforced` while the product was doing
exactly the right thing. They now select **their own** `rule_id`. Latent since the rules were
written, visible only once enough junk had piled up — the worst kind of test to leave standing,
because it fails long after the change that exposed it.

*A test I wrote hours earlier and never ran.* The e2e case for key expiry asserted that leaving the
lifetime empty produces a key showing "never" — true when I wrote it, false the moment the bound
landed, and I updated the unit and API tests without touching it. It now asserts the opposite,
plus that the form states the policy the server enforces. **A test that has never been executed is
not a test**, and this one was written, committed to a file, and left unrun until the suite was
finally invoked.

*A one-line CSS rule whose effect nobody had measured.* The e2e layout suite failed on the
API-keys tab at phone width: **428px of document in a 360px viewport**. The offender turned out to
be `.sr-only` — the visually-hidden "Actions" column header. It is `position: absolute` with **no
positioned ancestor**, so it keeps its *static* position relative to the **document** rather than
to the card that scrolls it. In a six-column table that sat at x≈427, and the whole page grew a
horizontal scrollbar. My new "Expires" column is what pushed it over the edge; the rule had been
wrong since it was written, and the rate-limits tab was quietly 8px over as well — never seen,
because the loop failed on the keys tab first and stopped.

Fixed with `left: 0` on `.sr-only`, which removes the static position from the equation for every
present and future use. **Not** by making `.table-wrap` a containing block: that would also clip
the info-hint popups, which are absolutely positioned inside table headers on purpose and are
*meant* to escape. Nothing moves visually — `clip-path: inset(50%)` hides the element wherever it
sits. Every detail tab now measures exactly 360.

*And a test that was measuring more than it claimed.* `a live view refreshes without moving
anything` observed layout shifts with `buffered: true`, so it counted the **initial render** as
well as the refresh ticks it was written for. With 1919 findings in the database that render
reflows once — 0.0207 at t=101ms, a card arriving after the shell, which is ordinary for a
data-driven page and is not a control moving under the reader's cursor. Measured, then narrowed to
what the name promises: shifts *after* the page has settled, still required to be exactly zero.

**Also fixed:** `test_management_api.py` scanned the use-case list as a plain array and failed after
the rebuild — server paging (`FRD-208`) had changed the body to `{count, …, results}` the same week,
and the integration layer is not part of `make ci`'s hermetic half, so nothing had told it. It now
**searches** rather than scanning, which it needed anyway: with hundreds of use cases in that
database, page one would not have contained a slug created a second earlier however the body were
shaped. The e2e round hit the identical trap with `ensureUseCase` on the same day.

## 2026-08-08 (later) — Stage A: OpenCode against the gateway, and a thinking mode that was not

`FRD-132` stage A, run rather than reasoned about. Ollama in the stack, `qwen3:4b` pulled because
it declares `tools`, a use case with a bounded API key, OpenCode 1.18.15 from npm pointed at the
**existing Gemini surface** via `@ai-sdk/google` with an overridden `baseURL`
(`tools/opencode/opencode.json`).

**The answer is B1: no new surface is needed.** Provider resolution, base URL, auth, model
selection, plain generation and SSE streaming all worked unmodified, and the client failed at
exactly one thing — `tools`, refused by name with the message `FRD-124` gave it. Reaching that
refusal is the successful outcome: everything up to the missing capability held, and the missing
capability is `FRD-131`, already specified. `FRD-106` stays withdrawn, and this run is the evidence
that was absent when it was withdrawn.

**One trivial instruction produced three gateway requests** — one served, one refused, one
`client_gone` — every one of them on the audit trail. §5's warning about assistants is now a number
instead of a caution: limits and budgets sized for a chatbot are wrong for this shape by a
multiple, and "requests" on the reporting screen counts a different thing here.

**The finding that had nothing to do with surfaces.** `reasoning_effort: "none"` does not mean "do
not think"; it means "do not emit a separate reasoning channel", and those are the same thing only
on some models. Measured on one Ollama, one prompt, within a minute of each other:

| | `qwen3:0.6b` | `qwen3:4b` |
|---|---|---|
| field omitted | 115 tokens, content `"OK"` | 132 tokens, content `"OK"` |
| `"none"` | **3 tokens**, content `"OK."` | **103 tokens, content = 480 chars of raw chain-of-thought** |
| `"minimal"` | — | **400** `invalid reasoning value` |

The dialect maps `disabled` → `"none"` on a measurement recorded in the code — against the 0.6B
model, where it is correct. On the 4B model of the **same family, same server, same minute**, the
same mapping returns somebody's thinking *as the answer*, billed, with a 200. And the seed declared
`disabled` as the **default** for whatever model was configured, so this was the ordinary path, not
an edge case. A coding assistant against it would have received "Hmm, the user just asked me…" as
every answer.

Fixed as **data, not code**: both seeds now key the thinking declaration by model, from a
measurement, and a model nobody has measured gets no thinking declaration at all (`FRD-114` FR-7 —
absence of information is not permission). `qwen3:4b` no longer offers `disabled`, so `FRD-111`
refuses a request asking for it **by name**, which beats a 200 carrying reasoning. `minimal` is
gone from the `tools/` seed as well: the identical correction had been made in the *Management*
seed on 2026-08-06 and the second copy was never updated — one definition, two files, one of them
fixed.

The rule worth carrying: **a capability belongs to a model, not to a family, a vendor or a
runtime.** A declaration measured against one model is not evidence about its siblings, and the
seed that writes one declaration for "whatever model is configured" is the mechanism that turns a
measurement into an assumption.

## 2026-08-08 (later still) — `FRD-131` stages 1–4: a function call goes through the gateway

Tool calling, built in four stages, each run against the whole existing suite before the next —
the instruction was to add the capability without shooting anything down, and that is the part
worth describing.

**Stage 1, the canonical core.** `ToolCallPart` and `ToolResultPart` join `TextPart` and
`DataPart` in the ordered-parts union `FRD-110` created; `CanonicalRequest.tools` carries the
declarations; `CanonicalResponse.tool_calls` and `CanonicalChunk.tool_calls` carry the answer. The
whole suite passed unmodified, which was the bar `FRD-110` set. One existing rule needed changing
and it is the interesting one: `is_empty` refused a request with no text and no attachment, and
**the second turn of every agent exchange is exactly that** — nothing but "here is what `read_file`
returned". A tool result now counts as content, or the ordinary middle of an agent conversation
would have been refused as a no-op.

**Stage 2, the Gemini surface.** `functionCall` and `functionResponse` left the refused-parts list
and `tools` left the refused-fields list. Five existing tests failed, all of them asserting the
*old decision* rather than a property — they moved with it, and the docstrings say why. Google
sends no call id and the other two dialects require one, so an id is generated where absent.

**Stage 3, the OpenAI dialect** — the path to Ollama. Declarations become `tools`, a tool result
becomes a message of its own with `role: "tool"`, arguments travel as a JSON *string*. The trap the
FRD named before anything was built is real and is now handled: **a streamed tool call arrives in
pieces**, name once and arguments as fragments across deltas, so `StreamedToolCalls` accumulates by
index and emits whole calls on the chunk that ends the message. Unparseable arguments keep the name
rather than failing the request — a model's mistake should not be hidden behind ours.

**Stage 4, governance.** `tools_enabled` per use case, **default off** (migration `0020`,
`server_default false`), read only when a request actually declares tools so an ordinary request
pays nothing. A `tools` capability in the catalog, checked **per hop**, so a fallback skips an
incapable candidate instead of answering in prose to a client that will parse it as a function
call. The mock upstream answers a tool request with a call, because otherwise the feature would
only ever be exercised against a model nobody has in CI — the state `FRD-110` refused to leave
attachments in.

**mypy caught what no test could.** Three adapters iterated `message.parts` and treated "not
`TextPart`" as "an attachment". Widening the union made that untrue, and a tool part reaching those
loops would have been an `AttributeError` at runtime, on the Gemini and Anthropic upstreams, in
production. Now each checks `isinstance(part, DataPart)` explicitly and raises `DialectUnsupported`
otherwise. That exception then had to **move out of the OpenAI dialect** into `upstreams/base.py`:
two other adapters needed it, and importing it from a sibling dialect is precisely the import the
architecture assertion caught once before with `to_json_schema`. A thing every dialect needs was
never one dialect's to own.

**And a measurement corrected a rule I had asserted.** `toolConfig` was refused outright, on the
argument that its modes "hold on one vendor and silently do not on another". Then OpenCode was
pointed at the gateway and sent `{"functionCallingConfig": {"mode": "AUTO"}}` on **every** request —
and `AUTO` *is* the default: it asks for exactly what happens when nothing is sent. The blanket
refusal blocked the whole use case in the name of a fidelity problem that mode does not have. Now
`AUTO` is carried and `ANY`/`NONE` are refused **by name, because they are not built** — which is
an honest reason, unlike a claim about vendors nobody had measured. Same shape as the `tools`
refusal itself: a real capability question answered from the armchair.

**Proven against the running stack.** A real request with a real model:

```
POST /v1beta/models/qwen3:4b:generateContent  {"tools": [{"functionDeclarations": [read_file]}]}
→ {"functionCall": {"name": "read_file", "args": {"path": "hello.py"}}}, TOOL_USE, 487 tokens
```

and with OpenCode's own three-tool shape, `list{path: "."}`. Priced, budgeted and on the audit
trail like everything else.

**Not yet done, and stated rather than implied:** the full OpenCode loop still ends in a
`ReadTimeout` — a 4B model on CPU takes **86 seconds** for a three-tool request and OpenCode sends
ten plus a long system prompt. That is model speed, not a gateway defect: the same request answered
correctly when given the time. Tool calls on the **Vertex** dialects (Gemini upstream, Anthropic)
are not built either; both refuse a tool part by name, and the catalog capability keeps such a model
out of a tool request in the first place.

## 2026-08-08 (night) — `FRD-131` FR-7: the audit row learns what the model asked for

Stages 1–4 were called done, and a live OpenCode run said otherwise. The audit row of a real
assistant turn read `{"text": ""}` and nothing else.

**The cause is worth keeping.** The streaming path builds its stored response by accumulating
`text_delta`. A tool call has **no text delta** — the answer *is* the call — so the row was
literally correct and completely useless: tokens and cost recorded, and no trace of what the model
asked to have run. The buffered path stored it in full, because the whole response object went into
the payload. One feature, two exits, one of them blind: `FRD-126`'s lesson arriving through a door
it had not been pointed at.

For a coding assistant this is not a detail. Every such client streams, so *every* tool call in
real traffic was unrecorded — and "which functions did the model ask to run" is exactly the
question `ADR-0013` promises the gateway can answer. A platform whose point is auditable model
access had the least auditable part be the one that matters most.

**Closed as one fact on the trail, not two at the exits.** `AuditTrail` gains `tools_declared` and
`tool_calls`; `Accounting.served()` records the names, so both exits get it by calling the same
method rather than by each remembering; `tool_summary()` is an **allow-list** in the shape
`FRD-122` established — names and counts only. Arguments stay out: they are caller content and
belong under `store_payloads`, inside the retention clock and behind `FRD-406`'s redaction, not in
a metadata column no clock covers. Migration `0021`.

`declared` is recorded beside `called` because **"offered ten functions and asked for none" and
"offered none" are different events**, and only one of them is a model behaving oddly. A request
that declares nothing stores NULL — a column that is never NULL stops being evidence of anything.

**And the client was not receiving them either.** The chunk mapper carried only `text_delta`, so a
streamed tool call reached nobody: the audit was blind *and* the answer was lost. Both fixed
together, which is the honest framing — recording something is not the same as delivering it.

The mock now emits a tool call on the final chunk, exactly as a real dialect does. Without that the
streamed path had no hermetic coverage at all, which is how the gap survived stages 1–4 and had to
be found against a running model.

Verified live, same command as before:

```
streamGenerateContent  545   NULL                                  ← the client's own title call
streamGenerateContent  2099  {"declared": 10, "called": ["read"]}
streamGenerateContent  2115  {"declared": 10, "called": []}
```

**Model selection, measured.** `qwen3:4b` calls tools correctly and spends 352 completion tokens
and 86 seconds doing it, almost all of it discarded reasoning. `qwen2.5-coder:7b` is fast and
**cannot call tools at all** — it returns the JSON as prose with `tool_calls: null`, while
`ollama show` lists `tools` as a capability. `qwen2.5:3b` calls correctly in **2 seconds and 21
tokens**, a factor of 43 against qwen3:4b with better arguments. `qwen2.5:0.5b` called correctly
once and was then used to argue that the family can do it at any size; asked again it answered in
prose, then called with invented arguments, once naming a parameter that is not in the schema.

Which produced the rule the seed now states: **a vendor's capability flag is a claim, not
evidence, and one successful call is not a capability.** `TOOLS_BY_MODEL` holds what was *seen*, is
appended to only after a run, and a model absent from it declares no tool calling — so the dispatch
chain refuses it by name rather than letting prose reach a client that will parse it as a call.
That entry was written for `qwen2.5:7b` while it was still downloading and taken out again before
the file was saved: the fourth instance in one evening of the same reflex.

## 2026-08-08 (night, later) — tool calling on all three dialects, and a matrix instead of anecdotes

**Gemini and Anthropic now carry tool calls too.** Both had raised `DialectUnsupported` since the
part union widened; the catalog capability kept a tool request away from them, so nothing was
broken — the feature simply only existed on one of three wire formats.

Two dialect facts worth keeping. **Google sends no call id** and matches a result to a call by
*name*, so an id is generated deterministically — otherwise a conversation begun there could not be
continued on the two dialects that require one. And **`functionResponse.response` is an object**,
not a string, so a result is parsed back and a non-JSON one wrapped; the canonical model keeps text
because two of three want it. Google also sends a function call **whole in one chunk**: no
accumulator was written for it, because a mechanism defending against a problem a wire format does
not have is a mechanism nobody will maintain correctly.

**Anthropic is where the collision the FRD predicted actually lives.** `input_json_delta` means two
different things on that dialect and only `content_block_start` says which: for a structured
request the fragments **are** the answer and stream as text (`FRD-112` depends on it), for one of
the caller's tools the identical fragments are arguments and must be accumulated — streaming them
as text would send `{"pa`, `th": "he` to the client as the model's reply. And `aira_structured_
output` is itself a `tool_use` block, filtered out of the reported calls: returning it would hand
the caller a function they never declared.

The **tools-plus-schema conflict is a dispatch decision, not a mapping error**: structured output
on that dialect *is* a forced tool call, so one field would have to serve two purposes and one of
them would silently lose. `ToolsAndSchemaTogether` skips the candidate by name, exactly as
`SamplingExpressible` does for `top_k`, and each adapter declares `tools_with_schema` — absent
means "cannot".

### The matrix

Asked whether the edge cases were covered, the honest answer was **no, not systematically** —
individual cases existed, a matrix did not. `test_tool_calling_matrix.py` is organised by *where in
the path* × *what is wrong with it*: the declaration, the replayed turn, the model's answer, the
stream, governance, and the audit row seen as evidence. Writing it found three things the code had
never decided:

- **a function name nothing can call** (empty, or with spaces and dots) was accepted and would have
  been rejected downstream with a message naming neither the tool nor the field;
- **the same name declared twice** was accepted — and a call to it cannot be matched to one
  function, so the caller would run whichever their code found first;
- an **empty `tools: []`** must stay identical to sending none, or a client that always includes
  the field is refused by the use-case gate for asking nothing.

The first two are now refused at the surface, where parsing belongs. One deliberate **non**-decision
is recorded too: a tool result answering no call in the history is carried, not policed —
`ADR-0013` says the gateway governs model access, not the caller's conversation.

The matrix also caught a sloppy assertion of my own: `"functionCall" not in response.text` passed
for the wrong reason, because `Part` serialises all four shapes and the string appears as a null
field. Asserted on the parsed answer now.

### And a caching setting made a test suite fail

`make ci` went red on **twelve frontend tests**, all timing out at five seconds. Nothing to do with
the code: `uptime` said load average **103** and 118 MB free. `OLLAMA_KEEP_ALIVE=30m`, which I set
an hour earlier so an agent loop would not reload its model between turns, had pinned **two models
(7.8 GB)** in a 15 GB box for half an hour, with `NUM_PARALLEL=2` making it two rather than one.

Lowered to Ollama's own default of 5 minutes, and the models unloaded: 9 GB free, 502 frontend
tests green. The lesson is the coupling itself — a *caching* setting starved a *test suite*, and
nobody would look there. A knob that trades memory for latency needs a number that fits the machine
it runs on, not the workload that motivated it.

## 2026-08-08 (night, last) — the console side of it: who may ask what, and a config that runs

Three things the owner asked for before going to sleep: collect as much about AI usage as
possible, show each role only what it may see (with a *"only my own requests"* toggle even for
those who see everything), let IT Security find a compromised client or system as fast as
possible, and put a button on API-key issuance that produces an OpenCode configuration. Tool calls
first, because that is where an assistant's behaviour actually shows.

### The reporting screen IT Security could not read

`visible_scope` — the one function `FRD-601` deliberately put the visibility rule in — asked
`principal.is_governance`. Governance and oversight differ by exactly one role: **IT Security**.
So the role whose job is investigating an incident got the "you are in no use case" branch: an
**empty** report and an **empty** trace list, on the screen built for it.

It survived because every test asserting "an oversight role sees everything" used a *global admin*,
which satisfies both predicates. It was found while writing a test for something else entirely,
which is the third time this month that a defect has come out of a test aimed elsewhere. `N40`
mutates the predicate back and both suites now catch it.

### What a trace has to carry before it is evidence

`tool_calls` joins the row (`FRD-131` FR-7), and the list learned the filters an investigation
actually opens with: **which system** (the API key's prefix), **whose identity**, **which
machine**, plus *only my own requests* and *only the turns where the model asked for a function*.

Two rules decided the shape:

- **`source_ip` is a different kind of fact.** It identifies a machine, not a use case, so it is in
  `INCIDENT_FIELDS` and reaches only a role that may act on an incident. Asking to filter by it
  without one is **refused with a 403, not ignored** — a filter that silently does nothing lets
  somebody conclude an address made no requests, which is the opposite of what the screen told
  them.
- **A filter must not widen the scope.** Every one of them is applied *after* `visible_scope`, so
  "only my own requests" narrows and can never reveal.

The console offers the address field on the same condition, and the predicate is now **one
definition** (`core/auth/roles.ts`) rather than a role list retyped in the screen that needs it —
`it-security`/`global-admin` had been written by hand in the security console, which is precisely
the shape of the 2026-08-07 finding where `it-steuerung` could stop traffic in one plane and not
the other.

### A configuration built at the only moment it can be

The OpenCode config is generated **at issuance**, because the plaintext key exists for exactly
that moment. Offered on any later screen it could only carry a placeholder — and a placeholder is
what somebody pastes and then debugs for twenty minutes. It names only models whose catalog entry
**declares** `tools`: `FRD-114`'s rule at the console, where undeclared means unsupported. An
assistant pointed at a model that answers in prose is the failure `FRD-131` exists to prevent.

### The defect the tests found

`loadMore` rebuilt its query by hand, so page two was fetched **without** the filters page one was
fetched under — the reader turned the page and silently got back the rows they had just excluded.
One list, two questions, no error anywhere. Both call sites now read a single `query()`; shown to
fail against the old code before the fix went in.

That is `FRD-126`, `FRD-206`, `FRD-602` and the KIRA membership bypass in one sentence again: **a
rule restated by hand at a second call site is a rule that will disagree with itself.** The fix is
never "remember both" — it is one function with two callers.

Frontend: 524 tests, branch coverage back above its gate by adding tests, not by moving it.

### The edge round, and an error shape nobody could read

31 live cases over the new surface, each asserting the three things `test_edge_cases.py` asserts:
never a 500, a status the caller can act on, and a message that names the problem. Two shapes were
worth walking twice — every combination of filters (contradictory, empty, absurd) must **narrow**,
and a tool call's *arguments* must never reach the metadata column, because a file path or a
customer number is content and this list is readable by every oversight role.

One finding, from `limit=100000`: FastAPI answered **422** with its own `{"detail": [...]}` list.
Every other error this API produces is `{"error": {code, message, status}}`, so a Google client
handed the framework's shape reports "unknown error" and the caller never learns that `limit` has a
maximum of 200. That is the *same* finding as the routing handler added on 2026-08-06 — a wrong
URL answered in the framework's shape — one layer in, and it had been there since the first typed
query parameter.

Now **400 `INVALID_ARGUMENT`, naming the parameter**. 400 rather than 422 because the caller's job
is to fix the request, which is what the rest of the surface says with that number. The KIRA
surface keeps its own `422` + `code`/`details` envelope — its routes parse their own bodies, and a
client migrating by changing a URL must keep receiving the errors it already handles. The handler
picks the envelope by path, like the routing one; the KIRA branch is **unreachable through the
published surface today**, which is stated in the test rather than left for somebody to discover,
because the next KIRA route with a typed parameter would otherwise answer in Gemini's envelope
silently.

### An id that identified two models

The same round put a **500** on the predecessor's surface: `MultipleResultsFound`, because two
catalog rows claimed numeric id `9001`. `tools/seed_local_catalog.py` writes a **fixed** id for
"the local chat model", and it had been run for a second one — so both kept it, and every KIRA
request naming that id failed. Silently created: the seed printed success, and the gateway's
read-model has no unique constraint (Management does, but this script writes past it).

Fixed at all three places it could have been stopped. The **resolver refuses** — this is
`ADR-0011`'s ambiguous routing table one level down, and picking a row would answer, bill and audit
under a model the caller never named, with nothing in the response looking wrong; `503`, because
the installation is misconfigured and an administrator can fix it, with the two model names in the
log rather than in the answer. The **seed releases the id** before taking it, since the number names
a *role* and re-running for a different model must move it — fixed rather than derived, because a
caller's configuration holds that number and changing it would break them silently. And the live
catalog was cleaned.

Two integration rows were **retired rather than deleted**: `tools` sat on the "fields this gateway
does not serve" lists until `FRD-131` served it this morning. The requirement did not go away, it
moved — a use case without the toggle refuses a declaration by name — and a row deleted without a
comment reads as a requirement somebody dropped.

## 2026-08-09 — The requests view, read by somebody who did not build it

A walkthrough of yesterday's screen produced eight findings across two rounds. They were one
complaint in different shapes: **the view assumed the reader already knew the answer.**

The sharpest one was also the most embarrassing. The source address was added as a **filter** and
not as a **column** — so an investigator could search for an address the screen never showed them.
A filter narrows a lead; it cannot produce one, and I had built only the narrowing half.

### Three defects, none visible to a hermetic test

**A 200 rendered in red.** `outcome` arrived with `FRD-122`, so every row written before it is
NULL, and the badge fell through to its danger branch printing the status. A status column that
calls a success a problem is the one thing it must never do.

**The control that opens a request was off screen.** It was the last column, and the table scrolls
sideways now that it carries a use case and an address. Reported as *"the button was hidden behind
the scroll, I did not even know it was there"* — which is the accurate description of an action
that does not exist. First column now, and the whole row opens.

**Three info hints that said nothing.** `InfoHint` takes its explanation as projected content; I
wrote `text="…"`, which is not an input, and Angular ignores an unknown attribute on a component
silently. The panels opened empty — *precisely* the defect the component was built to prevent,
since `FRD-206` shipped info buttons as `title` attributes that displayed nothing and this
component was the fix.

### The guard against it was itself inert

Worth more than the fix. The first version was an Angular spec using `import.meta.glob` to read
every template, and it **did not work**: those specs run in a browser, the glob is unavailable at
runtime, and the file failed to *load* — Vitest reported "0 tests" for it while the run's total
stayed green, and my grep for the pass count showed 535 and told me nothing.

Found by breaking a template on purpose and watching nothing happen. It lives in the Python suite
now, which has a filesystem, and it was shown to fire.

**A guard that cannot fail is the thing it guards against, one level up.** Third time in this
repository that a new test had to be broken before it could be believed — and the first time the
test in question *was* the safety net.

### And the deferral that could never be discharged as written

`ADR-0009` deferred per-request browsing until `FRD-406` made it safe. `FRD-406` then shipped its
credential half and **declined its PII half on purpose**, because names and customer numbers are
what a payload is stored *for* and a redactor that mangles them ends with storage switched off.

So the redactor was never going to discharge that deferral: the sensitive content and the useful
content are the same content. `ADR-0016` grants the view on a different condition — a named set of
roles, and **every read writes a record** naming who read what, when and on what authority, written
*before* the content is returned. The boundary is still crossed. It is now crossed visibly.

IT Steuerung reads none of it: every figure about every use case, no content. Visibility and
content are different answers, which is the same split `FRD-206` had to make between seeing a use
case and administering one.

### Smaller, and still real

- The **summary panel** built for "I have to know the key first" was **removed the same day**: it
  answered the question and pushed the requests below the fold, and the first reader asked where
  their traces had gone. A discovery aid that hides the thing being discovered is a net loss.
- **"Show me the prompts that threw a warning"** is a filter now, backed by a `flagged` column
  derived in `record_request` from the argument all three call sites already pass. Not a query over
  the JSON decisions: containment is written differently on SQLite and Postgres, and the hermetic
  suite runs on one while production runs on the other.
- A migration id of 42 characters **applied its DDL and then failed writing its own version row**,
  because `alembic_version.version_num` is a `varchar(32)`. The same shape as the Keycloak client
  description that broke a realm import at `varchar(255)`: a length only a real database enforces.
- The **phone layout test** caught a ten-pixel overflow the day a checkbox gained a sentence-length
  label — `.checkline` was `white-space: nowrap`, which is right for "Refusals only" and wrong for a
  sentence.
- The e2e test for reading a prompt **issues its own key and sends its own request**, because the
  first version opened whichever row was on top and failed against rows another suite had left
  there, dated 2031 with no payload. A test that depends on ambient data is flaky by construction —
  and flaky in a way that looks like a product defect.
- `clearCookies()` does not end a Keycloak SSO session. Walked into twice in one day; written into
  the test rather than remembered.

## 2026-08-09 (later) — Four columns, and a search box that survives its own query

A second walkthrough of the same screen, and the more interesting of the two findings is the one
about a text field.

### Typing two characters threw the reader out of the field

> *"in Suchfeldern wenn ich 2 character reinschreibe, dann fängt er an zu suchen und ich fliege aus
> dem Feld raus und muss es nochmal anclicken"*

The use-case list had its search input inside the `@else` of `@if (loading())`. So the first
keystroke that reached the debounce started a query, the query set `loading`, Angular tore down the
`@else` — taking the input with it — and built a fresh one when the answer arrived. Focus gone,
mid-word.

**A control that starts a request must survive that request.** The box now sits outside the branch,
and "busy" is a word beside it rather than a screen that replaces it.

### The guard missed the case it was written for

The shape is the defect, not the occurrence, so the guard scans every template for a search input
inside a block its own query toggles. Its first version found nothing — because `@else` carries no
condition, so `} @else {` reads as innocent no matter what it is the alternative *to*. Teaching it
to inherit the `@if` it belongs to immediately turned up a second instance in the model catalog,
which does not misbehave today only because that search is client-side.

Second time in two days that a new guard had to be broken before it could be believed, and both
times it was silently wrong in the same direction: **passing**.

The e2e test was then shown to fail against the restored bug. Only a browser can see it — a
component test types into a field and asserts a request went out, with no notion of where the caret
is.

### Eleven columns down to four

When, from where, what, how it ended. That is what somebody scans a list *by*; model, tokens, cost,
latency, trace id, tools, credential and use case are details *about* a request, and they now belong
to the request that was opened. The table had grown to eleven columns and scrolled sideways, which
is precisely how the control that opens a row ended up off screen yesterday — so the fix for that
and the fix for this are the same fix, and an assertion on the scroller's own width keeps it.

Dates are `dd.MM.yyyy`: this console is read in Europe, and `9/8/26` means two different days
depending on who is reading it. A request a pipeline step objected to is marked **red on the row**
rather than with a badge in a column nobody scans for.

Sixteen unit tests moved from asserting the list to asserting the opened row — the same statements,
one indirection further in. The harness gained a router, because the detail links to its use case
and a harness without one tests a different component.

## 2026-08-09 (evening) — The catalog, the rule editor, and an audit of the audit

### "How do I know a model is reachable if I have no key?"

The sharpest question of the round, and the honest answer was that nothing could tell you. A
catalog entry is a **declaration**: it needs no credential and proves nothing. Without a key no
adapter is registered, so the model sits in the catalog looking perfectly healthy while every
request for it comes back `model_not_found` — which a caller reads as a typo, not as a missing
credential.

`GET /v1beta/models/{model}:check` now answers **three separate facts**: declared, served,
reachable. `reachable: null` means nothing was contacted, which `FRD-117` already established is not
the same as failing. Never a generation — a self-deployed model can be scaled to zero, and a
"does this work" button must not be the thing that wakes it, bills for it and takes minutes to say
so. The upstream's error *text* is never repeated back: a provider's message can carry the URL it
was called with, and that URL can carry the key.

Verified against the live registry, which is the only place it means anything: the local model
answers, and a Vertex model this stack has no credential for reports **"declared, but nothing
serves it"** instead of looking fine.

### Declaring a model was at the bottom of the page

Somebody who came to add a model had to scroll past the entire catalog to find out how. The thing a
screen is *for* goes where a reader starts. A row now opens to **everything on file** — built as a
list in the component so it is exhaustive by construction, with a test that populates every field
and requires each on screen. A catalog entry is what the gateway *enforces*; a partial answer to
"what does this row actually say" is worse than none.

### An audit of the audit

Asked whether all the test combinations actually occur, so it was measured rather than asserted:
each branch of `payloads.py` was broken in turn and the parametrised rows that noticed were
recorded. Three findings.

**`is_oversight` was undefended.** Removing it makes an oversight role fall through to
`OUT_OF_SCOPE`, which is *also* a 403 — so a matrix checking only the status passed with the role
boundary gone. The distinction is the entire point of the message: "you see figures, not content"
and "that use case is not yours" send the reader to two different people. The matrix asserts the
sentence now.

**Half an audit reports half a matrix as pointless.** Deleting a branch can only make code *more*
permissive, so a case guarding against over-restriction can never go red for a deletion. Running the
inverse mutations — refuse too much — showed that four rows exist precisely to defend against that,
and one of them is the only thing standing between a colleague's request being readable and not.

**One row was defended a layer up**: `outsider` is caught by the route's own scope guard, which no
module-level break could reach. A mutation for it now exists.

Nine of these became permanent (`N46`–`N54`); the harness stands at 316 properties.

### And the rule editor

Its buttons sat in the same wrapping `form-inline` as its fields, so "Create rule" flowed in beside
"smallest sample" and read as one more setting. Fields and actions are two things now, separated by
a rule, with room to aim — a dozen controls at 0.6rem is a wall, and clicking the wrong one of two
adjacent checkboxes is a governance mistake rather than a typo.

## 2026-08-09 (night) — The button nobody could reach, and a seed that lied by one

### A capability with no way in

`FRD-500` says a global rule is IT Security's to author, and the server has accepted one since the
day it was written. The console never offered the button — so every global rule that existed
anywhere had been written into the database by a seed, and the question came back exactly as one
would expect: *"wie mache ich es über die Oberfläche?"*

This is `FRD-206`'s defect **inverted**. That one was a control that refuses when used; this is a
capability nobody could reach. Both are a console disagreeing with its server, and only the first
one announces itself.

### Three lists that only grow

Rules, what is stopped now, and what was stopped before — the last of which is *kept* on purpose,
because "blocked for two hours last Tuesday" is what a review asks. All three are paged and
searchable now, and one box covers both suspension lists: "has this caller ever been stopped?" is
answered by the live list *together with* the record, and a search over only the first would answer
it wrongly while looking like it had answered it.

Paging then broke two e2e tests, and correctly: they created a rule and looked for it on screen,
and with a few hundred rules from earlier runs a fresh one is well off the first page. They search
for it now — which is what a person would do, and what the tests should have been doing all along.

### The seed lied by one

Four rules were seeded and three appeared. The fourth named a use case this seed does not create,
and the loop's `continue` dropped it silently: the count looked plausible and the one that went
missing was the only rule that *acts* rather than alerts.

Found by running the seed rather than reading it. It raises now — the **third** instance in this
repository of "returns silently for something unknown", after `record_to_outbox` and the missing
Kafka topics.

### And the design question that was asked directly

> *"Warum machen wir check reachability nicht im Window, und wenn reachability false ist, dann kein
> Anlegen?"*

In the window: yes, done. Blocking: **no**, and it is worth writing down why. Declaring a model
before its credential exists is the ordinary order of work — you write the catalog, then configure
the platform — and an adapter is registered only once the credential is there. A hard gate would
make a fresh installation undeclarable, and would make it impossible to prepare a catalog for a
platform whose key arrives next week. `FRD-114` already settled the shape of this: deprecation
warns, revocation blocks. A reachability verdict is information, and it is shown as such.

## 2026-08-09 (late) — A button that was invisible when it mattered, and tabs that stop hiding

### "Test connection is still not in the window"

It was — inside `@if (name())`. Opening "Add model" starts with an empty name, so there was **no
button at all**, and the feature read as missing because from where the reader stood it *was*
missing. A control that appears only after you have done something else is a control nobody finds.

The follow-up was sharper: *"und ich kann ein Modell ohne Testen anlegen"*. Yesterday I argued
against blocking on a failed verdict and that argument still holds — declaring a model before its
credential arrives is the ordinary order of work, and an adapter exists only once the credential
does, so refusing on `served: false` would make a fresh installation undeclarable.

But that was an answer to a different question. **Refusing the verdict and refusing the ignorance
are not the same refusal.** Save now needs a check to have been *answered* for the name in the
form — whatever it answered. It rules out the one outcome a single button can rule out: nobody adds
a model without having found out. An erroring check counts as looked-at, because a diagnostic that
cannot answer must not become a gate.

Five unit tests and one e2e test started failing immediately, all of them creating a model without
checking. That is the gate working, and updating them is the cheapest possible proof of it.

### Tabs that stop hiding themselves

Below 60rem the strip is a vertical list. Scrolling was the old answer and it is the wrong one for
*navigation*: a tab that has scrolled out of view is a section the reader does not know exists, and
the use-case detail has seven of them. The breakpoint is 60rem rather than a phone width because the
pain starts on a laptop half-window, not at 360px.

CSS only — no template changed, so nothing that clicks a tab by role or text had to move.

### And the mistake worth writing down

Proving the layout test could fail, I cut the media query out of the stylesheet by searching for an
end marker that appears *earlier* in the file than the block. The slice silently **duplicated**
content instead of removing it, the rule stayed in effect, and the test stayed green — which I
briefly read as "the test does not work".

Checking what the file actually contained took ten seconds and turned a wrong conclusion into a
right one. The lesson is the ordinary one, and it keeps arriving in new clothes: **when an
experiment says something surprising, verify the experiment before believing the result.**
