# FRD-602 — Exporting the usage report

> Phase: 8 (KIRA parity) · Status: **Done (2026-08-06)** · Owner: Vadim Scheibe · Last updated: 2026-08-06
> Origin: `kira_api.md` §2.7 (content negotiation, CSV), programme: `ADR-0010`.
> Follows `FRD-601` (delivered 2026-08-06).

## 1. Problem

`FRD-601` serves the report as JSON and renders it as a screen. The predecessor's `/ki-usage`
additionally returns **CSV** by content negotiation, with a filename derived from the period.

That exists because of who reads it. Governance and controlling functions do not consume JSON; they
put the month's figures into a spreadsheet next to a budget. Today the only route from AIRA to a
spreadsheet is copying numbers off a web page, which is exactly the workflow that produces
transcription errors in the document a cost decision is made from.

## 2. Goals & Non-Goals

**Goals**
- The same report, as CSV, over the same endpoint by content negotiation.
- A download that opens correctly in Excel without an import dance.
- **The same visibility rule**, from the same code path (§5.3 — this is the requirement with a
  security consequence).

**Non-Goals**
- Scheduled or emailed reports.
- XLSX. CSV is what the predecessor produces and what every tool reads.
- Per-request export. `FRD-601` deliberately does not expose individual requests, and this changes
  nothing about that — an export of aggregates is not a route to payloads.

## 3. User Stories
- As **IT Steuerung**, I want last month's spend per use case as a file, so that I can put it beside
  the budget without retyping it.

## 4. Functional Requirements

- **FR-1 Content negotiation.** `Accept: application/json` (default) or `text/csv`. Anything else
  is **406**, as the predecessor does — a caller asking for XML is better told no than handed JSON.
- **FR-2 Breakdown selection.** `?breakdown=use_case|model|member`, default `use_case`. A CSV is one
  table, and silently picking one of three would be a guess.
- **FR-3 Columns.** The `FRD-601` row: key, requests, failed, prompt/completion/total tokens, cost,
  unpriced requests, average and maximum latency. Header row present.
- **FR-4 Encoding.** UTF-8 **with BOM**, so Excel reads umlauts correctly rather than as mojibake.
- **FR-5 Filename.** `Content-Disposition: attachment` with
  `aira-usage_<breakdown>_<from>_<to>.csv`.
- **FR-6 Unpriced traffic stays visible.** Its own column, as in the JSON, plus a trailing comment
  row when the period contains any — a spreadsheet that omits the caveat the screen carries would
  understate spend in exactly the document where that matters most.
- **FR-7 The same scope.** §5.3.

## 5. Design & Architecture

### 5.1 One report, two renderings

The service already returns the aggregates; this is a formatter over the same result. No second
query, and no second scope decision — see §5.3.

Buffered, not streamed: the report is *aggregated*, so row count is bounded by the number of use
cases, models or members in the period, not by traffic. Even a large installation is kilobytes.
Streaming would add machinery for a problem this endpoint does not have.

### 5.2 Delimiters, honestly

RFC 4180: comma-delimited, `.` as the decimal separator, quoted where needed. The BOM (FR-4) fixes
encoding but not the delimiter, and a German Excel with a comma decimal separator will still ask
about columns.

The alternative — semicolons, which German Excel opens directly — breaks every non-German tool and
every script. So: RFC 4180, and the SPA's download link says in one line that Excel may ask for
the separator. Pretending otherwise would just move the surprise.

### 5.3 The scope rule is the security requirement

The obvious way to build this is a new endpoint that queries and formats. That is also how an
export ends up returning more than the screen does — the visibility rule in `FRD-601`'s
`visible_scope` is one function, and a second entry point is a second chance to forget it.

So CSV is a **renderer on the existing endpoint**, after `visible_scope` has already been applied.
The scope decision happens once, in the code that already has mutations `N1`/`N2` guarding it, and
the format is chosen afterwards.

