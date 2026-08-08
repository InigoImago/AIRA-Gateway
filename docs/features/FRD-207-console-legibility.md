# FRD-207 — The console holds still, explains itself, and stays usable as it grows

> Phase: 2/5 · Status: **Done (2026-08-08)** · Owner: AIRA · Last updated: 2026-08-08
> Related: [`FRD-206`](FRD-206-console-truthfulness.md) (the console tells the truth),
> [`FRD-502`](FRD-502-security-console-and-traces.md) (the screens this pass first landed on),
> `FRD-601`/`FRD-602` (reporting and export), `ADR-0014` (detection and enforcement)

## 1. Summary

`FRD-206` made the console stop **promising** what the server refuses. This pass is the next
question a reader asks once they trust the buttons: *can I actually read this?*

Twelve findings from a walkthrough of the running console, in three groups:

- **It moves.** Every live view shifted the page on each refresh.
- **It does not explain itself.** "Stop traffic" named no object; a rule printed its kind and two
  numbers; a finding could not be opened; a column heading assumed the reader knew what a
  completion token is; one line of copy was a note to the author rather than a sentence for the
  reader.
- **It does not survive its own data.** Four stacked tables, no search, no paging, and a
  navigation bar that never marked the section you were in.

## 2. What was actually wrong

### 2.1 The jiggle was measurable, and it was one element

Not eyeballed. A `PerformanceObserver` on `layout-shift`, watching the security console for forty
seconds, reported **five shifts, every one of them the Refresh button**. The cause was the stamp
beside it: `"updating…"` is narrower than `"updated 12s ago"`, and `"9s"` is narrower than `"10s"`,
so the text changed width twice a tick and pushed the button along with it.

That is the whole defect. It is also why it was hard to name: the shift is a few pixels, nothing
appears to have *happened*, and the reader is left with an impression rather than an observation.

**Fixed by reserving the space**: the stamp is `min-width: 15ch` with tabular figures, and
"refreshing" became a dot that fades in space it already occupies rather than a word of a different
length. Verified the same way it was found — the observer now reports an empty list.

### 2.2 The navigation marker was never applied at all

`app.html` had `routerLinkActive="is-active"` on every nav item and `app.ts` did not import
`RouterLinkActive`. Angular does not complain about an attribute that matches no directive — it is
simply inert markup — so the class was never set, and the `.is-active` rule in `app.scss` had been
styling nothing for as long as the shell has existed.

This is the same shape as `FRD-502`'s `Live` defect two days earlier: **a declaration that is
silently inert**. Both were invisible to every unit test and obvious in a browser.

### 2.3 A cell of buttons was not a cell

`.table__actions` was `display: flex` on the `<td>` itself. A table cell made a flex container
stops participating in the row: it leaves the row's height and baseline and floats free of it. On
the model catalog that read as two lists side by side rather than one table — which is exactly the
"break between rows and their buttons" the walkthrough reported. The cell is a cell again; the
flexing happens one element in.

### 2.4 The filter row was centred, so it was not aligned

`form-row--spaced` centres its children. A bare checkbox next to a field with a label above its
control is a short box next to a tall one, so centring puts the two controls a reader treats as a
pair on two different lines. A filter row aligns on the **bottom**, where the controls are.
Measured: both control centres now sit on the same pixel.

## 3. What changed, by screen

### The security console (`FRD-502`)

**A finding opens.** Six columns is as much as a table can be read at, and the answer to "why did
this fire" is another six fields — so they go *under* the row rather than into it. The panel says
what was measured against what, over how many requests, what was actually done about it, and the
rule behind it.

**A rule opens, says what it does in a sentence, and can be edited.** `new_source_ip` beside two
bare numbers is enough for whoever wrote the rule and nothing for whoever has to decide, at eleven
at night, whether the alert in front of them matters. `rule-language.ts` turns a rule into English.
It is safe to write precisely because the vocabulary is **closed** (`aira_common.anomalies`): seven
kinds, each with one meaning, unable to grow by configuration.

Two things that sentence keeps honest:

- **A ratio is not a threshold.** `spend_spike` at 300 is "three times the window before", not
  "300 euros" — `FRD-500` chose a ratio deliberately, because a fixed number is a budget and there
  already is one.
