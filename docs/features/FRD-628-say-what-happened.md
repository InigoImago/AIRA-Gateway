# FRD-628 — Say what happened to a request

> Phase: 6 (reporting) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner. Strict data mode (`FRD-627`) took the prompt and the answer away from the
> people who run a use case and from the developers who call it; what is left to tell them why a
> request failed, or was changed on the way, has to be enough on its own. Constraint, also the
> owner's: the Gemini surface stays Gemini-compatible, and standard clients — the `google-genai`
> SDK, OpenCode through the AI SDK, plain HTTP libraries — keep working unchanged.
> Related: `FRD-302` (chains), `FRD-300` (pipeline), `FRD-502` (requests), `FRD-107` (KIRA).

## 1. Summary

An audit of both surfaces found that the gateway refuses precisely what a caller caused, and says
almost nothing about what it did itself: a pipeline block is a 400 indistinguishable by code from a
malformed request; re-routing, redaction and flag-only filters are silent; an exhausted fallback
chain reports only the last provider's bare status; a budget refusal names no reset. And the
explanation that does exist — every step's verdict, every candidate tried, stored on the audit row as
`pipeline_decisions` — is served to nobody, not even IT Security, while the message the caller got is
not stored at all.

## 2. Goals & Non-Goals

**Goals**
- Every response carries an id that finds its request in the console.
- A request's details say, in words, what happened: each pipeline step, each model tried, the
  outcome, the message the caller got.
- A refusal says which control refused, in a form a program can read, without changing its status.
- A served answer says which model answered and what the pipeline did to the request.

**Non-Goals**
- **No new body fields on a successful Gemini response**, and no new KIRA envelope fields or stream
  events: SDKs validate bodies, and a migrating KIRA client changes a base URL and nothing else.
  What is new travels in **headers**, which every client ignores unless it looks.
- **No change of any status code**: a client's retry logic keys on them.
- Showing content: the decisions are metadata, and strict data mode still withholds the person.

## 4. Functional Requirements

- **FR-1 — A request id on every response.** `X-AIRA-Request-Id`; for a request that is recorded it
  is the audit row's id, which the console and `GET /v1beta/traces/{id}` find.
- **FR-2 — What the pipeline did, on a served answer.** `X-AIRA-Model`: the model that answered.
  `X-AIRA-Pipeline`: one `step=action` per step that ran, comma-separated (`model_route=a>b` for a
  re-route). Both exposed to browsers (CORS). A stream whose response had to begin before any
  model answered (`ADR-0046`) carries no `X-AIRA-Model`: a fallback may yet answer, and the chunks
  and the row name the one that did.
- **FR-3 — Which control refused.** `X-AIRA-Reason` on every refusal, one word from a closed list
  (`blocked_by_pipeline`, `budget_exceeded`, …, the audit's own outcome words). On the Gemini surface
  also Google's own form: `error.details` with one `google.rpc.ErrorInfo` (`reason` in upper case,
  `domain: aira.gateway`, `metadata`: `request_id`, and `step` for a pipeline block, `resets_at` for a
  budget). The status and the message's first sentence are unchanged.
- **FR-4 — The caller's message is kept.** A new `error` column on the audit row, bounded, with
  credentials redacted as payloads are.
- **FR-5 — The details explain.** `GET /v1beta/traces/{id}` adds `pipeline_decisions` (the stored, content-free
  summary), `degraded` and `error`. The console renders the outcome in words, a timeline of the
  decisions — steps with their verdicts, routes, redactions, each candidate tried or skipped and why
  — and the caller's message; and a field to find a request by its id. For a request that was not
  served it says first **where it ended**: in the request, in a rule of AIRA's, in AIRA's
  configuration, in AIRA itself, at the model provider, or on the caller's side of the connection —
  the owner's question about every failure, *was it AIRA or the provider?*
- **FR-6 — Better messages where they said too little.** An exhausted chain names every candidate and
  why it failed; a budget refusal names when the period resets and whether the limit is spent or this
  request's estimate is too large to fit; a body too large names the limit. A failure says **who
  acted** in its first words: the provider by its platform's name (`Google Vertex AI returned 529.`,
  `The model provider broke off the answer mid-stream: …`), or AIRA where the gateway stopped
  (`AIRA stopped waiting: Google Vertex AI sent nothing for 120 s …`, `AIRA could not reach …`,
  `AIRA had no free connection …`) — and the status follows: the gateway's own `504` or `503` where
  it stopped, never the masked `502` that reads as the provider being down.
  **A model the provider does not serve is final, and the status says so** (amended 2026-10-28): a
  provider's `404` to a model call — retired, not offered in the region, not enabled for the account
  — is `404 NOT_FOUND` on the Gemini surface once every region was asked, and the message adds that
  asking again will not change that, in this gateway's words (Google's quotes the project). As a
  `502` it was retried as an outage: the same turn six times by OpenCode 1 and eleven by OpenCode 2.
  The KIRA surface keeps the predecessor's one status and carries the sentence. A `404` to a `GET` —
  a listing, a readiness probe — is a missing route and gets no sentence about a model.
- **FR-7 — From a request to the pipeline's test.** A row a pipeline decided about links to the
  use case's pipeline test, to try it with text of one's own where the original cannot be read.

## 8. Security & Privacy
The decisions and the message are about the request, not its content; they are shown wherever the
row is, and strict data mode withholds the person on them as on the row. The new column is in the
privacy register.

## 10. Testing & Acceptance Criteria
The real `google-genai` SDK reads the new error envelope and a served answer with the new headers;
the AI SDK's Google provider parses the error. Each FR has a hermetic test and a mutation.
