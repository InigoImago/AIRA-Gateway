# FRD-626 — Usage over time: which model, when, what it cost

> Phase: 6 (Governance & Analytics) · Status: **Done** · Owner: Vadim Scheibe
>
> Origin: the owner, looking at a use case's own **What you used** card — *"wir haben schon in einem
> use case eigene nutzung, es wäre doch schön wenn wir histogramme der nutzung zeigen können welches
> modell wann was gekostet hat … aber auch so dass es in form von blöcken sichtbar wäre was die
> gesamtnutzung ist."* The first clause names the subject, and the first build missed it (§5.8).
> Related: `FRD-601` (the report this reads), `FRD-602` (export), `FRD-603` (a use case's own
> consumption), `FRD-403` (price), `FRD-122` (the audit trail), `ADR-0028` (the time bucket).

## 1. Problem

`FRD-601` listed charts as a non-goal and said exactly what it was waiting for:

> Charts. The first cut is figures and bars; **a chart is worth adding once someone has said which
> comparison they actually make.**

Somebody has. The comparison is two of them, and they are different questions:

- **When, and by whom.** A month's total says what was spent; it cannot say whether last Tuesday
  was ten times every other day, or whether one model has been quietly taking over. That is a
  *time* question and no figure on these screens answers it.
- **What the whole is made of.** 71 % one model and 18 % another is a sentence somebody acts on;
  the same information as five rows of a table is one they scroll past. The by-model table has had
  a share bar per row since `FRD-601` — against the **largest row**, which is a ranking and not a
  composition.

Everything needed was already recorded. `request_logs` has had a priced row per request since
`FRD-403`, and `FRD-601`/`FRD-603` already aggregate it. What did not exist was a **bucket**: the
report groups by use case, model, member and outcome, and never by time.

So this is the same shape as `FRD-603` — *the data was already there, the reader was not* — with one
difference. `FRD-603` needed no new query. This one does.

## 2. Goals & Non-Goals

**Goals**
- A period, **bucket by bucket**, each bucket split into coloured bands — by model, by use case or
  by outcome.
- The same period, as one row of **blocks**: what the whole is made of, at a glance.
- **Three measures over one load**: spend, requests, tokens. Which one is drawn is a question about
  the picture, not about the data.
- One query, inside the report that is already scoped — so the chart's legend and the table under it
  cannot disagree about one month.
- On the **reporting screen** and on a **use case's own overview**, where the consumption card
  already is.
- The figures are reachable as text, because colour is not a channel every reader has.
- Nothing says zero that nobody measured.

**Non-Goals**
- **The reporting screen.** It was built there first and taken out again: the ask was a use case's
  own overview, and the installation-wide view is what `/reporting`'s figures and breakdowns already
  are (FR-14).
- A charting library. Seven hues, a stack and a row of blocks are less code than the adapter would
  be, and a dependency that renders to canvas would put the figures somewhere a test cannot read
  them. See §5.4.
- A second aggregation path. The arithmetic is `ReportingService`'s; this adds a `GROUP BY` to it.
- Per-request drill-down from a column. That is `FRD-505`, with its own access rules and its own
  access record, reached from the Requests view.
- A local display time zone. Buckets are UTC days, like the window parameters already are
  (`ADR-0028` consequences).
- A fallback-free series for streams, anomaly overlays, forecasts, or a comparison against the
  previous period. Each is a feature; none was asked for.

## 3. User Stories
- As **IT Steuerung**, I want to see which model has been growing over the month, so that a price
  negotiation or a release decision rests on a trend rather than on one month's total.
- As a **member of a use case**, I want to see **my own** usage over time on its page, because what
  I am accountable for is what I called — and the figure for the whole use case, on the same screen,
  is a different and larger number.
- As a **use-case administrator**, I want to know whether my use case's spend is steady or spiked
  last Tuesday, because those two have different causes and only one of them needs me. That question
  is the reporting screen's, narrowed to the use case.
- As **IT Security**, I want to colour the same chart by **outcome**, so a use case grinding against
  its limit is visible as a wall of refusals rather than as a quiet month.
- As anybody, I want to download the chart's numbers, so the figure in my slide comes from the
  gateway rather than from a screenshot.

## 4. Functional Requirements

- **FR-1 One endpoint, one scope.** `GET /v1beta/reporting?series=auto|day|hour&split=…` extends the
  existing report. **No new endpoint**: `visible_scope` is one function and a second entry point is a
  second chance to forget it (`FRD-602` §5.3, `FRD-603` FR-1 — learned twice, applied here without
  being paid for a third time).
