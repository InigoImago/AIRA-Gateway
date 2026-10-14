# FRD-610 — Nothing spends outside a bucket

> Phase: 6 · Status: **Built** — diagnostics are audited (§2), the installation has its own budget
> (§3.1), an unpriced model cannot hide from a cost limit (§3.2) and an unenforced switch says so
> on every screen that shows a limit (§3.3) · Owner: Vadim Scheibe
>
> Origin: the owner, on the console's model checks: *"how does it look with the budget? Are these
> test requests counted and taken into the budget, as already described — every request should be
> auditable and budgetable."* Then: *"make it auditable. Then think out a concept for how we can do
> it so that money does not leak away from us."* Then, on the concept below: *"build the
> installation budget."*
> Related: `FRD-401`/`FRD-403` (budgets, pricing), `FRD-122` (audit), `FRD-125b` (pipeline spend),
> `ADR-0021` and `FRD-609` (the checks that prompted this).

## 1. What was measured

The console's *Ask the model* button spends real tokens. Pressed against the running installation:
**1491 audit rows before, 1491 after.** No row, no budget entry, no trace of the money.

That exemption was mine, written the day before, and its argument was that the spend is tiny,
bounded and role-gated. All three are true and answer a different question. **A small amount nobody
can see is not a small amount — it is an invisible one**, and the rule the owner states has no
clause for administrator convenience.

## 2. Part 1 — built: the checks are in the audit trail

Every probe now writes a row.

| | what it records |
| --- | --- |
| *Check reachability* | one row per region. `:countTokens` is free, so the usage is empty — and the zero is the point: *"who probed this model, and when"* now has an answer, and a row saying nothing was spent is a stronger statement than no row. |
| *Ask the model* | one row per word per region, with the **real** usage and the **real** price from the catalogue. Verified: `prompt_tokens 11 · completion_tokens 1 · cost_nanos 1500`. |

Three decisions inside that:

- **No use case, and none invented.** The check exists for a model nobody has released yet — that
  is what it is *for* — so there is nothing to attribute it to. A row with no use case is an
  existing, supported shape here (break-glass keys, demo traffic: 59 rows). Inventing an owner so a
  row has somewhere to sit is the failure `FRD-403` names.
- **`Outcome.DIAGNOSTIC`, its own value.** Counted as `served`, these would inflate every request
  figure with traffic no use case made — the shape `FRD-125b` refused for pipeline calls. Separable
  is what makes it governable: *"what did diagnostics cost this month"* is now a question somebody
  can ask. A report's `requests` leaves them out — it counted rows, so one check was one request in
  the installation's figures — and its spend keeps them, because they were spent.
- **Bookkeeping never breaks the answer.** A persistence failure is logged and the verdict still
  returns: a diagnostic that fails because its own accounting failed would be the worst of both.

**And the seed was writing a declaration that answers `400`.** `qwen3:0.6b` was seeded with
`minimal` among its thinking modes, justified by a comment citing `FRD-111`'s translation of
`minimal` → `"low"` — which `ADR-0021` deleted the day before. Measured: this Ollama refuses the
value by name, *"must be `high`, `medium`, `low`, `max`, or `none`"*. Fixed to what was measured,
including `max` — a word no vocabulary in this project ever had.

## 3. Part 2 — the concept: **nothing spends outside a bucket**

One sentence: **every nanosecond of spend has an owner, and every owner has a ceiling.**

Measured against that, the installation has three holes, and they are one shape — *spend no
allowance can see*:

| | owner | ceiling |
| --- | --- | --- |
| a use case's traffic | ✓ | ✓ |
| pipeline calls (classifier, router, redactor) | ✓ the use case | ✓ |
| **diagnostics** | ✓ recorded as `diagnostic` rows | ✗ **not capped** — see below |
| **unbound traffic** — break-glass keys, demo | ✓ the installation (§3.1) | ✓ |
| **an unpriced model, under a cost budget** | ✓ | ✗ **blind** |

**Diagnostics are recorded and not capped.** *Ask the model* (`:checkThinking`) makes real
generations — one output token per accepted word per region, at most `MAX_LEVELS_PER_CHECK` words
per press — and neither takes the early gate nor books against the installation budget: every call
is a `diagnostic` row with its real price, made by a holder of `operations.diagnose`.
*Check reachability* is `:countTokens` and spends nothing. Booking the first against the
installation budget, and refusing it there, is the open half of this row.

### 3.1 An installation budget — **built**

*(Called an "allowance" in the first draft, and the owner asked what one was — fairly, since it is
not a thing this system has. It is a **budget**: the same row, the same three limits, the same
periods, with one difference — no use case behind it.)*

Budgets were anchored to a use case: `use_case` (a shared pot) and `each_member` (one counter per
head). There was no third, so anything belonging to *the installation* had nowhere to be counted.

