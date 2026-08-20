# FRD-610 — Diagnostics in the audit trail, and a concept for where money leaks

> Phase: 6 · Status: **Part 1 built · Part 2 is a proposal for review** · Owner: Vadim Scheibe
>
> Origin: the owner, on the console's model checks: *"how does it look with the budget? Are these
> test requests counted and taken into the budget, as already described — every request should be
> auditable and budgetable."* Then: *"make it auditable. Then think out a concept for how we can do
> it so that money does not leak away from us."*
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
| **diagnostics** | ✗ | ✗ *(now visible, still unbounded)* |
| **unbound traffic** — break-glass keys, demo | ✗ | ✗ |
| **an unpriced model, under a cost budget** | ✓ | ✗ **blind** |

### 3.1 An installation allowance

Budgets are anchored to a use case: `use_case` (a shared pot) and `each_member` (one counter per
head). There is no third, so anything belonging to *the installation* has nowhere to be counted.

Propose one: an allowance whose scope is the installation, against which **everything with no use
case** is booked — diagnostics, break-glass keys, demo traffic. It is the residual bucket, and its
existence is what turns *unowned* into *owned by the installation*. Small by nature and therefore
easy to set: an installation that spends more than a few euro a month on diagnostics is telling you
something.

The rule it enforces is the one worth stating: **a request that fits no bucket does not run.**
Today such a request runs and is counted nowhere.

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

1. **Does the installation allowance need periods** (`day`/`month`) like a use case's, or is a
   single running ceiling enough for what is by nature small?
2. **Who may set it?** A Global Administrator, presumably — but it is the one allowance `IT
   Steuerung` might reasonably own, since it is the installation's own spend rather than a use
   case's.
3. **Refusing unpriced models under a cost budget will stop traffic** in installations that have
   models nobody priced. Behind a switch, defaulting to off for one release?
