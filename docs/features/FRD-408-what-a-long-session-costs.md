# FRD-408 — What a long session costs, counted where it is shown

> Phase: 4 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner, in daily use: costs do not rise the way they should — check whether the cost
> calculation is carried out correctly.
> Related: `FRD-403` (prices and cost budgets), `FRD-133` (prompt caching and its rates),
> `FRD-128` FR-6 (a stream its caller left, counted at the vendor), `FRD-508` (the model
> reference), `FRD-144`/`FRD-149` (OpenCode's configuration and budget), `FRD-147` FR-7 (a
> ranking's unit), `FRD-625` (the privacy notice).

## 1. Summary

The gateway's arithmetic was right: integers throughout, cache reads at their own rate, thinking
billed as output, a fallback priced as the model that answered, every pipeline call booked, and the
reports summing every row. What was wrong was **around** it — five places where a cost that was
billed reached no figure, or reached the wrong one:

1. **OpenCode's own cost was a third to a sixth of the gateway's.** The `opencode.json` the console
   writes gave each model `input` and `output` only. OpenCode subtracts the cached tokens from the
   input and multiplies them by `cache_read`, which is **zero when the key is absent** — read in
   the binaries of both generations (1.19.3 and 2.0.32). In an agentic session 80–95 % of every
   prompt is a cache hit, so every hit was free on the developer's screen. Measured by hand on
   three turns: gemini-3.5-flash 0.0525 against the gateway's 0.0795, Claude Sonnet 4.6 0.0225
   against 0.069, GLM-5.2 0.0272 against 0.0426.
2. **A long prompt was billed at the short prompt's rates.** Google and Anthropic bill a request
   whose prompt is longer than a stated length — 200 000 tokens on Gemini 2.5 Pro and 3.1 Pro,
   100 000 on Claude Haiku 5.5 — at a second set of rates, for **every** token of it. The catalogue
   had one set; the model reference dropped the second and left a note. A Gemini 2.5 Pro turn of
   250 000 tokens (230 000 cached, 3 000 out) was booked at 0.08375 against a bill of 0.1525.
3. **An unstated cache-write price fell back to the input rate**, under the claim that this never
   under-bills. True of a read; a quarter short of every write, because the one provider that
   reports writes (Anthropic) bills a five-minute write at 1.25x.
4. **A stream that broke off before the vendor reported usage was booked as nothing**, and its row
   — a failure — reached no caveat in any report. Vertex's Gemini and the open models report usage
   only in the last chunk, so a stream this gateway stopped waiting for (`AIRA stopped waiting`, its
   time limit) or whose connection broke cost nothing here while the vendor went on answering and
   billing.
5. **A Claude answer a caller did not stream could lose its whole prompt.** Every Claude request is
   sent as a stream and put together again (`MessageAssembler`, `ADR-0046`); the assembler merged
   the last event's usage with `dict.update`, and Anthropic's own type allows `null` for the input
   counts there. The streamed reader never had this — it takes a count only as a number.

And one label: a reranker's price is per million **queries** (`FRD-147` FR-7), and the field said
per million tokens. Google's $1 per 1 000 queries typed as `1` is a thousand times too small.

## 2. Goals & Non-Goals

**Goals**
- OpenCode is given every rate the gateway charges, and adds up what the gateway books.
- A catalogue entry can state the prompt length above which a second set of prices applies, and
  that set; the gateway bills and reserves at the set a request's prompt selects.
- No price the catalogue leaves empty is ever read as below the bill.
- A stream that broke off is booked at what can be shown it cost — or, where nothing can be, marked.

**Non-Goals**
- **Reconciling with the provider's bill.** `FRD-403` §2 stands; the figures are the gateway's own.
- **More than one step.** The catalogue holds one length and one second set; a list with more says so
  and the first is kept. No model this gateway reaches has two today.
- **The billing account's long-context SKUs** (`FRD-509`). Its lookup skips them on purpose, and its
  SKU matching has not been tried against a real account; the step comes from the reference or is
  typed.
- **A regional surcharge.** Anthropic prices a regional Vertex endpoint above the global list price
  for its newer models; the reference states list prices. An installation that copies list prices
  for an EU region is below its bill by that surcharge — the billing account, where it can be asked,
  states the regional price (`FRD-509`).

## 3. User Stories

- As a **developer** in OpenCode, I want its session cost to be the gateway's, so that what I see
  while I work is what my budget is charged.
- As a **Global Administrator**, I want to state that a model costs more above a prompt length, so
  that a long session is booked at what it is billed.
- As **IT Steuerung**, I want a broken-off request to show up in the spend, or the report to say it
  is a lower bound, so that the figure I am accountable for is not quietly short.

## 4. Functional Requirements

- **FR-1 OpenCode's rates.** The console writes `cache_read` and `cache_write` for every priced model:
  the catalogue's rate where it states one, else what the gateway charges — a read at the input
  price, a write at 1.25 times it, to six places. Where the catalogue states a step at exactly
  200 000 tokens, it writes `context_over_200k` with the step's four rates, filled the same way.
  A step at another length has no key in OpenCode's file and is not written.
- **FR-2 A price above a prompt length.** A catalogue entry carries `long_context_threshold` (the
  longest prompt, in tokens, the ordinary prices apply to) and four `long_context_*` prices.
  Management refuses a length without both its input and its output price, either price without the
  length, a cache price of the step without the length, and a step on a model with no ordinary
  price. The `model.upserted` event carries all five; the gateway reads a missing or non-positive
  length, or a step without both prices, as no step.
- **FR-3 Billed at the step a prompt selects.** A request whose prompt — every input token the
  provider counted, cached and cache-written ones included — is **longer** than the length is billed
  at the second set, every token of it; a prompt of exactly the length is billed at the ordinary set.
  The reservation before it prices its estimated prompt the same way.
- **FR-4 An unstated cache rate.** A read with no rate is charged at the input rate of the set that
  applies; a write with no rate at 1.25 times it. An hour's write stays at twice the input rate.
- **FR-5 The reference states the step.** The model reference reads the list's `tiers` (with their
  length) or the older `context_over_200k`, keeps the first step, says when there are more, and the
  editor fills it — converted at the installation's rate where the other prices are.
