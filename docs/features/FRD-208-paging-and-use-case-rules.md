# FRD-208 — Paging that is real, and a rule somebody can actually change

> Phase: 2/5 · Status: **Done (2026-08-08)** · Owner: AIRA · Last updated: 2026-08-08
> Related: [`FRD-207`](FRD-207-console-legibility.md) (the pass this corrects),
> [`FRD-206`](FRD-206-console-truthfulness.md), [`FRD-500`](FRD-500-anomaly-rules.md),
> [`FRD-502`](FRD-502-security-console-and-traces.md)

## 1. Summary

`FRD-207` added a search box and a pager to three lists. They were **client-side** — the whole list
came down in one response and the browser sliced it. Asked directly whether that was real paging,
the honest answer was no, and the follow-up question is the useful one: *does it matter here?*

It matters in exactly one of the three, and this closes that one. It also closes two defects the
same pass introduced or exposed:

- The console pointed at a screen for editing a use-case rule. **That screen did not exist.**
- `.table__actions` buttons wrapped onto two lines in a narrow cell.

And it pages the findings list, which `FRD-207` left open.

## 2. Where paging belongs at the server, and where it does not

Three lists, three different answers, and the difference is not style:

| List | Bound | Cost per row | Paged |
|---|---|---|---|
| Use cases | **None** — a live round found 801 | Object-level permissions computed **per row** | **Server** |
| Findings | **None** — an append-only log | Cheap | **Server**, by cursor |
| Model catalog | Tens: how many models an organisation contracts | Cheap | Browser |
| Report breakdowns | One aggregate response, already computed | Zero — the work is done | Browser |

**The use-case list is the one that mattered**, and the measurement says why: `GET
/api/v1/use-cases/` takes seconds on this installation because the serializer answers `can_admin`,
`can_manage` and `is_member` per row (`access.py`, `FRD-206`). Slicing that in the browser leaves
every one of those computations happening on every load. The reader waits exactly as long and then
sees twenty-five rows. Measured after: **1.6 s**, 211 use cases across 9 pages.

**Findings are cursor-paged, not offset-paged** — the same choice the trace view made and for the
same reason (`FRD-502` §4.2): they are an append-only log, so a detector firing while somebody reads
page two pushes rows across the boundary and they see one twice and never see another, invisibly.
The use-case list has no such problem and uses ordinary page numbers.

**The catalog is deliberately not paged**, and that is written into the viewset rather than left to
be rediscovered. Two of the console's warnings — "N models have no price on file", "N have no
capability declaration" — count over the *whole* catalog. Paging it would turn those into "N on
this page", a figure that means nothing. The console can honestly search and page it in the browser
because it has all of it.

## 3. What a page is

```json
{ "count": 211, "page": 1, "page_size": 25, "pages": 9, "results": [ … ] }
```

**The total is in the body, not a header.** A list that does not say how much it is not showing
reads as complete, and a reader who cannot see a total cannot tell a filtered list from a whole one.

**A search is a filter, not a ranking.** `?q=` is a case-insensitive substring over the fields a
person would type — for a use case, its name *and* its technical id, because somebody arriving from
a log line has only the second. Nothing scores or reorders: "why is this one first" has no good
answer when the rows are equally valid.

An empty or whitespace-only `q` is **not** a filter. Treating it as one would answer "nothing
matches the empty string" — wrong, and the sort of emptiness a reader takes for a broken screen.

## 4. Three behaviours a server-paged list has to have

`core/ui/server-table-view.ts`, each one a way this goes wrong:

- **Typing does not fire a request per keystroke.** A 250 ms pause, and an identical query is not
  re-sent. Without it a nine-letter search is nine round trips against the slowest endpoint here.
- **A new search starts at page one.** Otherwise a filter applied on page 4 asks for page 4 of a
  two-page result and gets nothing, which reads as "no matches".
- **A late answer never overwrites a newer one.** Requests are switched, not queued, so a slow "a"
  cannot land after a fast "abc" and repopulate the table with rows nobody asked for.

And the server has the last word on which page this is: asking for page 9 of a list that shrank to
three must not leave the pager claiming 9.

## 5. A use-case rule is editable, where it says it is

`FRD-207` had the security console say, of a rule belonging to a use case, that it *"is changed on
that use case"*. There was no such screen. **That is the `FRD-206` defect one level of indirection
further out**: not a button that answers 403, but an instruction with no destination.

The server had allowed it all along — `AnomalyRuleViewSet._guard` lets whoever manages the use case
change its rules, and `upsert_use_case_rule` exists for exactly this. Only the screen was missing.

Now: a **Rules** tab on the use-case detail. It lists the rules of that use case, says what each one
does in a sentence, and creates, edits and deletes them for anybody the server would accept. The
security console's sentence became a **link** to it.

**Global rules are deliberately absent from it.** They are not this use case's to change, and
listing them would offer an edit the server refuses.

### One form, two screens

`features/security/rule-form.ts` is used by the IT Security console **and** by the use-case panel.
Thirteen fields with a per-kind validation contract, written once: a second copy is how one screen
quietly loses the field the other gained.

What it does not offer on a rule that exists: the **kind**, and the **name**. The kind decides what
the threshold *means* — 50 is half the requests under `refusal_rate` and half a multiple under
`spend_spike` — so changing it in place silently reinterprets a number somebody chose. And the
server upserts a use-case rule **by name**, so renaming one would create a second and leave the
first watching.

## 6. Two layout defects

**Buttons wrapped.** `.actions` carries `flex-wrap: wrap`, which is right in a form row and wrong in
a table cell: the column is only as wide as its content, so Edit and Remove wrapped onto two lines.
The cell already refuses to wrap its text; its buttons agree now.

*(The earlier half of this — `display: flex` on the `<td>` itself, which stops it being a table cell
at all — was `FRD-207` §2.3. Same cell, two different ways of leaving the row.)*

## 7. Testing

- **The page is a page**: bounded, explicitly ordered (an unordered queryset may hand the same row
  back twice and never show a third), and honest about the total.
- **The search is the database's**: asserted by watching for the request carrying `q=`, not by
  checking which rows are on screen — the second passes for a client-side filter too.
- **Speed is an upper bound, not a benchmark**: the list must be usable within 15 s on a database
  the old shape could not have managed.
- **The rules panel is walked end to end in a browser** — create, read back in words, edit, and the
  read-only view for somebody who does not administer the use case.
- **Every tab is opened**, all eight, and required to render a panel. That is the `FRD-502` defect
  in its plainest form: two tabs whose panels failed to construct and rendered nothing while every
  unit test passed.

## 8. Open

- Report breakdowns stay client-side. `by_use_case` on an installation with 800 use cases is 800
  rows in one response — large, but already computed; paging it would mean re-running the aggregate
  per page. If that response becomes a problem, the fix is a paged aggregate, not a browser slice.
- The use-case list is fast enough now, and the per-row permission computation is still there. A
  list endpoint that answered permissions in one query rather than per row would be the real fix.
