# FRD-609 — A model in several regions, tried in order

> Phase: 6 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner: *"in the catalogue I would like to be able to enter several regions, and they
> should also be checkable — whether the model is reachable, and the same for the thinking methods.
> And check what else could depend on this."*
> Related: `FRD-115` (residency), `FRD-507` (cataloguing is enough), `ADR-0012` §6, `ADR-0021`.

## 1. Problem

A model's region was a single string, in both planes and on the wire. That is right for a model
that lives in one place and wrong for one that does not — and there are two ordinary reasons to
list several: a region runs out of quota, and a vendor rolls a family out region by region.

## 2. Decision

**An ordered list. The first entry is where an ordinary request goes; the rest are what the gateway
falls back to.** Chosen by the owner over a preference-only reading, because failover is the point.

### 2.1 Which failures move to the next region

The whole feature is in this distinction:

| | | |
| --- | --- | --- |
| **the place** | `404` `408` `429` `500` `502` `503` `504` | not deployed here, no quota here, unwell here → **next region** |
| **the request** | `400` `401` `403` `422` | identical everywhere → **refuse now** |
| **the content** | any `200` | the model answered → **never**; a second region would be shopping for a verdict |

A `403` retried across three regions would also triple the failed-auth count somebody is alerting
on, which is a second reason it is not in the first row.

### 2.2 Residency keeps exactly one owner

The failover loop holds **no copy** of `AIRA_ALLOWED_REGIONS`. It learns that a region is not
permitted by addressing it and being told — `transport.url()` raises `RegionNotAllowed` — and steps
over it. So a model catalogued in `europe-west1, global` works unchanged on an installation that
permits only the first, and widening or narrowing the policy needs no second edit anywhere.

### 2.3 A stream that has sent a byte is committed

The chain is walked while **opening**: connect, and let the status check run. Once a chunk has been
yielded, every later failure propagates untouched — a client with half an answer cannot have it
continued by a different region. The same rule this project already states about model fallback,
one axis along.

## 3. What else depended on it

The owner asked. Three things were **already wrong** before this feature and would have become
systematically wrong with it:

- **The audit row named the configured region, not the one that answered.**
  `provenance_for(provider)` returns the region of the first *configured* model on that adapter —
  right for a configured model, a guess for a catalogued one, and with a chain a confident wrong
  residency claim on every request that used a fallback. `CanonicalResponse.served_region` carries
  the truth now, through `AuditTrail.served_region`, and `provenance()` prefers it. A wrong claim is
  worse than a blank one: a blank column is neither a claim nor evidence.
- **A leaked stream on every refused connection.** `_StreamContext.__aenter__` raised after the
  inner httpx context had been entered, and Python does not call `__aexit__` when `__aenter__`
  raises. One leak per refused stream — invisible until `429`s arrive often enough. Failover turns
  that path from the unlucky end of a request into something a healthy request walks through.
- **A guard claiming `addressing` was read by nothing.** True until 2026-08-19, and no assertion
  could catch it: the check subtracts the payload keys *and* the exemption list, so a field that is
  genuinely offered is subtracted twice.

And the ones that simply needed following: `_target` → `_targets`, the canonical request's
`addressing` typing, the reachability and thinking checks, the console's field, and the reporting
column — which stays **one** value per row, because a request goes to one place.

## 4. Checking

Both checks are **per region**, chosen by the owner over a cheaper approximation:

- reachability, one free `:countTokens` per region;
- every thinking word in every region, one output token per accepted word and nothing for a refused
  one.

The second is not thoroughness for its own sake. Which words a place accepts is not knowable from
here: a vendor rolls a family out region by region, so `thinkingLevel` can work in one and answer
*"not supported by this model"* in another — and a declaration checked in one region would be a
claim about the others.

The summary is the **best** of the regions, with the failures named beside it. A model that answers
in one of its three regions *is* reachable — the request will be served — and a summary of "not
reachable" would be false. Both together are what an administrator needs: it works, and here is the
one that does not.

## 5. Testing

`test_region_failover.py` pins the distinction in §2.1 status by status, both directions of the
residency step-over, the last-failure-wins rule, and the audit row taking the served region through
both of `provenance`'s branches. `test_model_check.py` pins the per-region checks.
`test_the_two_readers_of_a_region_list_agree` runs the same eight shapes through the gateway's two
readers, and `read-regions.spec.ts` runs them through the console's third.

Sixteen mutations. `V26` — *the audit row prefers the region that answered* — **survived its first
form**, which is how the residency-evidence test came to exist: the code was right and nothing
would have noticed it becoming wrong.

## 6. Risks

- **A chain hides a sick region.** A model whose first region is permanently unwell answers from
  the second and looks healthy. The per-region check is the answer, and it is a button somebody
  presses rather than a monitor — named here rather than left to be discovered.
- **Latency doubles on failover.** A request that walks two regions waits for the first to fail.
  Bounded by the upstream timeout, and the alternative is a refusal.
- **Order is a decision nobody is prompted for.** The console shows which entry is first and says
  what that means; it does not ask whether the reader meant it.