- **FR-6 A broken-off stream.** Where a stream broke off after its first chunk:
  - with usage reported before the break, that running total is booked and the row is marked
    `partial` — a floor;
  - with none reported, and the break caused by this gateway's time limit or a lost connection
    (an `UpstreamError` with no status), the request and the answer received are counted at the
    vendor, as for a caller who left (`FRD-128` FR-6), booked as that floor and marked `counted`;
    the row keeps its failure status;
  - with none reported and an error the vendor sent, nothing is booked and nothing sent again.
  `AIRA_COUNT_CANCELLED_STREAMS=false` switches the second look off here as well.
- **FR-7 One reading of Claude's usage.** Both of Claude's readers fold an event's usage into the
  running totals by one function (`take_usage`), which takes a count only as a number.
- **FR-8 A reranker's unit.** The model editor says, for a model that declares `rerank`, that its
  input price is per 1 000 000 ranking units of up to 100 records.

## 5. Design

**Pricing** (`gateway/src/aira_gateway/pricing.py`). `Rates` holds one set and prices a usage;
`Price` is a `Rates` with an optional length and second `Rates`, and `rates_for(prompt_tokens)`
chooses. `PricingService.cost_nanos` and the reservation's `_reserved_cost` both ask it, so the
booking and the hold before it cannot choose differently.

**Why the whole request moves.** Both providers state it that way: past the length, input and output
are priced at the higher set. A model that charged only the tokens past the length at the higher
rate would need a different rule, and none does.

