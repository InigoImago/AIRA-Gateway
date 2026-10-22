# ADR-0039 — Reranking is a capability of the core, served on both surfaces

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** Vadim Scheibe

## Context

The predecessor ranks records against a query (`POST /rerank`, Google's ranking API). Its clients
need it to migrate, and the owner wants it on the Gemini side too — with the system kept strict:
no model reachable without authentication, attribution and release.

Reranking is not a Gemini method. Google serves it from the Discovery Engine, a different service
from Vertex, at `global`, billed per query rather than per token.

## Options considered

- **A — A KIRA-only route that calls Google directly.** Smallest. But it is a model call outside
  the shared sequence: no pipeline, no release, a budget it never books. Exactly the unmetered
  corner the governance exists to prevent, and the second surface would build it again.
- **B — A Gemini-style method (`models/{m}:rerank`) on the native side.** No client knows it; an
  invented method is an API nobody calls.
- **C — A capability of the core, with two thin surfaces**: the predecessor's route and Google's own
  ranking path, so Google's SDK works against the gateway unchanged.

## Decision

**C.** Reranking is `Capability.RERANK`, a canonical request and an adapter method, reached only
through `prepare_for_dispatch` and `resolve_direct_target`. Each surface parses its shape and
renders its errors, as `FRD-126` requires.

Two consequences of the provider are decided here rather than left to configuration:

- **Metering** is Google's unit: a query of up to 100 records. It is booked as requests and as the
  entry's input figure, priced per million units. Booking nothing would be a model call no
  budget sees.
- **`global` is not opened.** A reranker reachable only at `global` is permitted by name
  (`ADR-0034`), like every such model.

## Consequences

- Positive: one set of controls for every model call; Google's SDK and the predecessor's clients
  both work; a third surface would add a parser, not a path.
- Negative / trade-offs: a reranker's "tokens" in reports are ranking units — stated in the catalogue
  documentation and the API reference. The Discovery Engine is a new recipient for the privacy
  notice.
- Follow-ups: confirm whether `eu` serves the ranking API; if it does, the default addressing moves.
