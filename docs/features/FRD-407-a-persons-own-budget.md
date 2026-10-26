# FRD-407 — A person's own budget, in the use case's hierarchy

> Phase: 4 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner: a use case can set a budget for the whole use case and one budget that is the
> same for every member; it should also be possible to set an individual budget for one specific
> person, and the restriction is a hierarchy — the use case's budget, then the individual budget,
> then the budget that is the same for everybody.
> Decision: `ADR-0044`. Related: `FRD-400` (the budget model, and the named scope it removed),
> `FRD-401`/`FRD-403` (enforcement, cost), `FRD-402` (the budgets tab), `FRD-139` (a caller's own
> allowance), `FRD-606` (what each person used), `FRD-613`/`ADR-0019` (one person, one identity),
> `FRD-627` (strict data mode), `FRD-625` (privacy notice).

## 1. Summary

A use case's administrator can give **one named person a budget of their own**. It takes the place
of the per-person budget (*each member, individually*) **of the same period** for that person —
higher or lower — while the use case's own budget still bounds everybody together, and everybody
without one keeps the per-person budget.

`FRD-400` §2.1 removed a scope naming one person on 2026-08-14. That scope was **additive**: every
budget binding a request applied, so it could only ever lower somebody's allowance. This one is a
**replacement** in a hierarchy, and that is the difference the owner's request turns on — the
colleague whose agent works for the whole team can be given more than the default as readily as
somebody can be given less.

## 2. Goals & Non-Goals

**Goals**
- A budget scope `member` naming one person, set and removed by the use case's administrators.
- The hierarchy, enforced in one place both counters follow: the use case's budget over everybody,
  a person's own in place of the per-person budget of its period, the per-person budget for
  everybody else.
- One counter per person and period, whichever row decides: no request is booked twice, and
  switching between the two rows mid-period hands out no fresh allowance.
- The console shows the hierarchy, names the person, and tells each reader which budget binds
  them; a caller's own client is told the same (`/v1beta/me/budget`).
- A name nobody has, a name the gateway reads as somebody else, or somebody who cannot call the use
  case is refused when the budget is saved; a person who loses access afterwards is shown as such.
- Strict data mode withholds the name from everybody but the administrators, the person and those
  who may see people.

**Non-Goals**
- **An individual rate limit.** Asked for budgets only. The gateway's shared rule would carry one —
  `scopes.binding` is applied by the rate limiter too — and Management does not offer it.
- **Another person's consumption against their budget** for the administrators. The figure is that
  person's own, as every per-person figure is (`FRD-627`): the console says so instead of drawing a
  bar.
- **An individual installation budget.** The installation's budget has no members.

## 3. User Stories
- As a **use-case administrator**, I want to give one developer a larger monthly budget than
  everybody else, without raising everybody's.
- As a **use-case administrator**, I want to hold one integration account to a smaller budget than
  the people in the use case.
- As a **member with a budget of my own**, I want my client and the console to show *my* limit, not
  the default one that no longer applies to me.
- As a **colleague**, I want the budget that applies to me to be the one I am shown, and not to see
  who else was singled out where the installation runs in strict data mode.

## 4. Functional Requirements
- **FR-1 — The scope.** `member` with `subject` = the person's username, per period (`day`,
  `month`), with the same three limits as every budget. Upserted on `(scope, subject, period)`.
- **FR-2 — The hierarchy.** For a request by person P in use case U:
  - every `use_case` budget of U binds;
  - for each period, P's own budget binds where one is set **and on**, and the `each_member`
    budget of that period does not bind P;
  - otherwise the `each_member` budget of that period binds P, as before;
  - a budget of P's own that is **off** refuses nothing and hands P back to the `each_member`
    budget of its period; where there is none, it is still counted (`FRD-401`).
- **FR-3 — One counter.** P's own budget and the `each_member` budget count on P's per-person
  counter (`member:<U>:<P>`), and of the two at most one is booked per request and period.
- **FR-4 — The refusal says whose.** A request refused by a budget of P's own names it as an
  *individual member*'s, not as the per-person budget everybody has.
- **FR-5 — Who can be named.** The username must be an account here, must not be a name the gateway
  matches to somebody else (`FRD-613`), and must be able to call the use case (`may_call`, the
  gateway's own rule — a role's blanket is not access). The stored name is the account's own
  spelling. Each refusal is a `400` naming the fix.
- **FR-6 — Stored where it is enforced.** A database constraint holds that a `member` budget names
  somebody and no other budget does. The upgrade clears a name left on any other row first.
- **FR-7 — What a reader is shown.** `GET /v1beta/usage/{use_case}` marks each row `binds` and, for
  a per-person row that gives way to another for this reader, `replaced_by`. A budget set for
  somebody else carries no figures and names nobody. `GET /v1beta/me/budget` lists only the rows
  that bind the caller.
- **FR-8 — Who sees the name.** `GET /api/v1/use-cases/{slug}/budgets/` shows a `member` budget's
  person to the use case's administrators, the person, and holders of `incident.investigate`; under
  strict data mode everybody else reads `subject: null, subject_withheld: true`. With the mode off,
  everybody who sees the use case reads the name, as they read members and key owners. `reaches`
  says whether that person can still call the use case.
