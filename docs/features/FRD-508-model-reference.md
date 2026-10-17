# FRD-508 — Starting a catalog entry from a public model list

> Phase: 6 (catalog) · Status: **Built** · Owner: Vadim Scheibe
> Origin: the owner, after setting up Vertex on a standalone installation: adding a model meant
> researching a dozen facts per model, and Vertex publishes no list to start from.
> Decision: `ADR-0038`. Related: `FRD-507` (importing what the adapters serve), `FRD-307` (only
> approved models), `FRD-114` (the declaration), `FRD-403` (prices), `FRD-506` (reachability).

## 1. Summary

*Add a model* offers, for the platforms it describes, the models a public list —
[models.dev](https://models.dev) — states, with their limits, capabilities, reasoning options and
list prices. Choosing one opens the ordinary editor filled in, says what was filled and from
where, and leaves the region, the check, the approval and the release to the administrator.

## 2. Goals & Non-Goals

**Goals**
- No research to *find* a model's facts; research to *confirm* them.
- Every draft is a declaration the catalog accepts as it stands.
- Works without internet access: the release ships the list.

**Non-Goals**
- Approving, releasing or saving anything (`FRD-307`).
- The region: whether a model is offered in a region is not in the list, and residency is not a
  guess. The reachability check answers it.
- Contract prices, currency conversion, regional surcharges.
- Microsoft Foundry: its entries are deployments, not models.

## 4. Functional Requirements

- **FR-1 — Drafts in this catalog's vocabulary.** The list's Vertex (Google, Anthropic, Z.ai and
  DeepSeek publishers) and AI Studio entries become drafts under the provider names the gateway
  stamps (`vertex`, `generative-language`): capabilities from its flags and modalities; context and
  output cap from its limits (none for an embedding model, none above the context for a speech
  model); thinking from its reasoning options — effort words are levels and `none` is `disabled`, a
  budget range is `limited` with its bounds (a missing ceiling is the output cap less one, said in
  the draft's notes), a toggle is `disabled` and `auto`, and a floor above zero drops `disabled`;
  attachments as the image and PDF media types the gateway carries; prices per million tokens. A
  model whose output the gateway cannot carry, or from a publisher it does not speak, is not offered.
- **FR-2 — Shipped, or live.** The release carries the drafts (`models.json`, with the list's MIT
  notice), refreshed by `make model-reference`. `AIRA_MODEL_REFERENCE_URL` reads the live list
  instead, at most daily; a failure answers with the shipped copy and says why, and is not retried
  for five minutes.
- **FR-3 — `GET /api/v1/model-reference/`**, for `catalog.write`. Writes nothing.
- **FR-4 — The console.** In *Add a model*, a provider the reference describes shows its models,
  current first, deprecated last and marked, searchable by name. One already catalogued is opened as
  it is. Choosing one fills name, display name, publisher, limits, capabilities, thinking and media
  types, and a banner names what was filled, the source and date, and the draft's notes. Prices are
  filled only where the installation's currency is the list's; otherwise the banner states them.
  The region stays empty.
- **FR-5 — A model named by hand.** The editor offers the reference's ids for the chosen provider as
  the id is typed, and for a new entry whose id the reference describes, *Fill from the model
  reference* fills it the same way, keeping the provider and regions already in the form. Not over a
  declaration on file. A reference that cannot be loaded leaves naming by hand as it was.

- **FR-6 — The editor is a section of its own.** Declaring or correcting a model replaces the
  catalog on its page instead of opening a window: three tabs of fields, the check and the decisions
  had been compressed into a window bounded by the viewport until each tab scrolled inside it. One
  question per row, at a form's width; the standing decisions and the actions in a foot held at the
  viewport's bottom, so they stay in one place on every tab. The address is in the URL —
  `?editor=new`, `?editor=<model>` — so Back closes the editor (discarding the draft, as Cancel
  does) and a link opens the model it names once the catalog has loaded. Escape no longer closes it:
  on a page it would discard a draft by accident.

## 8. Security & Privacy

The reference names models, not people. The live list, where configured, is one outbound GET from
Management that sends nothing about the installation beyond its address. The privacy register is
unchanged.

## 10. Testing & Acceptance Criteria

`management/backend/tests/test_model_reference.py`: every shipped draft passes
`validate_declaration`; each mapping rule; shipped by default, live held, failure paused; the
permission; the route. `model-catalog.spec.ts`: the list in the browse window, the fill, the currency
rule, the region left empty, the save. Mutations `RF-1`–`RF-25`, `ED-1`–`ED-4`.
