# FRD-505 — The requests view, and reading what was actually sent

> Phase: 5 (IT Security) · Status: **Done (2026-08-09)** · Owner: Vadim Scheibe
> Last updated: 2026-08-09
> Decides: [`ADR-0016`](../adr/ADR-0016-content-is-readable-and-every-read-is-recorded.md), which
> amends `ADR-0009`. Related: `FRD-502` (traces), `FRD-404` (retention), `FRD-406` (redaction),
> `FRD-206` (the console stops promising what the server refuses), `FRD-131` FR-7 (tool calls).

## 1. Summary

`FRD-502` built a request view inside a use case, and `FRD-131` FR-7 put the tool calls on it. A
walkthrough of the running console produced eight findings in two rounds, and they were all one
complaint in different shapes: **the view assumed the reader already knew the answer.** It asked
which use case before it would show anything, it filtered by a key nobody had seen, it hid the
control that opened a request behind a horizontal scrollbar, and when a request *was* opened there
was no content in it because there had never been a way to see one.

This closes all of that. A cross-use-case screen, the values in columns rather than only in
filters, the prompts themselves — and a record of every prompt anybody reads.

## 2. Goals & Non-Goals

**Goals**

- One screen for the roles that work across use cases, and the existing tab for the people who work
  inside one.
- The facts an investigation needs **in the table**: which use case, which credential, which
  machine, what the pipeline objected to.
- The stored prompt and response for a single request, under a stated permission.
- A record of every content read.

**Non-Goals**

- **Not** a search over prompt *text*. That is content search over personal data, it is a different
  index and a different set of questions, and nothing has asked for it.
- **Not** alerting on reads. `ADR-0016` leaves that open rather than assuming it.
- **Not** PII redaction. `FRD-406` declined that on purpose and the reason has not changed.

## 3. Functional requirements

**FR-1 — Discovery, not recall.** The reader must not have to know a credential, a caller or an
address before they can look. *(Implemented as columns and clickable filters. A grouped summary
panel was built first and **removed**: it answered the question and pushed the requests themselves
below the fold, and the first person to open the screen asked where their traces had gone. A
discovery aid that hides the thing being discovered is a net loss.)*

**FR-2 — A screen, not a tab.** Requests across every visible use case, for the roles that may act
on an incident. The per-use-case tab stays, because the people who run a use case work inside it.
**One component serves both** — a second copy is how the two API surfaces of `FRD-126` and the two
consoles of `FRD-206` came to disagree.

**FR-3 — Content, by role** (`ADR-0016`). Global Administrator and IT Security read any stored
payload; a use-case administrator reads their own use case's; a use-case user reads their own use
case's, subject to FR-4. **IT Steuerung reads none** — every figure, no content.

**FR-4 — A use case may restrict its own members.** `restrict_members_to_own_requests`, owned by the
use case's administrator, shows each *user* only the requests they made themselves. Administrators
of the use case are unaffected. **Default off**, which is the behaviour that already existed: this
is a restriction somebody may impose, not a permission that was previously assumed.

**FR-4a** — the restriction applies to the **list**, not only to the content. Withholding the
payload while leaving the row visible still discloses who else calls, how often and at what cost —
the interesting half of what is being withheld.

**FR-5 — What the pipeline objected to is a column and a filter.** Two things count: a request that
was **blocked**, and one that was **flagged and served**. The second is the one worth having, since
a blocked request announces itself by failing while a flagged one is a 200 with a note attached.
Stored as a column at write time, not derived from the JSON decisions on read — JSON containment is
written differently on SQLite and Postgres, and the hermetic suite runs on one while production runs
on the other.

**FR-6 — Every content read is recorded**, before the content is returned: who, which request,
when, and on what authority (`incident`, `use_case_admin`, `use_case_member`). A failed record means
no read. Kept independently of `request_logs` retention — the content expires, the fact that
somebody read it does not.

**FR-7 — Three absences are three answers.** "This use case does not store payloads", "they were
stored and have expired" and "this request never reached a model" are different facts about the
installation, and two of them are somebody's to change. One message for all three teaches the reader
to distrust the screen.

## 4. What the walkthroughs found

Every item below was reported from the running console, and none was visible to the hermetic tests.

| Finding | Cause |
|---|---|
| "Where is my tracing?" | A summary panel above the table pushed it below the fold. |
| "The prompt button was hidden behind the scroll — I did not know it existed." | It was the last column, and the table scrolls sideways once it carries a use case and an address. |
| "Why is outcome 200 red?" | `outcome` arrived with `FRD-122`; older rows are NULL, and the badge fell through to the danger branch printing the status. |
| "You add info buttons everywhere and do not fill them with information." | `InfoHint` takes projected content; `text="…"` is not an input, and Angular ignores an unknown attribute silently. |
| "I have to know the key before I can search." | Filters only, no columns and no entry points. |
| "I have to know the use case first." | The view lived inside one. |
| "Where do I see the prompts?" | Nowhere — `ADR-0009` had deferred it. |
| "Show me the prompts that threw a warning." | Not asked for anywhere. |

### 4.1 The guard that was itself inert

The fix for the empty info panels is one line each. The **guard** against them repeating was
written first as an Angular spec using `import.meta.glob` to read every template — and it did not
work: those specs run in a browser, the glob is unavailable at runtime, and the file failed to
*load*. Vitest reported "0 tests" for it while the run's total stayed green.

A guard that cannot fail is the thing it guards against, one level up. It was found by breaking a
template on purpose and watching nothing happen, and it now lives in the Python suite —
`tools/tests/test_console_info_hints.py` — which has a filesystem. It was then shown to fire.

The general rule, third time in this repository: **prove the new test can go red, including when
the new test is itself the safety net.**

## 5. Testing

- **Hermetic**: a role matrix (`gateway/tests/test_payload_access.py`) — four roles × the
  restriction × three kinds of absence, as a parametrised table rather than as prose, because
  reasoning about "IT Steuerung, in a restricted use case, on somebody else's request, where
  storage is off" is reasoning nobody can check. Two rules were broken on purpose and shown to turn
  the matrix red.
- **Integration**: the access record asserted **in Postgres**, not inferred from a 200; and the
  refusal shown to leave no record.
- **e2e**: the test issues its own key, sends its own request through the gateway and opens *that*
  prompt. An earlier version opened whichever row was at the top and failed against rows another
  suite had left there — dated 2031, no payload. A test that depends on ambient data is flaky by
  construction, and flaky in a way that looks like a product defect.
- **Layout**: the phone-width check caught a ten-pixel overflow the day a checkbox gained a
  sentence-length label — `.checkline` was `white-space: nowrap`.

## 6. Open

- Nothing alerts on an unusual reading pattern (`ADR-0016`).
- `payload_access` has no retention clock, deliberately.
- The `flagged` column is `false` for every row written before it existed. Backfilling from the
  decisions column would state a measurement nobody took — the same rule as "unpriced is counted
  apart, never as zero".