There is now a third: `installation`. Every request with **no use case** books against it —
break-glass keys, demo traffic. It is the residual bucket, and its existence is what turns
*unowned* into *owned by the installation*. Small by nature and therefore easy to set. Diagnostics
have no use case either and are recorded beside it, but are not yet booked into it (the table
above).

The rule it enforces is the one worth stating: **a request that fits no bucket does not run.**
Before this, such a request ran and was counted nowhere.

#### How it works

| | |
| --- | --- |
| **the scope** | `Scope.applying` gains one branch, in the one place a scope is added (`gateway/src/aira_gateway/scopes.py`): an `installation` row binds a request that names **no** use case, and only those. A row that named one as well would be two owners for one spend. |
| **the counter** | its own prefix, `installation:` — deliberately not `uc:` with an empty name. A usage key is *stored*, so an empty one would be indistinguishable from a use case whose slug somehow emptied, and `_delete_usecase` sweeps counters by the `uc:{slug}` prefix, which with an empty slug is every counter there is. *(As built, retiring a use case — `_retire_usecase`, `FRD-607` — deletes `uc:{slug}` and every `member:{slug}:` counter.)* |
| **the gate** | `BudgetService.guard` no longer returns early for a request that names no use case. That early return was the whole hole: it was written when there was nothing such a request could be counted against, and it survived the arrival of something. |
| **the refusal** | names the allowance that ran out — `installation`, not *use case* or *member*. Caught by its own test before anybody saw it. |
| **who sets it** | a Global Administrator, on its own route `/api/v1/installation-budgets/`. `IT Steuerung` and `IT Security` **read** it. *As built, these are permissions (`FRD-614`): `budget.installation.write` sets it, `report.read_all` reads it; the built-in roles hold them as stated, and an installation may grant them to another role.* |
| **where** | the reporting screen, above the report — because the figure it bounds is already there, as the `(none)` row of *By use case*, and because a control must be findable in a period that returned nothing at all. |

Its own route rather than a use case's: `/use-cases/<slug>/budgets/` resolves an object from a slug
this budget does not have, and bending that route to accept an absent one makes *"which use case is
this for"* a question with a special answer at every layer that asks it.

**Two constraints, in the database and not only in a form.** `use_case` had to become nullable, and
a NULL is not equal to itself in SQL — so the existing `uq_budget` stops policing exactly the rows
this feature introduces, and two installation budgets for one period would both be accepted with
the gateway enforcing whichever it read first. A partial unique constraint covers what the first
cannot see, and a check constraint refuses a row whose scope and owner disagree, because `clean()`
runs for a form and not for a fixture, a shell or a migration.

**What it does not change.** A use case's traffic keeps booking against its own budgets and nothing
else; this is not a global cap over everything. The pipeline's own calls (`refuse_if_exhausted`,
`book_side_call`) still require a use case, because a pipeline **is** a use case's configuration —
there is no such thing as an installation-level pipeline call.

### 3.2 Unpriced is not unlimited — **built (2026-10-12)**

A cost budget compares `usage.cost_nanos` against its limit. An unpriced model contributes
**nothing** to that figure — it is counted separately as `unpriced_requests`, which is the right
answer for *reporting* (`FRD-403`: unknown is not zero) and the wrong one for *enforcement*, where
unknown becomes unbounded. A use case whose models are all unpriced could spend without limit
against a cost budget that looked configured.

Two ways out were considered:

- **Refuse** — chosen. A model with no price on file may not serve a request a *cost* limit binds.
  That makes pricing a precondition for governance, which is the honest relation between them.
- **Assume a worst case.** A configured ceiling price used for enforcement only, recorded as an
  estimate. Rejected: it invents a number, which is exactly what `ADR-0021` spent a day removing,
  and a wrong number that permits spending is worse than a refusal that stops it.

**It is a dispatch condition, not a check at the reservation**, and that is what makes it better
than a refusal: `PricedWhereCostIsLimited` is asked of each candidate, so a chain **skips** the
unpriced model and a priced one serves (`FRD-302`). Only a request whose last candidate cannot be
counted is refused — and it is refused by name, with the price, the screen to add it on, and the
switch that accepts the gap.

**What does not fire.** A rule about money that fired where no money is capped would take a model
away for reasons nobody asked for, so the conditions are narrow and each one is a test:

| | |
| --- | --- |
| a *token* or *request* limit | counts unpriced traffic exactly like any other — no refusal |
| no budget at all | no refusal |
| `AIRA_ENFORCE_BUDGETS=false` | nothing is capped, so nothing is refused for being uncountable |
| a disabled (lifted) cost budget | is counted and caps nothing (`cost_limited` asks `enabled`), so it cannot make a model unusable |
| the mock upstream | exempt, like every other requirement: deterministic fiction has no price, and the demo ships a cost budget |

