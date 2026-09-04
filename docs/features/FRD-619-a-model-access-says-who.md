# FRD-619 — A model access that says who it was for

> Phase: 1 (observability) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner read the **raw** payload of the delivery channel in the OTLP inspector and
> asked where, in it, the *model* accesses were — and *"the description and metadata about who
> accessed what, when."* The API accesses were fully described. The model accesses were in there,
> and they were anonymous.
> Related: [`FRD-618`](FRD-618-any-otlp-consumer.md) (the channel this is delivered on),
> [`FRD-617`](FRD-617-watching-the-wire-to-another-system.md) (the inspector the question came
> from), [`FRD-615`](FRD-615-a-trace-crosses-the-bus.md) (names, never content),
> [`FRD-117`](FRD-117-diagnostics-and-compatibility.md) (why the client span exists at all),
> [`FRD-105`](FRD-105-tracing-and-ip.md) (the one place that knows who is calling).

## 1. Problem

The delivery channel forwards **two** records per served request, and it is deliberate: the filter
in `deploy/compose/otel/collector-forward.yaml` keeps a root span that carries `aira.use_case` (the
API access) *and* keeps its children that carry a URL (the model access). `collector-nobackend.yaml`
states the promise in as many words — *"the access records — one per API call and per model call"*.

Measured on **2026-09-04**, against the live feed, from the inspector's raw view:

| | scope | attributes |
| --- | --- | --- |
| API access | `…instrumentation.fastapi` | **29** — `aira.subject`, `aira.use_case`, `aira.credential`, `aira.auth_method`, `aira.source_ip`, `aira.model`, `aira.operation`, `aira.api.surface`, `aira.outcome`, `aira.status`, `aira.total_tokens`, `aira.cost_nanos`, `aira.upstream.*`, `aira.pipeline.*`, `http.*` |
| model access | `…instrumentation.httpx` | **3** — `http.method`, `http.status_code`, `http.url` |

Three attributes, and the URL was `http://ollama:11434/v1/chat/completions` — which does not name
the model either, because an OpenAI-compatible upstream carries the model in the request **body**.
So the record said: *somebody POSTed to a host, and got a 200.*

### 1.1 Why this was invisible until somebody read the raw payload

The two spans share a trace. `parentSpanId` on the model access points at the API access, and a
**tracing backend joins them** — open the trace in Grafana and every one of those 29 attributes is
one click above the model call. Nothing was missing from the *trace*.

The second destination is not a tracing backend. A SIEM ingests flat events and correlates by
**field**: it filters `aira.use_case = kundenservice`, it groups by `aira.subject`, it alerts on a
model. A join across a parent pointer is not an operation it performs on ingest, and every question
anybody would ask a model-access record needed exactly that join.

This is the shape [`LESSONS.md`](../LESSONS.md) §1 keeps naming from a new angle: **a guarantee
that holds on one consumer and not another is a guarantee stated without its consumer.** *"One
record per model call"* was true. *"A record you can use"* was not, and only the raw view showed
the difference — the inspector's earlier tabular rendering would have shown three tidy columns and
looked fine.

## 2. Scope

**In.** The caller's identity and the model's name on the span of the upstream call itself, for
every path that calls a model: dispatch (per attempt), both streaming surfaces, both embedding
surfaces, the pipeline classifiers, and the incident probe. A declared reason for the call.

**Out.** Anything about *content*. `FRD-615`'s rule is unchanged and load-bearing here: a span
carries **names, never arguments** — no prompt, no completion, no tool argument. What is added is
five identifiers and a purpose.

**Out.** Duplicating the API access. Token counts, cost, outcome and the pipeline decisions stay on
the request span, which is the record that owns them; a model access is not the place to restate
what an API access already says.

## 3. Requirements

- **FR-1** A model call's client span carries `aira.subject`, `aira.use_case`, `aira.credential`
  and `aira.auth_method` — the four facts `FRD-105` puts on the request span.
- **FR-2** It carries `aira.model`, resolved, because the URL does not name it on every dialect.
- **FR-3** It carries `aira.model_call.purpose` from a **closed** set — `serve`, `pipeline`,
  `embed`, `probe` — so *"what share of this is the pipeline"* is a grouping rather than a guess.
- **FR-4** A fallback chain marks **each attempt**, naming the model that attempt tried.
- **FR-5** An outgoing call the gateway makes for its own reasons — a JWKS refresh, a Vault read, a
  reachability check — carries **none** of this, and stays the three HTTP attributes it was.
