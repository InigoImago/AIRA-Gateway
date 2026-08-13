# FRD-403 — Cost-based budgeting (model prices, spend limits)

> Phase: 4 · Status: **Done** · Owner: Vadim Scheibe
> Origin: user feedback — *"bei der Budgetierung kann man nur die Anzahl an Tokens angeben, nicht
> die Kosten, das finde ich problematisch."*
> Builds on FRD-400 (budget model), FRD-401 (enforcement), FRD-402 (budget UI). Introduces the
> price half of the catalog FRD-307 designs.

## 1. Problem

Budgets could only cap **tokens** and **request counts**. Neither is a cost control:

- A token differs in price by more than an order of magnitude between models. A cap of "1M tokens
  per month" is a few cents on a small model and a substantial sum on a large one — the same
  number means entirely different things depending on which model the traffic happens to use.
- **Input and output tokens are priced differently** by every provider AIRA talks to (commonly
  4–5× more for output). Even with a known price, a single `total_tokens` figure cannot be
  costed correctly.
- The audience for this product includes IT Steuerung — a governance function that is accountable
  for spend, not for token volume. The unit the tool budgets in was simply the wrong one.

## 2. Goals & Non-Goals

**Goals**
- Budgets can cap **spend** for a period, in the installation's currency.
- Per-model prices, maintained centrally, split by direction (input / output) per 1M tokens.
- The gateway prices each request and enforces cost limits with the same 429 as other limits.
- Every request's cost is recorded in `request_logs`, so spend can be reported, not just capped.
- Consumption that **cannot** be costed is visible as such, never as zero.

**Non-Goals**
- Multi-currency and exchange rates (see §4.1).
- Invoicing, chargeback, or reconciliation against the provider's bill.
- Automatic price discovery from provider APIs — prices are entered.

## 3. Functional Requirements

- **FR-1 Price catalog**: a Management `Model` record per model — `name`, `display_name`,
  `provider`, `input_price_per_million`, `output_price_per_million`. Read by any authenticated
  user; written by a **Global Administrator only**, because a price is a fact about the provider
  contract, not a per-use-case setting.
- **FR-2 Half prices refused**: a model is priced in both directions or in neither. One-sided
  pricing would produce a figure that looks complete and silently omits half the cost.
- **FR-3 Distribution**: `model.upserted` / `model.deleted` over the compacted Kafka topic
  `aira.models` → gateway `model_prices` read-model (FRD-204 mechanics).
- **FR-4 Cost limit**: `Budget.limit_cost` alongside the existing token and request limits. At
  least one limit of any kind is still required.
- **FR-5 Pricing**: the gateway computes each request's cost from the prompt/completion token
  split and the model's prices, once per request, shared by the budget counters and the audit log
  so the two cannot disagree.
- **FR-6 Enforcement**: pre-dispatch, an exhausted cost limit rejects with
  `429 RESOURCE_EXHAUSTED`, exactly like the count limits.
- **FR-7 Unpriced traffic**: a request whose model has no price is counted under
  `unpriced_requests`, **not** as costing zero, and does not consume the cost budget. The UI names
  the gap.
- **FR-8 Reporting**: `request_logs.cost_nanos` per request (NULL when unpriced); the usage
  endpoint reports consumed spend per budget.
- **FR-9 Display**: an amount is never rendered as `0.00` when it is not zero — the display
  widens its precision until it is truthful.

## 4. Design

### 4.1 One currency per installation

`AIRA_CURRENCY` (default `EUR`) states what every price and limit is denominated in. There is no
conversion anywhere.

The alternative — a currency per price plus exchange rates — was rejected: it requires a rate
source, a rate date per booking, and a story for retroactive corrections, all to serve a case
(one organisation buying from one provider) that rarely arises. A single currency is exact and
has no moving parts.

### 4.2 Money is an integer, never a float

Amounts are stored and computed as **nano-units** (10⁻⁹ of the currency; 1 EUR =
1_000_000_000 nanos) in `BIGINT` columns, via `aira_common.money`.

Binary floating point cannot represent 0.1 exactly; a spend figure is the sum of millions of
small charges and would drift. `NUMERIC` would be exact on Postgres but SQLite — which the test
suite runs on — stores it as a float, so the tests would not be testing the production
behaviour. Integers are exact on both.

Consequently, amounts cross service and API boundaries as **decimal strings**, never as JSON
numbers: `{"input_price_per_million": "0.075"}`. A JSON number is a float by the time it is
parsed.

```
tokens_in × input_price_nanos ÷ 1e6  +  tokens_out × output_price_nanos ÷ 1e6  =  cost_nanos
```

### 4.3 Where the pieces live

| Piece | Where |
|---|---|
| Price catalog + spend limits | Management (`catalog` app, `budgets` app) |
| Distribution | Kafka `aira.models`, `aira.budgets` |
| Prices, cost accounting, enforcement | Gateway (`pricing.py`, `budgets/service.py`) |
| Per-request cost | Gateway `request_logs.cost_nanos` |
| Spend limits + consumption UI | SPA budget tab; prices under **Models & prices** |

### 4.4 Unknown is not zero

The single most important rule in this feature. A request served by an unpriced model **did**
cost money; AIRA just cannot say how much. Booking it as `0` would make a spend figure that is
silently too low — the worst possible failure mode for a number someone is accountable for. It is
therefore counted separately, excluded from the cost total, and surfaced in the UI, both on the
budget tab and in the catalog.

## 5. Testing & Acceptance

- **Money**: exactness under accumulation (a million charges of 0.000001 sum to exactly 1.00),
  the classic `0.1 + 0.2` trap, direction-specific pricing, display never showing a non-zero
  amount as zero.
- **Gateway**: pricing by direction, unpriced → `None` (not `0`), cost limits blocking at the
  threshold, unpriced traffic counted apart and not consuming the cost budget, cost and count
  limits coexisting, prices and limits arriving through the consumer as exact decimals.
- **Management**: only a Global Administrator writes prices; half-priced models refused; prices
  published as strings; cost limits accepted and published.
- **Frontend**: spend limit validation (including a comma as decimal separator), the bar computed
  from nano-units, the unpriced warning, and the catalog screen (read-only for non-admins).
- **Acceptance** (verified against the live stack): price `mock-1` at 1.00 / 10.00 per 1M, send
  three requests of 5 input and 8 output tokens each → each priced at exactly 85 000 nanos,
  accumulated to exactly 255 000; lower the limit below that and the next request is refused with
  `429 Cost budget exhausted`.

## 6. Consequences & follow-ups

- Token and request limits remain supported and are still useful as a volume guard; the UI leads
  with spend.
- A price change applies from the moment it propagates. Historic `request_logs` rows keep the
  cost computed at the time — which is what makes them auditable, but means a corrected price
  does not retroactively fix past figures. Recalculation is out of scope.
- **Follow-ups**: seed prices for the catalogued models as part of the demo data; report spend
  over time (the data is now in `request_logs`, no view exists yet); the rest of FRD-307
  (approval and model pickers) on the same `Model` record; alerting when a budget approaches its
  limit.