Which budgets bind is `applicable`'s answer, less the lifted ones — the rows the reservation is
made against — so the use case's shared pot, a per-member cost budget **and** the installation's
own bucket (§3.1) each make a price a requirement. The price question is `PricingService.price_for`, the one definition
the audit trail and the counters already use.

**The order is cheap on purpose:** the price is asked first and the budgets only when there is no
price, so an ordinary request pays for no extra query, and a chain asks the budget question at most
once however many hops it takes.

`AIRA_ENFORCE_PRICED_MODELS` is **on by default** and sits with `enforce_budgets`,
`enforce_rate_limits` and `enforce_suspensions` — the four answers to *what does this installation
refuse*. Off, an installation with unpriced models keeps serving while the prices are filled in,
and it does so knowing the limit is not a limit.

### 3.3 A switch that is off must say so — **built (2026-10-11)**

`AIRA_ENFORCE_BUDGETS` turns enforcement off, and budgets keep their figures, their warnings and
their bars; they simply stop stopping anything. A control that is present and inert reads as a
working control — the shape `LESSONS.md` §6 opens with, applied to money.

Three places now say it, and the order they arrived in is the argument for the third:

1. **A caller** — `FRD-139`'s `GET /v1beta/me/budget` carries `enforced`, so an assistant showing a
   developer what is left does not draw a bar implying a stop.
2. **The use case's budgets tab** — `GET /v1beta/usage/{use_case}` now carries `enforced` beside
   the figures, and the tab puts a banner above everything else on it.
3. **The installation's own budget** (§3.1) — edited on the reporting page, so the report carries
   the same field and the card shows the same banner. Shown even with no budget configured: an
   empty state there is an invitation to set one, and whoever accepts it should know what it does.

**Said by the plane that enforces.** Management has a copy of this setting and no authority over
it, so a console reading its own configuration could claim enforcement the gateway does not
perform. The answer therefore travels with the figures.

**Three states, not two.** `true`, `false`, and *not answered* — the gateway may be unreachable, or
older than the field. A boolean defaulting to `true` would claim enforcement every time a request
failed, which is wrong in the reassuring direction; defaulting to `false` would announce inert
limits because of a network error. Unknown is drawn as nothing, and
`budgets-tab.spec.ts` asserts each of the three.

The message names the variable, because the reader's next question is where to change it, and says
what switching it on would do: **every limit takes effect immediately, including one already
exceeded.** That is the part a reader cannot guess, and the part that makes the banner actionable
rather than merely honest.

### 3.4 What is deliberately not in this concept

- **A hard cap on the vendor's side.** Right, and not ours: it belongs in the cloud account, and a
  gateway that believes it is the only spending path is a gateway that will be wrong.
- **Per-request cost limits.** A ceiling per request bounds nothing over a month. The reservation
  refuses once a budget is **spent** — a counter already at its limit — and not a single request
  larger than what is left: that one is admitted and settled at what it cost, which is why a
  remainder can go negative (`FRD-139` FR-4).
- **Alerting.** `FRD-500`'s anomaly rules already watch spend; a second mechanism would be a second
  opinion about the same number.

## 4. Open questions

All three are answered — the first two by building §3.1, the third by the owner:

1. ~~**Does the installation budget need periods**~~ — **yes, the same `day`/`month` as every other
   budget.** A running ceiling with no reset is a budget that can only ever be reached once, and
   the counters, the reset boundary and the console's wording all already exist per period.
   Nothing was saved by making this budget different, and a reader would have had to learn why.
2. ~~**Who may set it?**~~ — **a Global Administrator sets it; every oversight role reads it.**
   The installation's own spend is exactly the figure a governance role exists to see, and setting
   it is an act — `ADR-0007`: `IT Steuerung` oversees and acts in nothing. If that should change it
   changes in `InstallationBudgetViewSet.get_permissions` and nowhere else. *(Since `FRD-614` that
   is a permission, `budget.installation.write`, which an installation may grant to a role.)*
3. ~~**Refusing unpriced models under a cost budget will stop traffic**~~ — **behind a switch,
   defaulting to on** (the owner's decision, 2026-10-12). Defaulting to *off* for a release was the
   suggestion in this file and was rejected for the reason the whole section exists: a hole that
   stays open by default is a hole for everybody who does not know about it. On, with
   `AIRA_ENFORCE_PRICED_MODELS=false` as the escape hatch, an installation that has not finished
   pricing keeps serving **by saying so** rather than by not having been asked. And the chain
   softens the edge the question was worried about: a priced fallback serves where one exists, so
   what stops is traffic that has no countable model at all.
