# FRD-606 — What one person consumed in a use case

> Phase: 6 (Governance & Analytics) · Status: **Done** · Owner: Vadim Scheibe
>
> Origin: the owner, on the budgets tab: *"in reporting I am actually missing a display of how much
> money was used up — at the moment it is only the requests. Another component I am missing is an
> overview of what a person in the use case has used up, in tokens and in money, for both the API
> key and the Keycloak sign-in, even when there is no budget limit; where there is one, it should
> also be visible how much is left."*
> Related: `FRD-603` (the same complaint one level up), `FRD-601` (reporting), `FRD-402` (budget
> UI), `FRD-403` (cost), `FRD-604` (who answers for a credential), `ADR-0007`, `ADR-0016`.

## 1. Summary

`FRD-603` fixed exactly this shape for a **use case**: consumption was rendered only inside a
budget card, as a fraction of a limit, so a use case with no limit showed neither its tokens nor
its money — while every request had been counted and priced in the request log all along.

The same is true one level down. The budgets tab of a use case with a request-only budget shows
`Requests 0 / 500` and nothing else: no money, no tokens. The members tab lists who may call and
says nothing about what they called. And a per-head budget's *remaining* allowance is visible to
nobody but the person it binds.

This feature answers, for one use case: **who used what, in tokens and in money, and how much of
their allowance is left** — with or without a budget, and across both ways a person can
authenticate.

## 2. Goals & Non-Goals

**Goals**

- Money and tokens beside every budget, not only the metric that budget happens to limit.
- Per-person consumption inside a use case: requests, tokens, money.
- One person, **both credentials**: what they spent signed in and what they spent through a key.
- Where a per-head budget exists, what remains of it.

**Non-Goals**

- **Not a second identity system.** `subject` stays what a row *is about* and what every counter
  and every enforcement decision is keyed on. This feature adds a **name** for grouping a display.
- **Not payload access.** This is figures. Who may read a stored prompt is `FRD-505`/`ADR-0016`
  and is untouched.
- **Not a new permission.** Nothing here shows a figure to somebody who cannot already see the
  use case's `by_member` breakdown in reporting.

## 3. User stories

- As a **use-case administrator**, I want to see what each member consumed, so I can tell a
  runaway integration from ordinary growth without reading the request log.
- As a **use-case administrator**, I want that figure whether or not a budget exists, because the
  question "what is this costing" does not begin when a limit is set (`FRD-603`).
- As **anybody with a per-head allowance**, I want to see how much of mine is left.
- As a **person who uses both a key and the console**, I want my consumption to be one figure with
  the two parts visible, not two rows I have to recognise as me.

## 4. Functional requirements

- **FR-1** A budget card shows the **spend and the tokens** of its scope and period, beside the
  metric it limits. A request budget stops being a card about requests only.
- **FR-2** A use case reports consumption **per person**: requests, prompt and completion tokens,
  and cost, over the selected window.
- **FR-3** Each person's figure is **split by how they authenticated** — `oidc` and `api_key` —
  and the two are also given as one total. Both halves are named in the reader's words: a sign-in
  and a key.
- **FR-4** Where the use case has an `each_member` budget, each person's row shows the allowance,
  what they have used of it in the **current period of that budget**, and what remains.
- **FR-5** FR-4's figures are derived from recorded requests and say so. The authoritative counter
  for *enforcement* is the shared budget counter (`ADR-0008`); a display that silently claimed to
  be that counter would be a second answer to one question.
