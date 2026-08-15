# ADR-0019 — An allowance belongs to a person, not to a credential

- **Status:** Accepted
- **Date:** 2026-08-15
- **Deciders:** Vadim Scheibe (owner), with the gateway

## Context

A budget or a rate limit scoped `each_member` is one configured row and one counter per head. The
counter was keyed on the caller's **subject** — and the two credentials do not agree about what a
subject is:

| Credential | `subject` |
|---|---|
| API key | its owner's **username** (`FRD-604`) |
| OIDC token | the directory's **user id**, a uuid |

So one human holding both had **two** allowances. A limit of ten meant twenty to anybody who used
the console and a key. Nothing was wrong in the code — each half was keyed correctly on what it
knew — and nothing could join the two, because the audit row carried only a subject.

The console had to say so, on two screens: *"An API key and a Keycloak sign-in are two separate
budgets for the same person."* `scopes.py` recorded it as a known limit and named the fix:

> the fix (if it is ever wanted) is a stable identity for a person across credentials rather than a
> scope that names one.

`FRD-606` then built exactly that, for a different reason. Per-person consumption needed one row
per human rather than two, so the **name** was recorded beside the subject on every audit row — a
descriptive column, for a display. The owner's question followed immediately: if the figures can be
grouped that way, why can the allowance not be?

## Options considered

- **Key on the subject (status quo)** — nothing to build, and every enforcement key stays what the
  audit row is about. But the limit an administrator writes is not the limit the system applies,
  and the console has to warn about it forever. Governance that needs a footnote is governance
  somebody sizes wrongly.
- **Key on the name, fall back to the subject** — one pot per human. Needs both credentials to
  carry a name (they do) and a decision about what to do with the counters already keyed by
  subject.
- **Reconcile in the control plane instead** — Management knows which Keycloak subject belongs to
  which user. But the gateway must not ask Management on the request path (`FRD-204`), so this
  becomes another read-model to replicate and keep fresh, to answer a question the token already
  answers.
- **Give the gateway a directory lookup** — the same objection, plus a dependency on Keycloak
  during enforcement. A limit that cannot be evaluated when the directory is slow is a limit that
  fails at the worst moment.

## Decision

**An allowance is counted against the person: the name the credential carries, and the subject
where it carries none** (`aira_gateway.scopes.person`).

Three things this deliberately does **not** change:

1. **`subject` stays the identity.** It is what the audit row is about, what `FRD-604` answers
   "who is accountable for this credential" from, and what an investigation reads. Only the
   *counter key* changes.
2. **Suspensions still aim at what they name.** Stopping traffic targets a person, a credential or
   a use case, and those are three different acts: folding the first two together would make
   "block this leaked key" stop the human holding it. `guard_before_work` therefore keeps passing
   the subject and the credential to the suspension check, and the person only to the budget and
   the bucket.
3. **The fallback is the subject, never nothing.** A token with no `preferred_username` — a service
   account, an older realm mapping — keys on its own subject, which is stable and unique. Falling
   back to a blank would put every nameless caller into **one shared pot**, which is the opposite
   failure and much worse, because it is the case nobody checks.

Counters already written under a subject are **merged**, not abandoned (`0032_merge_member`). The
mapping is observed rather than invented: `request_logs` carries `subject` and `username` side by
side since `FRD-606`, so a subject that has called since then resolves to the name it was known by;
one that has not is left where it is, keying nobody. Where a person has both counters for a period,
the rows are summed — the pots merge and carry what each had spent. Without that step everybody who
signs in appears to start the period from zero, which under-counts a budget in the one direction a
budget must not be wrong.

## Consequences

- **Positive.** The limit an administrator writes is the limit the system applies. The console's
  two warnings become a promise instead of a caveat. The per-person figures `FRD-606` shows and the
  counter the gateway enforces against are keyed the same way, so a reader comparing them sees one
  answer.
- **Negative.** A person's allowance now depends on a **name**, and a name can be reassigned: give
  a departed employee's username to somebody new and the successor inherits the remainder of that
  period. Subjects do not move like that. Accepted because the window is one budget period, the
  alternative was two allowances for everybody, and a directory that recycles usernames has larger
  problems than a budget.
- **Negative.** Two people cannot share a username, and nothing here enforces that — it is the
  directory's invariant, relied upon rather than checked.
- **Follow-up.** `each_member` is now genuinely per person. If an installation ever wants
  *per credential* limiting back — bounding one runaway key without bounding its owner — that is a
  new scope beside this one, not a return to keying by subject.
