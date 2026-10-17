# FRD-509 — What a model costs this installation

> Phase: 6 (catalog) · Status: **Built** · Owner: Vadim Scheibe
> Origin: the owner, seeing the reference fill limits and capabilities and leave the prices empty in
> a euro installation; asked whether prices could be pulled too, and chose this: the billing
> account's own prices first, the public list price converted at a set rate where the account has
> none. Related: `FRD-508` (the model reference), `FRD-403` (prices), `FRD-142` (the identity),
> `FRD-141` (why a provider refused).

## 1. Summary

A catalog entry's price should be what the installation **pays**, not what a vendor publishes:
contract discounts, committed use and a regional surcharge all make the two differ. Google's Cloud
Billing Pricing API answers per billing account — each SKU's list price and, under a contract, its
contract price, in the currency asked for. The gateway asks it, with the Google identity it already
has, and the console fills the price fields from it, naming the SKU each came from. Where the
account bills nothing for a field, the reference's US-dollar list price is filled at the rate the
installation set; without a rate it is shown, not filled.

## 2. Goals & Non-Goals

**Goals**
- The price on the bill, in the installation's currency, with the line of the bill it came from.
- A fallback that is explicit about being one.
- Nothing invented: a field neither source prices stays empty.

**Non-Goals**
- Keeping catalogued prices in step with the account later; a notice when they drift.
- Tiered prices (above a context length): the base tier is filled, and the reference says when a
  model has another.
- Other platforms' billing (Azure).

## 4. Functional Requirements

- **FR-1 — The billing account.** `AIRA_VERTEX_BILLING_ACCOUNT` (gateway). With a Google identity —
  key, federation or metadata server, never an API key — `GET /v1beta/providers/{name}/prices
  ?model=&region=` (`catalog.write`) answers `{model, region, billingAccount, currency, prices,
  unpriced}`, each price `{perMillion, currency, skuId, skuName, region, contract}`.
- **FR-2 — The matching.** The account's services whose name says Vertex AI, Claude, Anthropic, Model
  Garden, Gemini, GLM or DeepSeek; their SKUs (every page; held six hours); a SKU names the model
  when its name, without a known prefix and up to the first word that says what is billed, is the
  model's id word for word (`claude-sonnet-4-5` is `claude sonnet 4.5`, `-maas` is dropped). Input,
  output, cached input and cache write by their words; batch, long context, audio, image, video,
  storage, tuning, grounding, provisioned throughput, priority and flex are not a per-token text
  price. The region's own SKU, then a multi-region holding it, then a global one.
- **FR-3 — The price.** One SKU's price, asked in `AIRA_CURRENCY`: the base tier (start 0), its
  `contractPrice` where present, else its `listPrice`, divided by the unit quantity and given per
  million tokens. A unit that is not tokens (characters) is not converted; the field is unpriced and
  says so.
- **FR-4 — The fallback.** `AIRA_MODEL_REFERENCE_EXCHANGE_RATE` (Management): units of
  `AIRA_CURRENCY` per US dollar. The reference answers `conversion: {from, to, rate}` where it is set
  and the installation does not count in dollars; the console fills list prices at that rate, in
  decimal arithmetic, to six places.
- **FR-5 — The console.** Filling from the reference fills the list price (same currency) or the
  converted one, then asks the billing account; each price it returns replaces the field. Every
  filled price names its source under the field — contract or list price, account, SKU and region; or
  list price and rate — and loses it when typed over. *Prices from the billing account* asks again,
  for the form's first region. Why the account was not asked, or which fields it had no price for,
  is said. A billing price in another currency than the installation's is never filled.
- **FR-6 — Refusals.** A missing Billing Account Viewer role (`billing.*` permission) is explained
  as such; every other cause as `FRD-141` explains it.

## 8. Security & Privacy

Read-only. The identity needs `roles/billing.viewer` on the billing account, which shows prices and
not spend. No person's data is involved; the privacy register is unchanged.

## 10. Testing & Acceptance Criteria

`gateway/tests/test_billing_prices.py` — the Pricing API played in its documented shapes: matching,
region preference, contract over list, units, paging, caching, refusals, registration.
`management/backend/tests/test_model_reference.py` — the rate. `model-catalog.spec.ts` — the order
of sources, the per-field source, the currency rule, the decimal conversion. Mutations `BP-1`–`BP-13`,
`RF-26`, `RF-27`, `PR-1`–`PR-8`. **Not tested against Google**: the SKU names in the matching follow
the published naming as far as it could be checked; the first real account is the test, and every
price shows the SKU it was matched to so a wrong match is visible before it is saved.
