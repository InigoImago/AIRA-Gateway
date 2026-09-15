# ADR-0026 — Speech output is model access, and the audit trail describes its audio

- **Status:** Accepted
- **Date:** 2026-09-14
- **Deciders:** Vadim Scheibe
- **Amends:** [ADR-0013](ADR-0013-auditable-model-access-not-agents.md). The decision stands; one
  refusal that cited it did not follow from it.
- **Realised by:** [FRD-624](../features/FRD-624-speech-output-on-the-gemini-surface.md).

## Context

The Gemini surface refused `speechConfig` with "speech synthesis is not part of direct model access
(ADR-0013)", and `responseModalities` with "this gateway returns text (ADR-0013)". `ADR-0013` says
nothing about audio. Its test is whether a feature makes model access better governed and
evidenced, or makes the gateway think for the use case. A speech model is ordinary model
inference: the use case sends text and receives audio, the provider bills it as output tokens, and
the gateway decides nothing about what is said.

A second question comes with it: what the audit trail keeps. PCM at 24 kHz is about 48 KB a
second, about 4 MB a minute once base64-encoded, and every request would store it in Postgres.

## Decision

1. **Speech output is in scope** on the Gemini surface, under the same governance as any model:
   the catalogue declares which models speak, approval and release apply, and it is priced and
   budgeted.
2. **The audit trail describes the audio and does not keep it.** It records the media type, the
   size, the duration where the format states a rate, and the SHA-256 of the decoded audio the
   caller received. The text that was spoken is the prompt, which is stored as every prompt is.
   The hash proves which audio was served without keeping it.

## Consequences

- Nobody can listen again to what was served. Anybody holding a copy can prove it is the one that
  was served.
- A deployment that must keep the audio needs a decision of its own. It would be a second storage
  path, which `ADR-0016` and `FRD-135` §5.3 argue against.
- The KIRA surface is unchanged: its contract has no field for audio.
