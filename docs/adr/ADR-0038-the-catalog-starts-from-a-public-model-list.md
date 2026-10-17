# ADR-0038 — A catalog entry starts from a public model list, and is still decided by a person

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Vadim Scheibe

## Context
Cataloguing one model asks for a dozen facts — its context window and output cap, which
capabilities it has, its thinking modes, budgets and level words, the document types it reads, four
prices — and the owner, setting up Vertex, found that finding them took longer than anything else.
Google does not help: Vertex publishes no listing a credential may read, and Model Garden's API
returns names and launch stages, not limits or prices. `FRD-507` deliberately imported neither
prices nor capabilities from a vendor listing, because a vendor's flag is a claim and an invented
price is worse than none.

[models.dev](https://models.dev) is an open (MIT) list, maintained in public and read by OpenCode,
that states exactly these facts per provider — Vertex, Claude on Vertex and AI Studio among them.

## Options considered
- **Keep typing by hand.** Honest and slow; the cost the owner reported.
- **Ask Google.** Model Garden's API has no limits or prices; the Cloud Billing catalog has prices
  as SKUs whose names have to be matched to models by text. Two sources, both incomplete.
- **Prefill from models.dev, as a draft.** Every fact in the form, in this catalog's vocabulary,
  marked with its source and date; nothing saved, approved or released by it.

## Decision
Prefill from models.dev (`FRD-508`). It narrows `FRD-507`'s non-goals rather than abandoning
their reason: a claim may **fill a form** where it says it is a claim, and still decides nothing.
The rules that keep it a draft: the source and date are shown beside what was filled; a price is
copied only where the installation counts in the list's currency (US dollars) and is otherwise
shown, not filled; the region is never filled, because residency is not a guess; an existing entry
is opened, never drafted over; nothing is approved. Capabilities remain what the gateway's checks
and the smoke tests measure.

**The release ships a copy** (`models.json`, refreshed by `make model-reference` before a release,
with its MIT notice), and that copy is the default: an installation reaches no new address because
it was upgraded. `AIRA_MODEL_REFERENCE_URL` opts into the live list.

## Consequences
- Positive: adding a Vertex model is choosing it from a list and checking reachability; the
  research left is confirming, not finding.
- Negative / trade-offs: a third party's errors reach the form — mitigated by saying so, by the
  catalog's own validation (every shipped draft is tested to pass it) and by the reachability check.
  List prices are not contract prices, and Claude on a regional Vertex endpoint costs more than its
  list price. The shipped copy ages between releases.
- Follow-ups: a notice when a catalogued model's list price changes; Foundry, whose catalog entries
  are deployments rather than models.