- **FR-2 Asked for, never assumed.** A report with no `series` parameter computes no series. The
  use-case overview loads this report **twice** per page for the consumption card, and a `GROUP BY`
  per bucket that nothing reads is a query nobody notices getting slow.
- **FR-3 Every bucket of the window, including the empty ones.** A chart assembled only from buckets
  that have rows puts Monday beside Friday and calls it a week. A quiet Sunday is a reading.
- **FR-4 The bucket is what the clock says**, in UTC, identically on both stores (`ADR-0028`).
- **FR-5 A granularity too wide to draw is refused by name.** Hours are capped at **192** — eight
  days. A month of them is 744 columns, which at phone width is under half a pixel each; a year is
  8 784. Past the cap it is a `400` naming the cap and the granularity that would work, never a day
  series served under an hourly label (`FRD-206`: a chart whose axis silently changed answers a
  question nobody asked). Below it the plot scrolls inside its own container, as a wide table does
  (`FRD-207`), so a dense chart never drags the page sideways.
- **FR-6 Past seven bands, the tail is one band.** `(other)`, summed **in the database**. An eighth
  generated hue is indistinguishable from one of the seven under colour-vision deficiency, and a
  chart that folded by *dropping* would add up to less than the figure above it.
- **FR-7 Colour follows the entity, not its rank.** Switching the measure re-orders the bands and
  **re-paints none of them**, so two views of one period can be read against each other.
- **FR-8 Three measures, no reload.** Spend, requests and tokens arrive together for every bucket.
- **FR-9 The numbers are text as well as a picture.** The plot carries `aria-hidden` and the day
  list (FR-16) is a real table **on the screen**. Three of the seven hues sit below 3:1 against this
  surface, and the relief for that is labels and a table — not a toggle somebody has to discover,
  which is what the first version shipped (§5.9).
- **FR-10 Absent is not empty.** A series that did not arrive says so, in the backend's own words
  (`core/api/error-message.ts`), **in the card** and not through the page's banner (`FRD-603` §5.4).
  A period in which nothing happened says that instead, and they look different.
- **FR-11 A failed reload clears what it replaces.** §5.6.
- **FR-12 Unpriced traffic keeps its caveat.** `FRD-403`'s rule: spend that excludes unpriced
  requests is a lower bound and says so — and the caveat is dropped on the measures it does not
  apply to, because a warning that is always there is one nobody reads.
- **FR-14 On a use case's own page, and only there.** It was briefly on the reporting screen too,
  and that was scope the owner never asked for: *"ich wollte ja nur diesen Übersicht im use case
  haben."* The reporting screen keeps its figures, its four breakdowns and its export, unchanged.
  Two splits are offered — colouring one use case's traffic *by use case* draws one band and calls
  it a comparison.
- **FR-16 A day reads as a sentence.** Under the two pictures, the period **day by day**: each day
  its own total and one row per band, carrying requests, **spend** and the token split together —
  *on the twelfth I made fourteen calls on this model and four on that one, and together they cost
  me this much.* All three measures at once, whichever measure the picture is drawing. Newest first;
  days with no traffic are left out, which is the opposite of the axis rule and right for both
  (§5.9). It lives in a native `<details>` whose summary carries the count — shut, because thirty
  days of rows pushed the rest of the overview off the page; native, because the rows then stay in
  the accessibility tree and a keyboard opens it without any script.
- **FR-18 A spend nobody could compute reads as unknown, and says which kind of unknown.** Where
  **every** request in a group was unpriced, the figure is an em dash — `0.00` is a measurement, and
  that one was never made. And the two causes are counted apart: a model with **no price on file**
  (add one) and an upstream that reported **no token usage** for anything to be priced from (no
  price can fix that). `unmetered_requests` is the second, a subset of `unpriced_requests`.
- **FR-17 A use case's own page gets one period control**, on the chart card — today, last 7 days,
  last 30 days, this month, last month. It is **the only one on that page**, and that is why it has
  to exist: the consumption card answers *this month* and *today* by design (`FRD-603`), and the
  panel below answers whatever period the reader's own **budget** resets in. On the showcase's
  `kundenservice` that period is a day, so on the first of a month the entire overview could speak
  only about one day. The panel now says **why** its window is what it is, beside the window.