**OpenCode** (`opencode-config.ts`). The rule written down twice — in `pricing.Rates` and here —
is held together by tests on both sides rather than shared, because the console has no gateway code
to call; each states the same three figures (read = input, write = 1.25 × input, step at 200 000).

**The broken-off stream** (`api/serving/streaming.py`). `StreamAccounting._report` already settled a
stream that broke off after reporting; it now marks that figure `partial`, and hands a stream with
no report to the exit's count (`Accounting.unreported`) when `_vendor_may_go_on` says the vendor did
not stop — the same exit, the same floor pricing (`counting.floor_priced`) and the same switch as a
caller who left.

## 6. Data Model

- Management `catalog.Model`: `long_context_threshold` (positive integer, null) and four
  `long_context_*_price_per_million` decimals; migration `catalog/0012`.
- Gateway `model_catalog`: the same, prices in nano-units; migration `0055_long_context_prices`.
- Both declared not personal with the rest of the catalogue (`FRD-625`).

## 7. API / Interface Contract

- `GET/POST/PUT/PATCH /api/v1/models/`: the five fields, read and written; the refusals of FR-2 as
  `400`s naming the rule.
- `model.upserted`: the five fields (`docs/API.md` §4.4).
- The model reference's `prices.long_context`: `{threshold, input, output, cached_input,
  cache_write}` or `null`.

## 8. Security & Privacy

The second look at a broken-off stream sends the request and the received answer to the same vendor
and region once more, as for a caller who left; the privacy notice says so in both languages (edition
2026-10-28). No new stored column holds anything about a person.

## 9. Observability

No new attribute. A counted or partial row is what the reports already flag as a lower bound
(`partial_usage_requests`).

## 10. Testing & Acceptance Criteria

- `gateway/tests/test_prompt_cache_accounting.py` — the cache-write default, the step for every token,
  "longer than" rather than "at least", half a step read as none.
- `gateway/tests/test_cost_budgets.py` — the step arrives on the event and a malformed length is no
  step; a long prompt is reserved at the step.
- `gateway/tests/test_a_stream_that_broke_off_is_counted.py` — both surfaces: a timed-out and a dropped
  stream counted at the vendor, a vendor's error not, the switch, a report before the break marked
  `partial`.
- `gateway/tests/test_vertex.py` — a `null` in Claude's last usage takes no count away.
- `management/backend/tests/test_catalog.py`, `test_model_reference.py` — the refusals of FR-2, the
  event, the reference's two spellings and its first step.
- `opencode-config.spec.ts`, `model-catalog.spec.ts` — the rates OpenCode is given, the editor's step,
  its conversion and refusals, the panel, the reranker's unit.
- Mutations `CO-1`–`CO-23`.

**Acceptance**

- *Given* a model priced 1.25/10 with 0.125 cached and a step above 200 000 at 2.50/15/0.25, *when* a
  request reads 250 000 tokens (230 000 cached) and answers 3 000, *then* it is booked at 0.1525.
- *Given* a use case's OpenCode file for a model priced 0.30/2.50 with no cache prices, *then* the file
  states `cache_read: 0.3` and `cache_write: 0.375`.
- *Given* a streamed request this gateway stopped waiting for after the first chunk, with no usage
  reported, *then* its row is `504`, `counted`, at the vendor's count of the prompt and the answer.

## 11. Dependencies & Risks

- Whether Google bills a stream the connection lost is not documented; the floor is booked because
  the vendor demonstrably read the prompt and began answering, and is marked as the floor it is.
- The reference's steps are a third party's claim; on 2026-10-28 models.dev no longer lists a step for
  Claude Sonnet 4.6 or Opus 4.6–4.8, which it did on 2026-10-17.

## 12. Rollout / Demo

Images only. A catalogue without steps prices exactly as before; an installation sets a step in the
model editor or fills it from the reference. Existing OpenCode files keep their old prices until the
connection panel's file is downloaded again.