- **FR-6** Unknown is never rendered as zero (`FRD-603`'s rule, unchanged): a figure that did not
  arrive is an em dash with a reason.

## 5. Design

### 5.1 A name on the row, and why it is not an identity

`request_logs` carries `subject`, and a subject is **not the same alphabet** for the two
credentials: an OIDC token's is the directory's user id, an API key's is its owner's username. So
one person is two rows and nothing can join them. `Attribution` has carried `username` all along —
with a docstring saying it is *"never written to the audit row"*, because a name can be reassigned
and a subject cannot.

That reasoning holds and this feature does not overturn it. It adds `username` to the row as a
**descriptive** column:

- `subject` stays the identity: every counter, every budget key, every enforcement decision, every
  question of *who* is answered by it, exactly as before.
- `username` is what the person was **called at the time**, used to group figures for a reader.

Grouping falls back to `subject` where there is no name, so rows written before this column exists
still appear — as themselves, uncombined. That is the honest answer for them: the join genuinely
was not recorded.

### 5.2 The remaining allowance is computed, not invented

An `each_member` budget names nobody: one row, one counter per head. Its *limit* is therefore the
same for everybody, and what remains for one person is `limit − what that person consumed in the
current period`. The console has both halves already — the budget from Management, the consumption
from the report over the budget's own period window.

Computed there rather than served from `budget_usage` because that counter is scoped to the reader
(`api/usage.py`: *"reporting somebody else's here would make a consumption bar a way of watching a
named colleague"*), and widening it is a permission decision this feature does not need to take:
the per-person spend it displays is the same `by_member` figure reporting already shows to anybody
who may see the use case.

The two can differ slightly — the counter is authoritative for refusing, the log for reporting —
so the figure is labelled as recorded consumption rather than presented as the enforcement number.

## 6. Data model

`request_logs` gains `username VARCHAR(255) NULL`, indexed. Nullable for every row that predates
it, and for credentials that carry no name.

## 7. Testing

| Layer | What only it can see |
|---|---|
| Gateway unit | the grouping: two credentials, one person; a nameless row standing alone |
| Gateway unit | the split by auth method sums to the person's total |
| Management/console | a budget card showing money for a request-only budget |
| Console | the per-person table, the credential split, the remaining allowance |
| Live stack | a real key and a real sign-in by one person landing in one row |


## 8. As built

Two figures were already being produced and neither was rendered, which is `FRD-603`'s finding
repeated at the next level down: `BudgetUsage` carries the period's tokens, requests **and** money
for every budget, and the card showed only the metric that budget happened to limit. A request
budget therefore answered "how much money" with silence while holding the answer.

The join needed a column. `request_logs` now carries `username` beside `subject` — descriptive,
indexed, nullable, and never an identity: `by_member` still groups by subject because that is what
every counter and every budget key uses, and `by_person` groups by the name. Rows written before
the column stand alone under their subject, which is the honest answer for them.

Two things the live stack showed that the tests had not:

- **A half with money and no requests was hidden.** The panel asked `requests > 0` to decide
  whether a credential had called, and a pipeline step's model call is recorded with no request
  against it (`FRD-125` FR-9) — so somebody whose month went entirely through a classifier had a
  half with real spend and no line saying so. It asks about spend now.
- **Two decimals hid the whole answer.** An allowance of `0.01` against a spend of `0.0003` read
  `0.01 of 0.01`. The remainder is computed in nano-units — integers, like every other money
  figure that is not being shown — and rendered in the significant precision of the numbers beside
  it, with the limit's storage zeros trimmed off first.


## 9. On the overview, and out of the way

Two more from the owner, in one sentence each.

**"I want to see my consumption and remaining budget in the overview of the use case."** The
members tab answers it for everybody; the overview now answers it for the reader. The **same
panel**, narrowed by a name, because the arithmetic is the part worth not writing twice — which
window a budget's period selects, how a remainder is computed in nano-units, in what precision it
is shown, and that a negative one is not a debt. A copy of that on the overview is a copy that
disagrees with the members tab the first time either is touched.

Half of "remaining budget" has no personal answer: a `use_case` budget is one pot the first caller
to arrive may spend all of. So it is said as the shared fact it is — *"Left of this use case's
shared day budget: 499 of 500 requests — shared with everybody in it"* — rather than divided by
head, which would invent an allowance nobody configured.

**"Make the description of connections collapsible, it takes up too much space."** Measured: the
overview was **3849 px** tall and that block was **3467** of them, 90% of a page whose job is to say
where a use case stands. It is a reference, not a status: read once when a client is wired up and
scrolled past every day after. A `<details>`, shut by default — 3467 px → **122**, page 3849 →
2347.

The **substantive sentence stays outside the fold**. It is the answer to the question that prompted
the panel — a caller hunting for a per-use-case URL that does not exist — and a reader decides
whether to open a block from what its summary says. Shortening it to "base URLs and examples" put
the answer behind the fold that exists to hide the examples; a test holds it there.

Two of the four tests written for this section could not fail, both found by the harness rather than
by reading them: one asserted the *wording* of a line that must not appear, so a mutation rendering
the same claim under a different label passed it; the other left the usage map empty, so the line
was absent because nothing had been measured rather than because the scope was wrong. Both now
assert the element and reach their own path.


## 10. And then the allowance followed the figures (`ADR-0019`)

The owner, on seeing the per-person figures: *"if it was that easy to calculate a person's
consumption, why not throw the API key and the Keycloak sign-in into one pot for limits, request
limiting, budgets and so on — then we do not have to worry about double budgets."*

Right, and `scopes.py` had already named this as the fix it was waiting for: *"a stable identity
for a person across credentials rather than a scope that names one."* This document built that
identity for a **display**; using it for the **decision** was the smaller half of the same idea.
`ADR-0019` records it, including the two things it deliberately does not change — `subject` stays
what a row is about, and a suspension still aims at exactly what it names.

The console's two warnings were true when written and became false the moment the key changed. They
now say the opposite, and a test forbids the old wording: a caveat that has quietly become wrong is
worse than none, because somebody sizes a limit around it.