- **FR-15 On a use case's own page the chart is the reader's own traffic**, not the use case's.
  `person=` narrows the whole report to one person, keyed the way `FRD-606` keys a person — the name
  the credential carried, the subject otherwise — and the card is headed *What you used, over time*.
  See §5.8: this is what the feature was asked for, and the first version got it wrong.

## 5. Design & Architecture

### 5.1 A `GROUP BY` with a bucket in it, and why that was the hard part

The series is one statement: `group by (bucket, band)` over the window the report already narrowed,
with the measures `_measures()` already defines. Everything interesting is in the two expressions.

**The bucket** is a text prefix of the rendered timestamp — `substr(cast(created_at as text), 1, 10)`
for a day. `date_trunc` is Postgres-only, `strftime` SQLite-only, and `FRD-601` §4.2 had already
refused to make a reporting expression dialect-dependent *in as many words*, because the production
half would then be exercised only by the live suite. The text prefix is the one spelling both stores
share — at the cost of one thing that had to be pinned rather than assumed: Postgres renders a
`timestamptz` in the **session's** zone. `ADR-0028` has the whole argument and the fix (a libpq
`options: -c timezone=UTC` on the engine).

**The band** is `case (coalesce(nullif(column, ''), '(none)') in (kept) then … else '(other)')`.
Folding in the database rather than in Python, for the same reason as every other figure here. The
`nullif` is not decoration: the breakdown this folding is ranked against labels an empty string
`(none)` too, and without it a band the legend calls `(none)` would have its rows folded into
`(other)` — a chart contradicting the table beside it.

`reporting/series.py` holds the arithmetic — granularity, axis, labels, the two expressions — apart
from the queries, because all of it has a right answer and a component is the worst place to test
one.

### 5.2 Which bands get a colour is decided from the breakdown that is already there

`report()` computes `by_use_case`, `by_model`, `by_member` and `by_outcome` for every call. The
series takes its ranking from **the one matching its split**, rather than running a query of its
own: two rankings of one period are two chances for the legend to disagree with the table under it,
and it is also one query fewer.

Re-sorted on `(cost, requests, tokens, key)` rather than taken as `_grouped` ordered it, which is by
cost alone. In a period where nothing has a price on file every row is zero, and the busiest band
would then be chosen by whatever the database happened to return first — a stacking order that
changes between two loads of the same screen.

### 5.3 The axis is arithmetic here and text from the store, so they are pinned together