- **FR-9 — The console.** The budgets tab offers *One person — a budget of their own* with a
  person field that suggests the members and says when a name is not among them; lists budgets in
  the hierarchy's order; states the hierarchy wherever such a budget exists; names a budget
  *One person: <name>*, *you* to that person, *another member* where the name is withheld; says on
  each card why it shows no figures for this reader; and marks a budget whose person can no longer
  call. *What you used* and *What each person used* read each person's remainder against the
  budget that applies to them.
- **FR-10 — The privacy notice** names the processing: the person's username, the period and the
  limits, who sees them. New edition.

## 5. Design & Architecture
- **One rule, `aira_gateway.scopes.binding`.** `Scope.applying` answers whether one row could bind
  a caller (a `member` row binds the person it names); `binding` applies the hierarchy over all of
  a use case's rows, keeping per slot — a budget's period, the one slot of a rate limit — the
  per-person row that ranks first: on before off, a person's own before the one for everybody.
  `budgets.store.applicable` (every enforcement path: the reservation, the early gate, the
  pipeline's side calls, the price condition) and `BudgetService.usage` both call it, so the view
  and the refusal cannot disagree; the rate limiter calls it too.
- **No new counter.** `member` rows use the key every per-person budget already uses. A per-person
  counter written before keeps being found.
- **Management.** `Budget.MEMBER`; migration `budgets.0006` (choices, the clearing step, the
  constraint `ck_budget_names_only_one_person`); `BudgetSerializer` requires the name for `member`,
  drops it otherwise, and computes `subject_withheld` and `reaches` from the reader in its context;
  `views/budgets._person_named` resolves the name to the account.
- **Events unchanged.** `budget.upserted` already carried `subject`. A gateway older than this one
  resolves no `member` row, so in the window in which Management is upgraded first such a budget
  binds nobody and the per-person budget still applies to its person.

## 6. Data Model
- Management `budgets_budget`: scope choice `member`; constraint `ck_budget_names_only_one_person`.
  `subject` was a column already, always empty.
- Gateway `budgets`: no change — `subject` existed and is now read for `member`.

## 7. API / Interface Contract
| Where | What |
| --- | --- |
| `POST /api/v1/use-cases/{slug}/budgets/` | `scope: "member"` with `subject`; `400` on the `subject` field for a missing name, an unknown one, an ambiguous one, or somebody who cannot call |
| `GET /api/v1/use-cases/{slug}/budgets/` | each row adds `subject_withheld` and `reaches` |
| `GET /v1beta/usage/{use_case}` | each row adds `binds` and `replaced_by`; a `member` row of somebody else has `measured_for: null` and no figures |
| `GET /v1beta/me/budget` | `allowances` keeps the rows that bind the caller |
| refusal | `Cost budget exhausted for individual member (month).` and the two variants of `FRD-628` FR-6 |

## 8. Security & Privacy
- Personal data: the person's username on a budget row in both planes — already registered under
  *Consumption, budgets and rate limits*; the texts now say what is stored and who sees it, and the
  notice has a new edition.
- Strict data mode (FR-8) as for a key's owner. The consumption against a person's own budget is
  theirs alone.
- Least privilege: only somebody who administers the use case sets one; a plain member cannot set
  their own.

## 9. Observability
No new span or log attribute. A refusal is an audit row as before (`budget_exceeded`).

## 10. Testing & Acceptance Criteria
- Gateway: `test_a_persons_own_budget.py` — the rule on rows; enforcement on both reservation paths
  (Redis scripts on `fakeredis`, and Postgres): a higher budget lets one person past the default, a
  lower one refuses first and names itself, the pot still bounds, one request books once, a budget
  set mid-period starts from what was spent, a switched-off one hands back to the default; what the
  reader is told; `/v1beta/me/budget` per person; the rate limiter takes one bucket.
  `test_one_person_one_allowance.py` — the budget binds a key and a sign-in of the same person.
- Management: `test_budgets.py` — stored spelling and the event, the three refusals, a member cannot
  set one, the upsert key, the constraint, the withholding by reader and mode, `reaches` after a
  removal, the upgrade clearing a stray name.
- Console: `budgets-tab.spec.ts`, `people-panel.spec.ts`.
- Mutations `OWN1`–`OWN14`.
- **Acceptance:** a use case with a monthly budget of 20 for each member and one of 100 for alice:
  alice is refused at 100, bob at 20; with the use case's budget at 50, alice is refused at 50.

## 11. Dependencies & Risks
- The rule rests on the directory not letting a person rename themselves onto a colleague
  (`scopes.person`, `docs/INTEGRATIONS.md` §2), as every per-person allowance does.
- A budget for somebody who leaves binds nobody and stays until removed; the console marks it.

## 12. Rollout / Demo
Images only: Management's migration runs at its start as every migration does. Nothing to
configure. In the console: *Budgets → + Add budget → One person*.