- **`alert` is not enforcement.** `ADR-0014` keeps detecting and doing apart; so does every
  sentence. And `detected_not_enforced` says, in words, that the rule asked for a block, did not
  get one, and the traffic continued — a console that showed only "blocked" would report traffic
  as stopped that was still flowing.

Editing covers the fields somebody actually changes — threshold, window, smallest sample, action
and its duration or rate, and whether it is watching at all. **Not the kind and not the name**: a
rule's kind decides what its threshold *means*, so changing it in place would silently reinterpret
a number chosen deliberately. A different kind is a different rule.

Authority follows `FRD-206`: a **global** rule is editable by an incident role, which is the
predicate the server enforces with. A **use-case** rule needs object-level permission, which is not
in the token — so rather than guess, the console names where that rule is edited.

**The kill switch says how far it reaches**, on hover: one caller, one API key, or one whole use
case, answered `429`, recorded as `suspended`, arriving within a few seconds. And it says there is
**no switch for the installation** — that is a deliberate absence, not an omission.

**The history line is a sentence again.** It read: *kept, because "blocked for two hours last
Tuesday" is what a review asks*. That is a note to whoever wrote the code, in the place where a
sentence for the reader belongs. It now says what the section contains.

### Reporting

**One table at a time.** Four stacked breakdowns made the page long enough that its own export
control scrolled out of sight, and left two ideas of "which table" — one for the screen and one for
the file. The selector now governs both, so a download is what is on screen.

`by_outcome` is shown and **not** exported: the CSV renderer takes three breakdowns (`FRD-602`),
and a download button that looked ready and answered 400 is the defect `FRD-206` was written about.
The button is replaced by the reason, naming the three that do work.

**The columns explain themselves.** Prompt and completion tokens, spend, and latency each carry the
sentence a reader needs — including *why* prompt and completion are shown apart (they are priced
apart, and output usually costs several times more, which is how a row with modest prompt tokens
turns out to be the expensive one).

### Lists

Search and paging on the reporting breakdowns, the model catalog and the use-case overview. Not a
nicety: a live round found **801** use cases in one installation, which made the overview useless
without a single line of it being wrong.

Two rules the shared `TableView` encodes:

- **Searching returns to page one.** A filter applied on page 4 that leaves you on page 4 shows an
  empty table, and the reader concludes there are no matches when there are five.
- **The reader is told what they are not seeing.** "1–25 of 801" is the difference between a list
  and a list that looks complete; a silent truncation reads as "that is everything".

The pager renders **even on a single page**, showing "8 of 8". A control that appears only once a
list grows teaches nobody it exists, and a reader who cannot see a total cannot tell a filtered
list from a complete one.

## 4. Two things extracted rather than repeated

**`core/ui/info-hint`** — the hover/focus/pin explanation. It had been written twice within a week,
which is one time more than a three-way interaction should be got right, and the second copy
promptly collided with the first on a `data-testid`. **One pinned at a time, page-wide**: the panels
are overlays, so two open beside each other cover one another and the figures they describe.

**`core/ui/table-view` + `table-pager`** — searching and paging, client-side, because these lists
arrive in one response already: paging them in the browser needs no endpoint and cannot disagree
with what was fetched. The trace view stays the exception and pages by cursor at the server, because
it is unbounded *in time* (`FRD-502` §4.2).

## 5. Testing

- **Layout is measured, not eyeballed.** A `layout-shift` observer over two live ticks must report
  nothing; the two filter controls' centres must sit within four pixels; an actions cell must share
  its row's top and height.
- **Interactions are exercised in a browser.** Hover is not a concept jsdom has; neither is a
  computed style. The nav marker, the info hints, the row toggles and the rule editor are all
  asserted where they run.
- **The rule tests create their own rule.** The first version skipped when the installation had no
  rules, which meant the editor — the part of this pass with the most behaviour in it — was
  exercised in the browser exactly never. A test that skips itself when the data is inconvenient
  reports green about nothing.
- Unit tests cover the search/page arithmetic, every one of the seven rule kinds, the two-kinds-of-
  empty distinction, and the busy states.

## 6. Open

- The use-case list endpoint computes object-level permissions per row, so an installation with
  hundreds of use cases takes many seconds to answer. The search box and the pager make that
  survivable; they do not make it fast. Server-side filtering is the fix, and is not built.
- The findings list is not searchable or paged — it is bounded by the endpoint's `limit`, not by
  the installation's size. It will need the same treatment once it is used in anger.