`buckets` is generated in the gateway's process from `[start, end)`; the labels on the points come
out of the database. If those ever spell a bucket differently, every point lands off-axis at once —
so `test_every_bucket_the_database_names_is_on_the_axis` pins them, and the service **appends** any
label it did not expect rather than dropping it. A column at the end of a chart is visible; a day's
spend quietly missing from a complete-looking screen is not (`FRD-124`'s rule, one layer out).

### 5.4 Built out of `div`s, not out of a library and not out of SVG

Three reasons, in order of how much they cost to get wrong:

1. **The figures stay in the DOM.** A canvas renderer puts them where no unit test and no screen
   reader can reach them. Every segment here is an element with a `data-testid`, and
   `usage-histogram.spec.ts` asserts the heights it was given.
2. **Responsiveness is the browser's job.** The console must work at 390px (`FRD-207`). A `viewBox`
   either distorts its own text or needs the width measured; a flex row of percentage-height stacks
   does not. Where the columns do not fit — an hourly view on a phone — the plot and its axis scroll
   **together** inside their own container, the same answer a wide table gets.
3. **No dependency.** Seven validated hues, a stack and a row of blocks are less code than the
   adapter would be.

The readout — what the hovered column is made of — sits **under** the plot rather than floating over
it, and that is a correction rather than a preference. A panel positioned over a column has to live
inside the scroll container to be anchored to it, and a container that scrolls horizontally clips
vertically too: the panel would be cut off or grow a second scrollbar. What matters is reading all of
a bucket's bands at once, not where that reading appears — and a readout works on a touch screen,
which a hover never does.

**And two things only looking at it found**, which is the argument for rendering a chart and
screenshotting it rather than trusting the unit tests that say `height: 75%`:

- *The axis thins by count, and what collides is width.* `UsageHistogram` labels every ⌈n/32⌉-th
  column, so a 30-day month labels all thirty — and at 390px each gets ten pixels, clips to one
  character, and the axis reads `0000000001111111112222222223`. A second thinning lives in the CSS
  as a **container query** on the plot (not a media query: this card sits beside a 15rem sidebar),
  showing every fourth cell below 34rem and letting the survivor out of its cell, which is free
  because its neighbours are hidden. The two compose rather than replace, and **index 0 satisfies
  both**, so however dense the chart the axis never goes blank.
- *A share cannot know how many pixels it is worth.* A block is labelled above 12 %, which fits
  `gpt-4o-mini` on a desktop card and `q…` on a phone. A cropped model name is worse than none — the
  family is at the front of it — so below 34rem the name goes and the share stays, since a number
  always fits and the legend directly below carries every name in full.

Two more details in the CSS are the sort that are wrong until somebody looks. The 2px separator between
segments is a `box-shadow` — a margin or a border would change the height a percentage just set, and
the segment heights have to keep summing to the stack. And it is cast **downwards** although the gap
wanted is the one above: the stack is `column-reverse`, so a segment's visual neighbour below it is
an *earlier* DOM sibling and therefore painted first, and a shadow offset upwards would be covered
by the very sibling it separates from. And the hover state was invisible in the first screenshot:
the tallest column — the one a reader points at first — fills its own cell, so a wash behind the bar
survives only as two 2px strips down its sides. It has a baseline marker now, which reads at any bar
height including a column with no bar at all.

### 5.5 The palette is computed, not chosen

Seven hues in a fixed order, validated as a set: every adjacent pair clears ΔE 9.1 under the common
forms of colour-vision deficiency (floor 8) and 19.6 for full-colour vision (floor 15). **The order
is the mechanism** — re-ordering them re-opens the question. `(other)` is a neutral grey rather than
an eighth hue, because it is not an entity and a categorical colour invites a reader to look for it
in the table. Red is deliberately absent although the validated set has eight slots: `--aira-danger`
is red throughout this console, and a band that happened to land in slot eight would read as the
failing one.

Three of the seven sit below 3:1 against white. The documented relief for that is visible labels and
a table view, which is FR-9 — and the one place text may wear a series colour is *on top of it*, so
which ink a label inside a block gets is **computed from the fill's luminance** and asserted to clear
4.5:1 for every colour in the palette. A constant white fails outright on the yellow, at 2.2:1.

### 5.6 A failed reload must not leave the old picture under the new label

Found by a test written to fail first. `granularity` and `split` need a new request; `metric` does
not. The first version derived the chart from `report().series`, so when a reader switched *Coloured
by* to Outcome and that request failed, the chart went on drawing the **model** bands under the new
label. Stale data beneath a changed control is a wrong statement, not a degraded one — so the series
is its own signal, a failed reload clears it, and the card says why.

Its own in-flight flag, too: a question about the chart's axis is not a page load, and blanking the
totals and the tables to answer it would read as one.

### 5.7 Why a row of blocks and not a treemap

The owner asked for *Blöcke*, and either shape is one. A treemap handles a skewed composition better
in two dimensions; a reader compares two **lengths** accurately and two areas badly, and the names
here are model names, which fit along a bar and not inside a tile. The small bands are a floor-width
sliver with their figure on the pointer either way.

It earns its place beside the histogram rather than duplicating it: each stack in the histogram is
scaled against the **tallest** bucket, so the quiet days say nothing about proportion. The blocks
answer the one question the columns cannot.

### 5.8 Whose usage, on a use case's own page

**The first version charted the use case; the request was the reader's own usage**, and the
correction is worth the space because the words were there from the start: *"wir haben schon in einem
use case **eigene nutzung**, es wäre doch schön wenn wir histogramme der nutzung zeigen können."*
*Eigene Nutzung* is the heading of a card that is already on that page — `people-panel` with
`[only]="myName()"`, *What you used* (`FRD-606`). The ask was a histogram **of that**, and the first
build put the use case's total above it instead: a larger figure about a different subject, on one
screen, with nothing saying which was which.

So the chart there is narrowed with `person=` and headed *What you used, over time*. Three
consequences worth naming:

- **One predicate, on the one window.** `person` goes into `_window` beside the scope, so the
  totals, the breakdowns and the series all describe the same subject. A filter applied only to the
  series would be a second place that decides what a figure is about — and the figure above it on
  that page is the use case's, which is exactly the confusion this fixes.
- **Keyed like a person, not like a credential.** `coalesce(username, subject)`, the same expression
  `by_person` groups on. Filtering on `subject` alone shows somebody half their own traffic: an OIDC
  subject is a directory id and an API key's is its owner's username (`FRD-606`). That is mutation
  `US15`, shown to fail first.
- **It reaches nothing new.** The filter subtracts rows from a window the caller's scope has already
  bounded, and every person it can name is already in `by_person` for the same caller. The scope
  remains the only thing that decides visibility.

The card loads from the `/me` response rather than from `ngOnInit`: until that answers there is
nobody to narrow to, and a load started earlier would fetch the use case's whole traffic and put it
under a heading that says *you*. Where the account has no name, neither this card nor the
`people-panel` beside it is rendered at all — they are about the same person, so they appear and
disappear together.

The **reporting screen** is unchanged and remains where the use case as a whole is read, split by
model, use case or outcome.

### 5.9 A day read as a sentence, and why the first version did not answer the question

The chart and the blocks answer *how much* and *what of*. Neither answers the one a reader actually
arrives with — *what did I use on that day, and what did it cost me* — and the first version put the
only figures that could answer it **behind a button** (*Show the numbers*) and as a **pivot of one
measure**: a grid of bucket × band showing spend, or requests, or tokens, never the three together.
Reported as *"aber wo ist ein schönes diagramm … was beschreibt: in dem tag habe ich das und das
verwendet und das kostete mir so und so viel"*, which is exactly the sentence a pivot cannot write.

So the pivot is gone and the day list replaces it, visible, with four rules worth naming:

- **All three measures on one row**, independent of the chart's metric. Switching the picture to
  Requests must not take the money off the list; the money is half the sentence.
- **Grouped, not flat.** One `tbody` per day: the day's own total, then its bands. A repeated date
  column would make a reader scanning for one day read fourteen identical cells.
- **Days with nothing are left out** — the opposite of the axis rule, and both are right. On an axis
  an empty day is a reading, because the week has a shape; in a list it is a row that says nothing,
  thirty times over.
- **Spend before the token split.** On a phone this table scrolls inside its wrapper like every wide
  table here, so whatever sits in the fifth column is what a reader has to drag for. Hiding the
  token column at narrow widths was the other candidate and is worse: `display: none` takes the
  figure from a screen reader too, which is the same loss by a quieter route.

It also discharges FR-9 properly. The relief the palette owes — three hues below 3:1 — was being
paid by a table nobody could see without knowing to ask.

### 5.10 One period control, and why it is on the chart

A use case's overview had no period control at all, and three cards that each answer a *fixed*
window: *this month* and *today* on the consumption card (`FRD-603` §5.3, deliberate), the budget's
own period on the per-person panel, and a rolling thirty days on the chart. On 1 October that is a
page about one day — reported twice, and the second time with the reason: *"ich würde gerne last 30
days, last 7 days, this month haben, damit ich auch mal zeitliche perspektive bekomme."*

The control goes on the **chart card**, because that is the card whose subject is time, and it drives
the chart, the blocks and the day list together. The other two keep their windows, and the reason is
not inertia:

- The **consumption card** is the use case's *now*, with a link to the reporting screen for history.
  That is `FRD-603`'s decision and this feature is not the place to reopen it.
- The **per-person panel** sits beside *Left of allowance*, and an allowance only means something
  against the period it resets in — a month of spend against a daily cap prints a remainder nobody
  has. So its window follows the budget, and what was missing was not a control but a **sentence**:
  the card now says *today, because the per-person allowance on this use case resets daily*, and
  points at the chart's control for the longer view. A reader who met that card on the first of a
  month had met a rule and had no way to tell it from a defect.

### 5.11 `0.00` for a model that has a price

Found by the owner on the running showcase, which is the only place it could have been found:
*"allminilm hat auch kosten von 0.0, was nicht so ganz gut passt."*

Measured on that stack: `all-minilm`, 9 requests, `prompt_tokens` NULL, `cost_nanos` NULL — nine
unpriced requests on a model the seed prices at 0.010000 per million tokens.

**And the first explanation was wrong, which is why it is written down.** It looked like an upstream
that does not meter what it serves. Asked directly, the runtime answers
`{"usage": {"prompt_tokens": 12}}` for exactly these calls — so the tokens were reported and **the
gateway was throwing them away**: `openai.mapping.response.embedding_values` returned a plain list
and dropped `data["usage"]`, while `accounting.embedded` looks for `vectors.input_tokens`. The
Vertex adapter had been returning `EmbeddingVectors` with both fields since `FRD-115`; the OpenAI
dialect and the Gemini one never did. Every embedding call through that dialect — the whole local
and self-hosted path — was recorded with no tokens and therefore **no cost**.

The lesson is the diagnosis, not the line: *the explanation that fits the symptom is not the same as
the cause.* "This runtime does not report usage" explained everything observed and was false, and a
single `curl` at the upstream settled it in one command. **Ask the far end before describing it.**

Two defects, and the second is the one that would have sent somebody to the wrong screen:

1. **The figure.** Nine unpriced requests out of nine, rendered as `0.00` — and this half stands
   whatever the cause, because an upstream that genuinely does not meter exists. Every screen in this
   console says *unknown is not zero*, and this view said zero. Where a group is entirely unpriced
   the cell is an em dash now — in the day list, in the legend, and in the reporting screen's
   breakdown table, which was showing the same model as `0.00 + 9 unpriced` on the same screen.
   A group with *some* priced traffic keeps its figure and its "+ N unpriced" lower-bound note; the
   two statements are different and keep different words.
2. **The reason.** `cost_nanos IS NULL` has two causes and had one counter; every caveat named one: *"ran on a
   model with no price on file."* For an embedding row that is false, and the remedy it implies —
   add a price — leads to a form that already has one. The row carries the evidence to tell them
   apart (`total_tokens IS NULL` means nothing was reported), so the report counts
   `unmetered_requests` as a named subset, and the caveat on the chart and the comment in the CSV
   say which is which and what each one needs.

The aggregate's `cost` string stays `0.00`: it is the sum of nothing, which is what it is. The
*interpretation* — unknown rather than free — belongs to the view, and now lives in one place that
all three views ask.

**And two of this round's own rules were broken while fixing it**, both caught by the mutation run
rather than by reading:

- *A test whose setup never reaches the path it is named after.* The refusal case was written with
  `prompt=0`, which records `total_tokens = 0` — not NULL — so the row was excluded by the *other*
  condition and `US18` survived. A real refusal records no tokens at all; the fixture does now.
- *A dead definition is a rule the module appears to have.* `_was_served()` was added to give both
  counters one condition and **nothing called it** — the inline expressions stayed. It read as a
  shared rule and was a comment with parentheses. Both counters call it now, which is also why `N3`
  needed re-anchoring.

## 6. Data Model

None. No table, no column, no migration — which is again the point. One line of engine
configuration (`ADR-0028`), which is not data.

## 7. API / Interface Contract

`GET /v1beta/reporting?series=auto|day|hour&split=model|use_case|outcome&person=<name>` — the
existing report, narrowed by `person` where given (`in_scope` and `use_case` keep their meanings from
`FRD-603`, and the answer gains `person`). With `series` it carries one more key:

| Key | Meaning |
|---|---|
| `granularity` | `day` or `hour` — what `auto` settled on |
| `split` | which column the bands are |
| `buckets` | every bucket of the window, in order, **including the empty ones** |
| `keys` | the bands, biggest first; this is also their colour order. `(other)` last where there is one |
| `folded` | whether a tail was folded into `(other)` |
| `points` | one row per `(bucket, band)`, carrying `FRD-601`'s measures plus `bucket` |

Refusals carry the surface's own envelope: an unknown granularity or split is named, and an hourly
series over more than 192 hours — eight days — says so and names `day`.

SPA: three views of one response — histogram, blocks, and a day list in a fold — in one
`app-usage-chart` card on a use case's **Overview**, above *What you used*, **narrowed to the
reader**, over a period the card's own control chooses. Nowhere else.

## 8. Security & Privacy

- Aggregates only. No prompt, no response, no per-request row; nothing reachable here that
  `FRD-601` did not already serve to the same caller.
- `person=` **subtracts rows, never adds them**: one predicate on the window the caller's scope has
  already bounded, and every person it can name is already carried in `by_person` to the same caller
  (`FRD-606`). It is a convenience for a screen, not an authorisation boundary — the scope above it
  is the boundary, here as everywhere on this surface. `test_a_person_filter_narrows_and_cannot_widen`
  pins it, and `US14` breaks it.
- The scope is the report's, resolved once by `visible_scope` (FR-1). The series is a new `select`,
  which is a new chance to build the window by hand — so it goes through the same `_window`, and both
  a mutation (`US8`) and a live test with a real token pin it.
- No new stored column, no new transmission, no browser storage: the chart's state lives in signals
  for the life of the page. Nothing in `apps/privacy/activities.py` changes (`ADR-0027`).

## 9. Observability

None added. The endpoint is the one already traced.

## 10. Testing & Acceptance Criteria

- **Unit (gateway)**, `gateway/tests/test_usage_series.py` — 52: the axis holds every bucket and
  stops before the exclusive bound; a window starting mid-bucket keeps its bucket; `auto` reads the
  window at each boundary; a named granularity wins; too wide is refused by name; a request lands in
  the day the clock says and 23:59:59 is not tomorrow; an hourly label is an ISO prefix; every label
  the store produces is on the axis; the buckets sum to the totals; a bucket carries one point per
  band; each split bands by its own column; the order is stable with every cost at zero; the tail
  folds without changing the total; `(none)` is itself and not the tail; refusals and unpriced
  traffic are counted; the parameter cannot reach around the scope; a report nobody asked carries no
  series; and both stores' renderings normalise to one label.
- **Unit (gateway, export)**, `test_csv_export.py` — **the fixture's keys are checked against
  `Figures.as_dict()`**:
  the missing test that let the `failed` column read `failed_requests` nowhere (§11).
- **Unit (frontend, the day list)** — 10: a day reads as its total and its bands with requests,
  spend and tokens on one row; money before the token split; newest first; an empty day left out;
  a period with no traffic said rather than tabulated; a fraction of a cent never shown as zero; the
  unpriced caveat and the failed count per row; and the week-then-the-rest control.
- **Unit (frontend)** — 51 across four specs. The arithmetic (`usage-series.spec.ts`): empty buckets
  are columns, a segment is scaled against its own column and not the peak, a measure switch
  re-ranks without re-painting, a quiet period is `empty` rather than flat, the order is stable at
  zero, and every palette colour's chosen ink clears 4.5:1 — with both inks shown to be used, so the
  gate is not computed from its own answer. The pictures (`usage-histogram.spec.ts`,
  `composition-bar.spec.ts`): the heights and stacks rendered, the readout listing *every* band of
  the hovered bucket, labels thinned rather than overlapped, a tiny block kept visible, a label
  dropped where it would not fit, `aria-hidden` on both plots. The card (`usage-chart.spec.ts`): the
  metric switch costs no request while granularity and split are reported upwards, only the offered
  splits appear, absent reads differently from empty, the table is in the DOM either way, a band with
  no row in a bucket is an em dash and not a `0.00`, and the unpriced caveat appears on spend alone.
- **Unit (frontend, money)** — `displayAmount` against the cases the Python side is tested on,
  including the one that matters: a non-zero amount is never shown as zero.
- **Unit (gateway, the person filter)** — 8 more: it leaves only that person; one person through two
  credentials is one series; a row whose credential named nobody is found under its subject; it
  narrows and cannot widen; it narrows the **whole report** and not only the chart; an absent filter
  is about everybody; and the endpoint says who it narrowed to.
- **Unit (pages)** — 14 more, seven on each page. The reporting page sends the series on its own load, re-asks without blanking
  the page, reports a failed reload in the chart and not in the banner (written to fail against the
  derived-signal version, §5.6), and exports with the series parameter; the use-case page asks for
  thirty days **of the reader's own traffic** by model, asks for **no** series on either consumption
  window, keeps an out-of-scope report out of the chart, and — the one that matters — **asks nobody
  and draws nothing when the account has no name**, rather than falling back to the use case's whole
  traffic under a heading that says *you*.
- **Integration (Postgres)**, `tests/integration/test_usage_series.py` — 6, and these are the ones
  this feature exists on: the session **is** UTC; 23:30 and 00:30 either side of a midnight are two
  columns labelled by their UTC days; the axis is the window and not only the busy days; the bands
  sum to the totals over real Postgres; a person is found across **both** their credentials by the name the
  credential carried; and a member asking for another use case's series gets an empty one rather
  than somebody else's.
- **e2e** — 7: a column per day of the month with a painted segment on the day the traffic happened,
  the legend naming the model, the blocks drawn; hovering a column says what that day was made of
  (the layer that can tell a readout that renders from one that appears); the table present and
  hidden until asked for; a month of hours refused in the chart and not across the page; and no
  horizontal overflow at 390px.
- **Mutation** — `US1`–`US18` less `US12` (whose mechanism was removed with the reporting
  screen's export), plus re-anchored `N3`, `N20` and `N21`, each shown to fail before it was shown to
  pass: the empty buckets,
  the exclusive bound, the day/hour width, the refusal, the named granularity, the fold, the
  `(none)` band, the scope, the stable order, the unasked series, the split refusal, the export's
  own data, the `failed` column, that the person filter narrows at all (`US14`), and that it narrows on the
  person rather than on the subject (`US15`).

**Acceptance**
- *Given* a month with traffic on two models, *when* Reporting is opened, *then* there is a column
  per day of the month including the quiet ones, each day's column is split by model, the blocks
  below state the month's composition, and switching to Requests re-ranks the bands without changing
  any band's colour.
- *Given* a reader who cannot distinguish the hues, *when* they open the same screen, *then* the
  legend names every band and the full table of figures is already in the document.
- *Given* an unreachable gateway, *when* the chart's granularity is changed, *then* the card says
  what the gateway said and the rest of the page keeps its figures.

## 11. Dependencies & Risks

- Depends on `FRD-601` (the aggregation and the visibility rule), `FRD-403` (price), `ADR-0028` (the
  bucket).
- **Risk — two numbers for one month.** Mitigated by one query inside one report (§5.2). The way
  this goes wrong is somebody adding a second query "just for the chart".
- **Risk — a chart read as complete.** Unpriced traffic adds nothing to a spend bar, so the caveat
  travels with the figure (FR-12) and names the measure that *does* count it.
- **Risk — the bucket's time zone.** The failure is invisible and only wrong near midnight, which is
  why it is a pinned session and a live test rather than a convention (`ADR-0028`).

### Two defects found on the way in, worth recording where they were found

The usage export's **`failed` column has been 0 in every file since `FRD-602`.** The renderer read
`row.get("failed", 0)`; the report emits `failed_requests`. Nothing noticed because the fixture
`test_csv_export.py` renders was **hand-written** and spelled it the renderer's way — a stand-in more
generous than the thing it stands in for (`LESSONS.md` §2), and 0 failures is the ordinary answer, so
the column looked right.

Two fixes, and the second is the one that matters: the renderer reads the real key, and a test now
checks every key in that fixture against `Figures.as_dict()`. The first stops this instance; the
second stops the next one.

**And the reporting screen said the wrong thing about what it counts.** The *Requests* info button
read: *"An embedding batch counts as the many texts it carries, not as one."* That is true of a
**budget** (`FRD-113` FR-6) and false of this figure. The audit writes one row per call —
`gateway/tests/test_serving_options.py` asserts *exactly one* for a batch of two, and a sibling test
asserts the budget weighs a batch of five as five — and the report counts rows. Both rules are
deliberate and they genuinely disagree, which is precisely why the sentence had to be the other one:
somebody reconciling a request budget bar against the report will find the two differing on
embedding traffic, and a figure whose own definition was wrong is the half they stop believing
(`FRD-206`). Corrected on both screens, with the difference named rather than smoothed over, and
pinned by a test.

## 12. Rollout / Demo

A histogram needs days, and `tools/demo_traffic.py` produces eleven requests that all arrive *now* —
right for a consumption bar, one column for a chart.

`tools/demo_history.py` (run by `make showcase`, or `make showcase-history DAYS=… PER_DAY=…`) drives
**more real traffic** through the gateway and then **moves the `created_at` of the rows it just
created** across the past four weeks, in a working-week shape. What is real and what is not is stated
in the script's own docstring and in its output:

- *Real* — every request: authentication, the pre-dispatch sequence, the pipeline, a model that
  answered, the price in the catalogue, the audit row. Every token count, latency and figure of money
  is the gateway's own.
- *Not real* — **when** it happened, and how much of it happened on which day.

That is a smaller liberty than inserting rows, which `FRD-130` §4 refuses outright, and a larger one
than nothing, so it is bounded: it updates only rows belonging to the demo's own use cases that
**this run** created, matched by a timestamp marker, and it reports any day whose rows it could not
match rather than moving one that is not its own.

It clears the budget counters **between its own simulated days** — each backdated day is its own
budget period, and without that the showcase's deliberately tight limits (`FRD-130` FR-3) refuse
everything after the first day and the history becomes a wall of 429s. It runs **before**
`demo_reset_usage.py`, so the bars a walkthrough looks at belong to the showcase's own run.

The shape is seeded, so two runs produce the same chart: a demo somebody has to re-learn before
every walkthrough is a worse demo. The injection attempt is repeated every fifth day and the
embedding batch every third, so *Coloured by → Outcome* has a refusal in it and *by Model* has two
bands — a chart whose every band is `served` demonstrates no control at all.
