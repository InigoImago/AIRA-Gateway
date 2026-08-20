# FRD-610 — Nothing spends outside a bucket

> Phase: 6 · Status: **Partly built** — diagnostics are audited (§2) and the installation budget
> exists (§3.1); §3.2 and §3.3 remain · Owner: Vadim Scheibe
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
  can ask.
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
| **diagnostics** | ✓ the installation (§3.1) | ✓ |
| **unbound traffic** — break-glass keys, demo | ✓ the installation (§3.1) | ✓ |
| **an unpriced model, under a cost budget** | ✓ | ✗ **blind** |

### 3.1 An installation budget — **built**

*(Called an "allowance" in the first draft, and the owner asked what one was — fairly, since it is
not a thing this system has. It is a **budget**: the same row, the same three limits, the same
periods, with one difference — no use case behind it.)*

Budgets were anchored to a use case: `use_case` (a shared pot) and `each_member` (one counter per
head). There was no third, so anything belonging to *the installation* had nowhere to be counted.

There is now a third: `installation`. Everything with **no use case** books against it —
diagnostics, break-glass keys, demo traffic. It is the residual bucket, and its existence is what
turns *unowned* into *owned by the installation*. Small by nature and therefore easy to set: an
installation that spends more than a few euro a month on diagnostics is telling you something.

The rule it enforces is the one worth stating: **a request that fits no bucket does not run.**
Before this, such a request ran and was counted nowhere.

#### How it works

| | |
| --- | --- |
| **the scope** | `Scope.applying` gains one branch, in the one place a scope is added (`gateway/src/aira_gateway/scopes.py`): an `installation` row binds a request that names **no** use case, and only those. A row that named one as well would be two owners for one spend. |
| **the counter** | its own prefix, `installation:` — deliberately not `uc:` with an empty name. A usage key is *stored*, so an empty one would be indistinguishable from a use case whose slug somehow emptied, and `_delete_usecase` sweeps counters by the `uc:{slug}` prefix, which with an empty slug is every counter there is. |
| **the gate** | `BudgetService.guard` no longer returns early for a request that names no use case. That early return was the whole hole: it was written when there was nothing such a request could be counted against, and it survived the arrival of something. |
| **the refusal** | names the allowance that ran out — `installation`, not *use case* or *member*. Caught by its own test before anybody saw it. |
| **who sets it** | a Global Administrator, on its own route `/api/v1/installation-budgets/`. `IT Steuerung` and `IT Security` **read** it. |
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

### 3.2 Unpriced is not unlimited

A cost budget compares `usage.cost_nanos` against its limit. An unpriced model contributes
**nothing** to that figure — it is counted separately as `unpriced_requests`, which is the right
answer for *reporting* (`FRD-403`: unknown is not zero) and the wrong one for *enforcement*, where
unknown becomes unbounded. A use case whose models are all unpriced can spend without limit against
a cost budget that looks configured.

Two ways out, and the first is recommended:

- **Refuse.** A model with no price on file may not be served where a *cost* limit applies. That
  makes pricing a precondition for governance, which is the honest relation between them — and the
  console already counts unpriced models on its own screen.
- **Assume a worst case.** A configured ceiling price used for enforcement only, recorded as an
  estimate. Rejected as the default: it invents a number, which is exactly what `ADR-0021` spent a
  day removing, and a wrong number that permits spending is worse than a refusal that stops it.

### 3.3 A switch that is off must say so

`AIRA_ENFORCE_BUDGETS` turns enforcement off, and **nothing in the console says so.** Budgets keep
their figures, their warnings and their bars; they simply stop stopping anything. A control that is
present and inert reads as a working control — the shape `LESSONS.md` §6 opens with, applied to
money.

Whatever else is built, this is the cheapest and should come first: a banner on the budgets screen
when the installation does not enforce them.

### 3.4 What is deliberately not in this concept

- **A hard cap on the vendor's side.** Right, and not ours: it belongs in the cloud account, and a
  gateway that believes it is the only spending path is a gateway that will be wrong.
- **Per-request cost limits.** A ceiling per request bounds nothing over a month, and the reservation
  already refuses a request that cannot fit its budget.
- **Alerting.** `FRD-500`'s anomaly rules already watch spend; a second mechanism would be a second
  opinion about the same number.

## 4. Open questions

Two of the three were answered by building §3.1:

1. ~~**Does the installation budget need periods**~~ — **yes, the same `day`/`month` as every other
   budget.** A running ceiling with no reset is a budget that can only ever be reached once, and
   the counters, the reset boundary and the console's wording all already exist per period.
   Nothing was saved by making this budget different, and a reader would have had to learn why.
2. ~~**Who may set it?**~~ — **a Global Administrator sets it; every oversight role reads it.**
   The installation's own spend is exactly the figure a governance role exists to see, and setting
   it is an act — `ADR-0007`: `IT Steuerung` oversees and acts in nothing. If that should change it
   changes in `InstallationBudgetViewSet.get_permissions` and nowhere else.
3. **Refusing unpriced models under a cost budget will stop traffic** in installations that have
   models nobody priced (§3.2). Behind a switch, defaulting to off for one release? — **still
   open.**
