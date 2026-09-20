# FRD-136 — Capacity: the gateway is not the bottleneck

> Phase: 9 (operability) · Status: **Done — measured** · Owner: Vadim Scheibe
>
> Origin: the owner. Related: `FRD-127` (several gateway instances), `FRD-405` (rate limits and the
> budget reservation), `FRD-122` (the audit trail that every request writes), `FRD-113` (embedding
> batches), `FRD-131`/`FRD-135` (tools and reasoning on the hot path), `ADR-0008` (shared counters),
> `ADR-0012` §5 (a self-hosted endpoint's 429 means *no free replica*).

## 1. Problem

The installation this gateway is being built for will carry **50–300 people at once**, all day,
across several use cases and several models:

- up to **300 agentic coding** seats — streamed answers, tool calls carried back and forth,
  reasoning returned, long and growing prompts;
- ordinary **chat with a RAG** in front of it — a large retrieved context, a short answer;
- **nightly embedding batches** for further RAGs — few callers, enormous bodies.

The owner's requirement is not a throughput number. It is a **statement about where the ceiling
is**: when the system is slow, it must be slow because a *model* is slow, not because the gateway
is. Everything this gateway does per request — authentication, the read-model lookups, the
pipeline, the rate limit, the budget reservation, the audit row — is work the models never see, and
none of it has ever been measured under load. A control plane that governs 300 people and silently
costs 40 ms and a database connection per request is a governance system that has become the
outage it was meant to prevent.

**This is not `FRD-127`.** That one is about running several instances. This one asks what *one*
instance is worth, because a scaling factor applied to an unmeasured number is not a capacity plan.

## 2. Goals & Non-Goals

**Goals**
- A **repeatable** load harness in this repository that drives the three workloads above against a
  running stack, and costs nothing to run: no cloud model is contacted.
- An upstream double whose latency is **declared rather than incidental**, so the gateway's own
  cost is what is left when the declared latency is subtracted.
- The harness proves it is **not itself the ceiling**: every run is preceded by the same load
  driven straight at the double, bypassing the gateway.
- Numbers for: added latency per request (median and tail), requests per second per gateway core,
  concurrent streams held per instance, memory per in-flight stream, and the point at which each
  named component saturates.
- A named list of what actually saturates first, with the evidence, and what each one costs to fix.

**Non-Goals**
- Tuning. This FRD *measures*; a change it justifies is its own change with its own tests.
- Measuring the models. The double answers instantly or slowly on command; how fast a real model
  is is the vendor's number, not this system's.
- A benchmark of the console or of Management. Neither is on the request path (`FRD-127` §5.2).
- Autoscaling or a capacity *product*. The output is evidence and a sizing statement.

## 3. User Stories
- As the **owner**, I want to know how many people one gateway instance carries, so that the
  instance count is a decision and not a hope.
- As an **operator**, I want to know which component gives way first and what it looks like from
  outside, so that the first production incident is one I have already seen.
- As a **developer**, I want a load run I can repeat after a change, so that a regression in the
  request path is visible before an installation finds it.

## 4. Functional Requirements

- **FR-1 A free upstream.** `tools/loadtest/modelsim.py` serves the OpenAI dialect — chat
  completions streamed and unstreamed, and embeddings — with time to first token, inter-token
  delay, token counts, tool calls and reasoning all set by the caller. It contacts nothing.
- **FR-2 No cloud model is reachable during a run.** The run brings the gateway up with the Vertex,
  Google AI Studio and Foundry adapters unconfigured; a run that could bill somebody is a run that
  eventually does.
- **FR-3 Three workloads**, each a profile with its own concurrency, body shape and think time:
  `agentic` (streamed, tools, reasoning, growing context), `chat-rag` (streamed, large prompt,
  short answer), `embed-batch` (batched vectors, large bodies).
- **FR-4 A baseline that bypasses the gateway.** The same profile, driven at `modelsim` directly,
  at the same concurrency, from the same driver. Reported beside the gateway figures.
- **FR-5 What is reported.** Per profile: completed and failed requests, the failure kinds, p50/p90
  /p99/max of end-to-end latency and of time to first token, throughput, and the **overhead** —
  the gateway's latency less the double's declared latency, which is the only figure that answers
  the owner's question.
- **FR-6 What is sampled.** CPU and memory per container for the duration, the audit queue depth,
  the database connection pool, and the gateway's own error log.
- **FR-7 Saturation is found, not assumed.** A profile ramps until a stated failure condition —
  errors, or a latency multiple — and the run records the concurrency at which it happened.
- **FR-8 The run cleans up after itself.** The use cases, keys, catalogue rows and request logs it
  creates are removable by one command, because a load run's rows in the audit trail are a lie
  about what an installation did.

## 5. Design & Architecture

```
driver (N processes)  ──►  gateway :8001  ──►  modelsim :8099   (measured)
driver (N processes)  ─────────────────────►  modelsim :8099   (baseline)
```

- **`modelsim`** is a plain ASGI application behind uvicorn, in its own container on the stack's
  network. Plain, not FastAPI: it must be cheaper per request than the thing under test, or it
  becomes the thing under test.
- **The gateway** reaches it as one more entry in `AIRA_OPENAI_SERVERS` — the same adapter a
  self-hosted vLLM or Ollama uses (`FRD-123`), so the transport, the streaming and the mapping on
  the path are the real ones.
- **The driver** is asyncio over `httpx`, spread across processes, because one Python process
  driving 300 streams measures the driver.
- **Seeding** writes the use cases, keys and catalogue rows straight into the gateway's read-model,
  as `tools/seed_local_catalog.py` does and for the same reason: the distribution path has its own
  suite, and what a measurement needs is the rows in place.

## 6. Data Model
No schema change. The harness writes ordinary `use_cases`, `api_keys` and `model_catalog` rows, all
under a prefix it owns, and deletes exactly those.

## 7. API / Interface Contract
`modelsim` speaks the OpenAI dialect it is addressed in; the shape it must produce is the one
`upstreams/openai/mapping/response.py` reads. Its behaviour is steered by the model name
(`sim-fast`, `sim-think`, …) and by `x-sim-*` headers the gateway passes through nothing of — so
the name is the contract.

## 8. Security & Privacy
The harness runs against a development stack with keys it created. Its prompts are synthetic, so no
personal data is processed and `apps/privacy/activities.py` is unchanged. The keys it seeds are
active keys in a real read-model: FR-8's cleanup is a security requirement, not tidiness.

## 9. Observability
The run reads what the system already exposes — `/readyz`, the gateway's logs, `docker stats`, the
OTLP pipeline — and adds nothing to the product. Where a figure the run needs has no exposure
(`RequestLogWriter.pending` is an example), that gap is a finding of this FRD.

## 10. Testing & Acceptance Criteria
- Unit: the double's timing honours its declaration; the driver's percentiles are computed over the
  samples it kept; the seeder is idempotent and its cleanup removes exactly what it made.
- **Given** the stack with no cloud adapter configured, **when** a profile runs at a concurrency the
  baseline sustains, **then** the gateway sustains it too, and the overhead figure is reported.
- **Given** a ramp, **when** any stated failure condition is met, **then** the run stops, names the
  concurrency and the condition, and the report says which component gave way.

## 11. Dependencies & Risks
- **The measurement machine is the stack's machine.** Eight cores carry Postgres, Kafka, Keycloak,
  the double *and* the driver, so an absolute throughput number here is about this laptop. The
  figures that travel are the per-core and per-request ones, and the run pins the gateway's CPU so
  that they mean something.
- A double that is too cheap flatters the gateway (no upstream backpressure); one that is too
  expensive measures itself. Both are answered by FR-4.

## 12. Rollout / Demo
`make loadtest` brings up the double, seeds, runs the profiles and writes a report. Nothing in the
demo or the product depends on it.

---

## 13. What was measured (2026-09-20)

Eight cores, the whole stack on one machine, the double and the driver included. **An absolute
throughput figure here is about this laptop**; the figures that travel are per request and per
core. Both full runs are in [`docs/measurements/`](../measurements/): the gateway as the image
ships it ([one worker](../measurements/2026-09-20-capacity-one-worker.md)) and the same load with
[four](../measurements/2026-09-20-capacity-four-workers.md).

The baseline behaved throughout: the same 300 callers driven straight at the double held
p50 2 800 ms / p99 2 823 ms with 5.6 ms of overhead, so nothing below is a statement about the
driver.

### 13.1 What one request costs

Marginal CPU, fitted across two concurrencies of the same profile so that the gateway's idle work
is not charged to the traffic:

| Workload | CPU per request | What it is |
| --- | --- | --- |
| chat with a RAG | **48 ms** | 4 000-token prompt, 220-token streamed answer, ~110 SSE events |
| agentic coding | **93 ms** | 6 000–12 000-token prompt, tools, thinking, ~390 SSE events |
| embedding batch | **75 ms** per 128 texts (**0.59 ms** per text) | no payload storage |
| — background — | **0.16–0.23 cores**, per worker, with no traffic at all | the anomaly tick, the upstream probe, the audit writer |

**The cost is driven by streamed events, not by requests.** The same chat workload from a runtime
that packs eight tokens into an SSE event instead of two cost **91 ms instead of 131 ms** at equal
concurrency — 31% less for a quarter of the events. How the upstream frames its stream is
therefore a first-order capacity lever, and not one this gateway controls.

### 13.2 Where it gives way, and it is not one place

**One uvicorn worker is one core**, and the image ships with one. Throughput held at 36–43 rps
across 16, 48 and 96 callers while the container's `cpu.stat` sat at 0.93–0.97 cores; two workers
gave 80 rps at 1.87 cores and four gave 125 rps at 3.39, with the per-request cost unchanged. It
scales linearly, and it does not scale at all without being told to.

**A single event loop degrades well before its core is full.** `chat` at 300 callers used 0.66
cores and still moved the median overhead from 74 ms to 763 ms and the 99th percentile to 16.5 s.
The per-request work arrives in an unpreemptible burst at the start of each request, so time to
first token is what suffers first. The practical rule: **size for about half a core of request
work per worker**, not for a full one.

**At 100 requests in flight per worker, the connection pool is the ceiling — at a fifth of a
core.** Every upstream adapter builds `httpx.AsyncClient(...)` without `limits=`, so httpx's
default applies: 100 connections, 20 of them kept alive. With the double made to take 15 s per
answer, one worker plateaued at **5.8 requests a second — the pool's 100/15 — while using 0.21
cores**, and at 240 callers the median doubled to 31 s because a request waited a whole slot for a
connection. This is the ceiling that matters for agentic work: answers are long, so concurrency is
high and per-request CPU is beside the point. The other 80 connections are re-established per
request, which here costs a TCP handshake and against a real cloud endpoint a TLS one.

**The audit queue overflows before anything refuses.** `agentic` at 300 callers on one worker
logged `request_log_queue_full` **42 times** in a minute: the 512-entry queue saturated and rows
were written on the request path, which is the designed backpressure (`FRD-405` §4.4) and is also
the first visible sign that the instance is past its capacity. There is **no metric for it** — it
exists only as a log line, which §9 records as a gap rather than papering over.

**Memory is not a constraint.** 300 concurrent agentic streams held the gateway at **152 MB**.

**Nothing refused.** Across every step, no request was rejected for load; the gateway queues and
slows instead. An instance past its capacity looks like a slow model, which is precisely the
confusion this FRD was written to remove.

### 13.3 The capacity statement

Per simulated person, measured: an agentic seat issues **0.066 requests a second** (a 12 s answer
plus 2–8 s of thinking, 60% of turns ending in a tool call), a chat seat **0.044**.

| The owner's peak | Requests/s | Gateway CPU for the traffic |
| --- | --- | --- |
| 300 agentic seats | 19.7 | **1.83 cores** |
| 300 chat seats | 13.3 | **0.64 cores** |
| both at once | 33 | **2.5 cores**, plus background per worker |

Measured against that arithmetic: four workers carried 300 agentic seats at 2.72 cores — and were
already degrading, at 68% of the four available. **Six gateway cores** carry the stated peak with
the headroom the tail behaviour in §13.2 requires; one worker carries about **50 agentic seats**,
and was measurably past its limit at 150.

### 13.4 The audit trail is the volume that grows

Measured per row, against prompts with the entropy of prose:

| Workload | Bytes per audit row |
| --- | --- |
| chat with a RAG | **17.7 kB** |
| agentic coding | **28.3 kB** |
| agentic, reasoning returned | **32.5 kB** |

At the owner's peak that is **48 GB a day** from the agentic seats and **20 GB** from the chat
seats. Payloads are pruned per use case (`retention_days`, 7 by default) but **rows are never
deleted** — `AIRA_LOG_RETENTION_DAYS` defaults to 0 and means *never*, deliberately, so the spend
history survives. A volume sized for this workload needs roughly **half a terabyte** of payload
plus a row count that grows without bound.

This figure was wrong by 14× on its first measurement, and the reason is recorded in
`tools/loadtest/driver.py`: the synthetic prompt was twelve words repeated, and Postgres compressed
it about fifty to one. A load harness that generates unrealistically uniform text measures the
compressor.

### 13.5 Two things found that are not capacity

- **A rate limit counts weight, so its unit is items and not calls.** A batch weighs its own size
  (`FRD-405` §4.2), so an embedding use case allowed 4 800 requests a minute gets 4.7 batches of
  128, and a burst below the batch size refuses that request outright however long the caller
  waits. 99.2% of a nightly-batch step came back `RESOURCE_EXHAUSTED` with a per-minute figure that
  looked generous. The refusal says which of the two it is, which is the only reason this took
  minutes to diagnose.
- **The `budgets` and `rate_limits` identity sequences sit behind their rows**, because the Kafka
  consumer writes both with explicit ids. Any insert that lets the sequence choose collides, and
  the message names a primary key rather than the cause. `tools/loadtest/seed.py` realigns them;
  the consumer is the authority and this is a note, not a fix.

### 13.6 What the answer to the owner's question is

Under the traffic described, **the gateway is not the bottleneck — provided it is given more than
one process.** As the image ships it, one worker is one core and is past its limit at about 50
agentic seats; that is a deployment decision, not a defect in the request path, and the per-request
cost does not change when workers are added. The three things to decide before this runs for real
are in §13.2 and §13.4: the worker or instance count, the upstream connection limit, and the volume
the audit trail will fill.
