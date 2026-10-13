# ADR-0034 — A region outside the list is permitted for a model by name, never for every model

- **Status:** Accepted
- **Date:** 2026-10-13
- **Deciders:** Vadim Scheibe

## Context

Residency is one policy with one owner (`ADR-0012` §6): `AIRA_ALLOWED_REGIONS` lists where requests
may be processed, every transport is measured against it, and the console refuses an impermissible
region where it is typed (`FRD-611`). `global` is not in the shipped default and was taken out of
this installation's list on purpose, because it names no region and guarantees none — Google's own
data-residency page says a global endpoint may process a request anywhere.

Agent Platform serves managed open models — GLM, DeepSeek, Qwen, gpt-oss, Kimi, MiniMax — at one
OpenAI-compatible endpoint (`FRD-115` FR-2a). Probed against this installation's project, every one
of them answered at `global` and none in `europe-west1`, `europe-west4` or the `eu` multi-region.
Under the policy as it stood they could not be used at all, adapter or not.

The owner decided to use GLM and DeepSeek, knowing that `global` gives no regional guarantee for
them. The question this ADR answers is how that decision is written down, because the obvious way
says something else.

## Options considered

- **Add `global` to `AIRA_ALLOWED_REGIONS`.** No new mechanism. But the list stops meaning what it
  says: every model a catalogue entry places at `global` becomes permitted — `gemini-3.5-flash`,
  catalogued and refused today, the moment somebody approves it; a Gemini model given `global` as a
  failover region, silently, whenever its EU region has no quota. The decision the owner took was
  about two models, and this records a decision about all of them. It is also the exact state
  `FRD-611` was written to end.
- **Permit `global` per publisher** (`zai-org`, `deepseek-ai`). Closer, and a new GLM version would
  need no change. But a publisher's next model is a model nobody has decided about yet, and the
  policy would then permit something that did not exist when it was written.
- **Permit a region for named models alone** — `AIRA_ALLOWED_MODEL_REGIONS=global=glm-4.7-maas,…`.
  The list keeps meaning what it says for every other model; the exception names exactly what was
  decided; a new model is a new, visible line.

## Decision

**A region outside the list is permitted for a model by name, in a setting beside the list.**

- **One rule, one owner.** `residency.check_region(region, allowed, model=…, for_models=…)` is the
  rule, and every place that measures a *model* against the policy asks it with the model's name:
  the startup check of a configured model, the transport on every address, and the chain's
  per-hop condition. A second copy is how a permission would hold at startup and be refused at
  request time.
- **A place that serves many models is never opened by one model's name.** An OpenAI-compatible
  server, a Foundry resource and Google AI Studio are checked without a model, so only the list
  applies to them.
- **The console is told, not given a copy.** The gateway publishes `modelRegions` beside
  `allowedRegions` on the answer the editor already fetches, and the editor permits a region for
  exactly the model it is permitted for — refusing it there would block the very entry the decision
  was made for.
- **A malformed entry stops the gateway.** It is read per request; a typo that started would refuse
  the model somebody meant to permit, with a residency message that sends them looking for a policy
  they believe they set.
- **The privacy notice says it.** A model released by name for a region outside the list, and what
  `global` means, are stated in the recipients section of both languages (`ADR-0027`).

## Consequences

- Positive: the owner's decision is recorded as made — two models, one place — and `global` stays
  refused for every model nobody decided about.
- Positive: the audit row still names the region each request went to, so a request to an open
  model is evidenced as `global` rather than hidden behind a list that looks EU-only.
- Negative: two settings now answer "where may this be processed", and an auditor reads both.
  `docs/CONFIGURATION.md` lists them side by side, and the console's refusal names both.
- Negative: a model's new version needs its own line. Deliberate — it is a new model.
- Follow-up: if Google begins serving these models in an EU region, cataloguing that region makes
  the exception unnecessary; the exception should then be removed rather than kept as a fallback,
  or a failover would leave the EU silently.
