# FRD-630 — A request explained to a technician

> Phase: 6 (reporting) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner, after long OpenCode sessions with Claude ended in `499`s that nobody could
> place: the console said the caller's connection had closed, in a sentence, and the question a
> technician asks — after how long, after what silence, while the provider was doing what — had no
> answer anywhere. Asked for: the failures shown technically rather than as plain text, with the
> actual error and the trace, without breaking strict data mode.
> Related: `FRD-628` (what happened to a request), `FRD-627` (strict data mode), `FRD-141` (why a
> provider refused), `ADR-0046` (streams keep their connection), `FRD-625` (privacy notice).

## 1. Summary

`FRD-628` says what happened to a request in words: an outcome, whose it was, the message the
caller got, each decision on the way. That is enough for the people who run a use case and too
little for whoever has to find a fault. A `499` after four minutes looks the same whether a proxy
cut an idle connection, a client gave up, or a load balancer capped a request's length; a `502`
looks the same whether Google refused with `RESOURCE_EXHAUSTED` or the connection was reset.

Every request now keeps a **technical record**: where it ended, each milestone in milliseconds from
its arrival, what reached the caller, what the provider's answer stated about itself, and the
exception chain by class. The console shows it under *Technical details*, copies it as text for a
ticket, and links the request's trace where the installation names a trace viewer.

## 2. Goals & Non-Goals

**Goals**
- A `499` answers *after how long* and *after what silence* the connection closed, and whether the
  provider was still answering.
- A provider failure carries the provider's status code, its error word, its own id for the request
  (what its support asks for), `Retry-After`, and the host.
- A transport failure carries the exception chain and the operating system's word (`ECONNRESET`).
- A failure of the gateway's own names the line it came from.
- The trace is one click away where a viewer is configured.
- Nothing in it can carry a person or content, so strict data mode has nothing to withhold.

**Non-Goals**
- **No provider or exception text.** A provider's message can quote the prompt, or a URL with a key
  (`FRD-141` reads structured fields for the same reason); an exception's text carries whatever it
  was handed. The caller's message stays in `error`, as before.
- **No stack trace.** One line of the gateway's own code for its own failures; frames from
  libraries say where a failure surfaced, not why, and locals are content.
- No change to what a caller is told.

## 3. User Stories
- As an **operator**, I want to see how long a dropped stream was open and how long it had been
  silent, so that I can tell my proxy's idle timeout from a client's.
- As an **operator**, I want the provider's request id and error word, so that I can open a case
  with the provider without reproducing the request.
- As a **use-case administrator** under strict data mode, I want the same technical facts as
  anybody, because they describe the request and not the person.

## 4. Functional Requirements

- **FR-1 — The record.** `request_logs.technical`, JSON, NULL for a request refused before anything
  happened to it. Keys, all optional:
  - `phase`: `completed`, `before_dispatch`, `waiting_for_provider`, `mid_answer`,
    `after_provider_finished` — derived from the milestones (`technical.phase_of`). A caller who
    left before the provider's first chunk was `waiting_for_provider`, however long the gateway then
    read on for the vendor's figure (`ADR-0041`).
  - `timeline_ms`: milliseconds from arrival to `dispatched`, `provider_first_chunk`,
    `provider_finished`, `response_started`, `last_byte_to_caller`, `caller_left`, `recorded` —
    each present only when reached; a milestone keeps its first moment, the last byte its last.
  - `to_caller`: `bytes`, `writes`, `keepalives` — counted where they left the application.
  - `provider`: `status`, `reason`, `request_id`, `retry_after`, `host`, `where` (`in_stream`).
  - `exception`: `chain` (class names, outermost first, at most six), `errno`, `at` (a failure of
    the gateway's own: `aira_gateway/…py:line in function`).
- **FR-2 — Codes and numbers only.** Every text value matches one pattern (letters, digits and
  `._/:@<>+=-` and spaces, at most 160 characters); anything else is dropped when the record is
  written, not stored and filtered later.
- **FR-3 — Where it is measured.** Arrival, the response's start and every body write are seen by
  the request-id middleware; `dispatched` at the end of the pre-dispatch sequence; the provider's
  first chunk and finish where the stream's chunks are observed, or when an answer read whole is
  settled; `caller_left` the moment the body is cancelled, before any drain.
- **FR-4 — The request's own.** A pipeline step's call, recorded on a row of its own, carries none.
- **FR-5 — Served with the request's details.** `GET /v1beta/traces/{id}` returns `technical` beside
  `pipeline_decisions`, `degraded` and `error`; the list does not. Strict data mode withholds the
  person on the row as before and nothing of this record.
- **FR-6 — The console.** *Technical details* under *What happened*: open for a request that was not
  served. The phase, the milestones in order with the gap before each, how long a closed connection
  was open and how long it had been silent, what reached the caller, the provider's facts, the
  exception chain, the operating system's word and the gateway's line. The trace id, linked where
  `AIRA_TRACE_URL` names a viewer. **Copy for a ticket** copies the same as text — never the person,
  which strict data mode may withhold from the very reader copying it.
- **FR-7 — The trace link.** `AIRA_TRACE_URL`, written into `runtime-config.js` at the console's
  start, with `{trace_id}` in place of the id. Linked only when it is an `http(s)` address that
  names the id.
- **FR-8 — The privacy notice** names the column and describes it in every language, with a new
  edition.

## 5. Design & Architecture

`aira_gateway.technical.Technical` lives on the request's state. The middleware creates it, so the
clock starts at arrival; the recorder snapshots it when the row is written. `UpstreamError` gained
`facts`, filled by `exchange.refused` from the response's status, the diagnosis's reason word and
headers (`request-id`, `x-request-id`, `apim-request-id`, `x-ms-request-id`, `x-goog-request-id`,
`retry-after`), by `exchange.unreachable` from the host and the cause, and by `stream_failure` from
the in-stream error's type.

## 6. Data Model
Gateway migration `0058_request_technical_record`: `request_logs.technical JSON NULL`.

## 7. API / Interface Contract
`docs/API.md`, `GET /v1beta/traces/{id}`.

## 8. Testing & Acceptance
- The stream's milestones watched on the wire (`test_a_quiet_model_keeps_its_connection.py`): a
  completed stream's bytes, writes and keepalives equal what left; a caller who leaves mid-answer
  and one who leaves before the first word; a chain that runs out after the status.
- The record's rules and the provider's facts (`test_a_request_keeps_its_technical_record.py`):
  every phase; a reset connection's `ECONNRESET`; the gateway's own line; Google's and Anthropic's
  words and request ids; a prompt and a key in a provider's message never reach the record.
- The console (`technical-details.spec.ts`): the timeline, the silence before a close, the provider
  table, the link and its refusal of a `javascript:` template, the copied text without the person.

## 9. Dependencies & Risks
- A timeline measured inside the gateway cannot see a proxy in front of it. What it shows is when
  the gateway saw the connection close and when it last wrote; together they place the cut.
- Milliseconds from one process's monotonic clock: comparable within a request, not across hosts.