- **FR-6** A stream is marked for as long as it streams, and not afterwards.
- **FR-7** An upstream call added later without a mark is a **test failure**.

## 4. Design

### 4.1 Two context variables, because they have different lifetimes

`aira_common.observability` holds both, and the split is the design:

- `_caller` — set **once**, by `gateway.auth.attribution.set_attribution`. That function is
  already the single owner of *"a fact about who is calling reaches the span"*, already extracted
  for exactly this reason (`FRD-105`), and already guarded by
  `test_every_attribution_reaches_the_span.py`. Deriving the identity a second time somewhere
  nearer the call is the mistake this project has now paid for twice.
- `_model_call` — set **only around an actual upstream model call**, by `telemetry.model_call_span`.
  Its presence is what tells the httpx hook that this outgoing request is a model access.

FR-5 falls out of the second variable rather than being enforced separately: outside a marked
block, `model_call_attributes()` returns `None` and the hook does nothing. That matters more than
tidiness — a JWKS refresh labelled with a subject is not merely noisy, it is a record a SIEM would
**count as a model access**.

### 4.2 The mark is a block, per attempt

`dispatch_with_fallback` enters `model_call_span(model, purpose="serve")` inside its candidate
loop, so a chain that tried three models leaves three records naming three models. Marking the
*request* instead would put the model that eventually answered on all three — and in an incident
the interesting record is usually an attempt that **failed**: *which model was down at 14:02* is
the question, and it is the one reading nobody could contradict from the record itself.

### 4.3 Streams are wrapped, not enclosed

A streaming upstream issues its HTTP request on the first `__anext__`, not when the iterator is
built, so the mark has to be in force for the *iteration*. `telemetry.model_call_chunks` wraps the
iterator; the surfaces' long loop bodies are untouched. An async generator does not get a context
of its own in CPython, so what it sets is what the surface's loop sees, and it is released when the
stream is exhausted or closed (FR-6).

### 4.4 Seven call sites, and why they are not one

`FRD-126` extracted the pre-dispatch sequence into `prepare_for_dispatch` precisely so that a
surface could not assemble it wrong, and the instinct here is the same. It does not apply: dispatch
needs the mark **per attempt**, a stream needs it **around the iteration**, a classifier has a
**different purpose**, and the adapters — the only layer all seven pass through — know the model
but never the reason, which is the field FR-3 exists to make groupable.

So the sites stay, and `test_every_model_call_says_what_it_is.py` fails on an eighth that is not
marked. Seven sites and a convention is six sites and a convention.

The `upstreams/` package is exempt, stated rather than assumed: it is the **callee**. What matches
in there is an adapter reaching its own helper or its own method, inside a block a caller has
already marked, and marking it again would report one call as two model accesses.

## 5. What a consumer receives now

One flat record per model call, self-describing:

```json
{
  "aira.subject": "admin",       "aira.use_case": "personalwesen",
  "aira.credential": "888a5742", "aira.auth_method": "api_key",
  "aira.model": "qwen3:0.6b",    "aira.model_call.purpose": "serve",
  "http.method": "POST", "http.status_code": 200,
  "http.url": "http://ollama:11434/v1/chat/completions"
}
```

*when* is `startTimeUnixNano` and `endTimeUnixNano`, which the span always had; the duration
between them is how long the model took, which is what the client span was added for (`FRD-117`).

## 6. Verification

- `gateway/tests/test_outgoing_calls_are_traced.py` — the spans, asked rather than the wiring: the
  identity arrives, a plain outgoing call gains nothing, each attempt names its own model, a stream
  is marked while it streams and not after, an undeclared purpose is refused.
- `gateway/tests/test_every_attribution_reaches_the_span.py` — the **wiring**, through a real
  request: asserting the carrier by calling it directly passes with the one line that feeds it
  deleted, which is what the mutation harness demonstrated.
- `gateway/tests/test_pipeline_dispatch.py` — the chain marks the model it tried.
- `gateway/tests/test_every_model_call_says_what_it_is.py` — FR-7.
- `tools/mutation_check.py` `MC1`–`MC8` — eight properties, each verified to be caught.
- **Live**, 2026-09-04 13:20 CEST: driven through `make showcase` with forwarding on, read back
  from the inspector's raw view. Before the change, three attributes; after, the nine above.

## 7. Status

Built. The live measurement covers `serve`; `pipeline`, `embed` and `probe` are covered
hermetically and by the source guard — the demo installation's daily budget for the use case that
runs a pipeline was exhausted at the time, which is a state of the demo rather than of the feature.