The test that matters is the direct one: a caller without oversight requests CSV, and the file
contains exactly the use cases their JSON contains — asserted against the file's bytes, not against
the service call.

## 6. Data Model
None.

## 7. API / Interface Contract

```
GET /v1beta/reporting?from=&to=&breakdown=use_case
Accept: text/csv
→ 200, text/csv; charset=utf-8, Content-Disposition: attachment; filename="aira-usage_use_case_2026-08-01_2026-09-01.csv"

key,requests,failed_requests,prompt_tokens,completion_tokens,total_tokens,cost,unpriced_requests,avg_latency_ms,max_latency_ms
demo-uc,412,3,120400,240800,361200,42.18,7,340,2100
```

`406` for any other `Accept`; the existing `400`s for the window are unchanged.

## 8. Security & Privacy

- §5.3. An export is the classic place for a visibility rule to be re-implemented and got wrong.
- Aggregates only: no prompts, no responses, no per-request rows. The `FRD-406` constraint that
  keeps per-request browsing out of `FRD-601` applies here unchanged.
- `Content-Disposition: attachment` so a browser downloads rather than renders — a CSV rendered
  inline is a small XSS surface for no benefit.

## 9. Observability
The chosen format on the span, so it is possible to see whether anyone uses the export before
maintaining it.

## 10. Testing & Acceptance Criteria

- **Unit** — CSV requested returns CSV with the header row and the right filename; JSON stays the
  default; an unsupported `Accept` is 406; each breakdown selects the right table; the BOM is
  present; a value containing a comma is quoted; the unpriced caveat row appears only when there is
  unpriced traffic.
- **Unit (scope)** — a caller without oversight receives a CSV containing only their own use cases,
  asserted on the returned bytes.
- **Integration** — a real request with `Accept: text/csv` over the live stack parses as CSV and
  its figures match the JSON for the same window.
- **e2e** — the SPA's download produces a file.
- **Mutation** — the scope is applied to the CSV path (mutate it away and the scope test must go
  red); 406 is actually returned rather than falling back to JSON.

**Acceptance**
- *Given* a governance user, *when* they download last month by model, *then* the file opens in a
  spreadsheet with correct umlauts and figures matching the screen.
- *Given* a use-case user, *when* they download the same period, *then* the file contains only
  their use cases.

## 10a. What was built (2026-08-06)

A **renderer on the existing endpoint**, chosen by `Accept`, exactly as §5.3 requires — the scope
decision happens once, in the code that already has mutations guarding it, and the format is picked
afterwards. A test asserts by source inspection that `visible_scope` is resolved exactly once and
that the CSV path grew no query of its own, because the failure being guarded against is an export
that returns more than the screen: a governance failure delivered as a file, forwarded, saved and
impossible to recall.

The scope test asserts on **the file's bytes**, not on the service call. A test that checked the
arguments would pass against a renderer that ignored them.

BOM, CRLF, RFC 4180 commas, quoted keys, money formatted for people (the exact integer stays in the
JSON, which is what a script should read), the unpriced caveat as a trailing comment row, and a
filename that sorts. Verified live against the real database, umlauts and all.

The SPA downloads via a blob rather than an `<a href>`, because the endpoint needs the bearer token
and a link that 401s looks like a broken export rather than like a browser that cannot
authenticate. The object URL is revoked immediately — a dozen exports would otherwise pin a dozen
blobs for the life of the page. The download panel says in one line that Excel may ask about the
separator, which §5.2 asks for and is the honest alternative to picking the other surprise.

Mutations **E9**–**E13**; 29 hermetic tests and 6 in the SPA.

## 11. Dependencies & Risks
`FRD-601`, delivered. No new dependencies. Risk is confined to §5.3, which is why it is a test
rather than a note.

## 12. Rollout / Demo
A download button beside the period selector on the Reporting screen, with the one-line note from
§5.2.
