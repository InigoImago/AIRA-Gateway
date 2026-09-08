# FRD-620 — Both channels configured the same way

> Phase: 1 (observability) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner asked for the two OTLP channels to be checked — *set up correctly, working,
> and adjustable* — and reported the thing this document is about: **setting the second one up does
> not look like setting the first one up.** Then the specific ask, which turned out to be the sharp
> end of the same complaint: *the encoding should be adjustable on both channels — I want to be
> able to send raw JSON.*
> Related: [`FRD-618`](FRD-618-any-otlp-consumer.md) (the seven axes, and the pipelines split),
> [`FRD-616`](FRD-616-the-audit-trail-as-an-event-stream.md) (what the delivery channel carries),
> [`FRD-617`](FRD-617-watching-the-wire-to-another-system.md) (the inspector every claim below was
> driven against), [`FRD-001`](FRD-001-observability-baseline.md),
> [`ADR-0004`](../adr/ADR-0004-observability-grafana-otel-lgtm.md).

## 1. Problem

`FRD-618` §3.1b separated the two channels' **pipelines** — they had shared a batch processor and
two of the three signals had had no pipeline of their own — and §3.1c separated the observability
channel's **endpoint**, which had been the literal `otel-lgtm:4317`. Both were reported by the owner
in the same terms as this round: one channel was the product and the other an addition to it.

Neither round finished the job, and the reason both stopped where they did is the same: each fix
was checked against the file it changed, and the asymmetry is not visible in any single file. It is
only visible when the two families of variables are held side by side. Measured on 2026-09-07:

| | observability | delivery |
| --- | --- | --- |
| `--config` slots | **1** (off) | **3** (off · transport · credential) |
| variables | **4** | **17** |
| transport | OTLP/gRPC, fixed | HTTP or gRPC, a variable |
| **encoding** | **not expressible** | `json` · `proto` |
| compression | fixed `gzip` | four values |
| per-signal endpoints | no | yes |
| credential | **none, at all** | four fragments |
| client certificate | no | yes |
| batching | `batch: {}` — the collector's defaults, unreachable | two variables |
| queue, retry | unreachable | three variables |

So an installation with a trace backend behind **any** authentication — Grafana Cloud, Honeycomb,
Datadog, a Tempo behind an nginx — could not reach it without editing a file this repository ships.
That is precisely the complaint `FRD-618` §3.1c recorded about the endpoint, still open one layer
along, and `INTEGRATIONS.md` said so in as many words: *"One asymmetry is left, and it is named
rather than hidden."* Naming it was right; leaving it was the report this round answers.

### 1.1 The encoding could not have been a variable, and that is the interesting part

`AIRA_OTEL_BACKEND_ENCODING` was not an oversight in the way the credential was. **OTLP/gRPC is
protobuf by definition** — the specification defines no JSON over gRPC — so a channel with one
transport has one encoding, and a variable for it would have had nothing to set. The missing
transport was the missing encoding, and asking for JSON on that channel is asking for OTLP/HTTP on
it first.

That is worth writing down because it is the shape of the whole round: the four knobs the
observability channel had were not four of seventeen chosen for some reason. They were what is
reachable when a channel has one exporter, and every one of the other thirteen followed from
giving it a second.

## 2. What it costs to be missing

Two things, and the second is worse.

**A destination that cannot be reached.** OTLP/JSON is what to send a receiver that parses
documents rather than one that speaks OTLP — a datalake, an internal collector, anything with a
JSON ingest in front of it. On the delivery channel that has been one variable since `FRD-618`. On
the observability channel it was a wall, and the workaround is editing `collector-config.yaml`,
which is a file `git pull` overwrites.

**And a reader who concludes the second channel is second-class.** `CONFIGURATION.md` had a
twenty-eight-row table for one channel and a five-row table for the other, three paragraphs on how
to authenticate one and a paragraph explaining that the other could not be. Somebody planning a
deployment reads that as *this product has a trace backend and, if you insist, a SIEM feed* — which
is the opposite of what `FRD-618` decided and what the code now does.

## 3. Design

### 3.1 The same three slots, on both

A destination varies in three ways that do not depend on each other, and `FRD-618` had already
found the shape: three `--config` fragments, because YAML has no conditional and the alternative is
a block that is always present and sometimes empty. The observability channel gets the same three.

| slot | question |
| --- | --- |
| `…_CONFIG` | **whether**, and where — the channel's on/off switch |
| `…_PROTOCOL_CONFIG` | **how it is reached** — OTLP/HTTP or OTLP/gRPC |
| `…_AUTH_CONFIG` | **who we say we are** — nothing · header · basic · OAuth2 · platform identity |

Seven `--config` flags now, always: the base plus three per channel, each merging the empty
fragment until a variable names a real file. That empty fragment was called `noforward.yaml`, which
was accurate when one slot defaulted to it and became a small lie when six did; it is `empty.yaml`.

**Twenty-six variables each, and the two that differ do so under a rule.** `…_ENDPOINT` is the
channel's *default transport*, and the other transport gets its own name — so observability, which
defaults to gRPC, has `_ENDPOINT` and `_PLAINTEXT` for gRPC plus `_HTTP_ENDPOINT`; delivery, which
defaults to HTTP, has `_ENDPOINT` for HTTP plus `_GRPC_ENDPOINT` and `_GRPC_PLAINTEXT`. The rule
reads backwards between the two, which is why it is stated rather than left to be inferred, and why
`test_the_two_channels_take_the_same_settings` asserts the difference is exactly those two.

### 3.2 The encoding, which needed a transport first

`otlphttp/backend` joins `otlp/backend` in the base configuration, and
`collector-backend-http.yaml` — three pipelines, nothing else — swaps the base pipelines onto it.
Both exporters are defined always and the pipelines decide, which is `FRD-618` §3.1a's arrangement
for the same measured reason: an exporter is validated whether or not a pipeline references it, so
a fragment that *introduced* one would stop the collector on every deployment that had not selected
it, and a credential fragment has to attach `auth:` to both because it cannot know which transport
is in play.

`AIRA_OTEL_BACKEND_ENCODING` is then `json` or `proto`, and a third value stops the collector at
validation — deliberately, and this is `FRD-618` §3.3's reasoning unchanged: an endpoint can be
*forgotten* while somebody is configuring something else, and an encoding is one of two words typed
on purpose in the same minute as the endpoint it belongs to.

The HTTP leg's default is `http://otel-lgtm:4318` — the bundled Grafana's own HTTP port — so
switching the transport against the demo stack needs no second variable, the property
`otlp/backend`'s default already had.

### 3.2a And the off switch has to outrank the transport

`nobackend.yaml` strips the backend exporter out of the base pipelines; `backend-http.yaml` puts a
different one in. **A merged pipeline replaces**, so of two fragments naming the same pipeline the
later wins — which makes the order of the `--config` flags a decision rather than a detail.

`_PROTOCOL_CONFIG` is merged first and `_CONFIG` last, so a channel that is switched off stays off
however its transport is set. The other order makes a stale `_PROTOCOL_CONFIG` quietly turn a
disabled channel back on, and a channel that is off is not a thing anybody re-checks.

(The delivery channel's pair is the other way round and has to be: its transport fragment moves
pipelines that `forward.yaml` *defines*, so selecting it alone names an exporter that does not
exist and stops the collector. Loud, and therefore the acceptable half of the pair.)

### 3.3 A credential — and the list that had room for exactly one

The four credential shapes are `backend-auth-header`, `-basic`, `-oauth2` and `-azure-identity`:
the delivery channel's four, generic first, under the same names one prefix along. Basic is not
filler — it is how **Grafana Cloud** takes OTLP, an instance id and an access policy token, which
makes the hosted version of the product this stack bundles the first destination this closes.

Building them found the defect that makes this section worth reading.

**`service::extensions` is a list, and a merged list replaces.** Every credential fragment carried
its own — `extensions: [oauth2client]` — with a comment explaining that the base names none, so one
entry is the whole list. True while there was one credential slot in the stack. With one slot per
channel it means that **selecting a credential on each channel starts only the second one**.
Measured on 2026-09-07 against collector-contrib 0.157 with an extension in each of two fragments:

```
Extension is starting...  {"otelcol.component.id": "headers_setter/b"}
Everything is ready.
```

One line, for the second fragment. No error, no warning, and `otelcol validate` answering `rc=0`
throughout — because a configured-but-unstarted extension is a perfectly valid configuration. The
symptom would have been a `401` from one of two receivers, which reads as a *wrong* credential
rather than an absent one and sends the reader to check the value they got right.

So the base configuration declares **all eight** authenticators and lists them once, and a fragment
is three lines attaching `auth:` to its own two exporters. No fragment names `service::extensions`,
a guard test fails on any that does, and the hazard is gone by construction rather than by everyone
remembering.

### 3.3a Declaring them always meant they had to survive being unconfigured

Three of the eight refuse to be built empty. Measured, one at a time:

```
headers_setter   missing header source, must be 'from_context', 'from_attribute', 'value', …
oauth2client     no TokenURL / no ClientID / no ClientSecret provided
```

An extension is validated whether or not an exporter references it, so with bare `${env:…}` the
collector would refuse to start **on every deployment**, not just one that selected the fragment.

Each therefore falls back to **the name of the variable that is missing**:
`AIRA_OTEL_FORWARD_AUTHORIZATION-is-not-set`, `https://forward-oauth-token-url-is-not-set.invalid/token`.
The same trick as `forward-endpoint-not-set.invalid`, one layer along. Nothing is configured,
everything starts, and a fragment selected without its values reaches the destination as a
credential that says which variable to set — legible in `make otlp-inspector` and quoted back in
the far end's own rejection.

**That is strictly better than what it replaces, and what it replaces was documented wrong.**
`collector-forward-auth-header.yaml` said, in its own comment:

> Selecting this fragment and leaving the value empty sends an empty header. … Nothing here can
> tell an empty credential from an unset one — `make otlp-inspector` can, and shows it as `(empty)`.

Neither sentence was true. Measured on 2026-09-07 with `AIRA_OTEL_FORWARD_AUTHORIZATION=`: the
collector **refused to start**, and one container carries every exporter, so Grafana went with it.
Nothing could send an empty header because nothing got as far as sending. `forward-auth-oauth2.yaml`
had the same outcome with an empty client id and did not document it at all.

The paragraph had described the *previous* defect's behaviour — `FRD-618`'s always-present
`headers:` block, which really did send `authorization: ''` — and outlived the fix. It is the shape
`LESSONS.md` §5 lists: a rule the project states and does not have, in the file a person reads
while wiring the thing up.

### 3.3b The trap this round walked into, having been warned in writing

The placeholders went into `collector-config.yaml` first, where the values are. Result:

```
=== 220 configurations validated, 220 failed
```

**Compose passes an empty string for an unset variable, and an empty string overrides a
`${env:…:-default}` inside the collector.** `collector-forward.yaml` has said so since `FRD-618`,
about the endpoint, in a paragraph headed *"That computation is Compose's, not this file's"*. The
fallback has to be spelled at the Compose end or it never applies. Moved there: 220 of 220 pass.

Recorded rather than smoothed over, because the interesting part is not the mistake. It is that the
project had already paid for this exact lesson, written it down twice, and it still cost a round —
which is an argument for the test that now asserts it (`test_the_credential_fallbacks_are_spelled_where_they_apply`)
rather than for reading more carefully next time.

### 3.4 The rest of the axes

Compression (`gzip` · `none` · `zstd` · `snappy`), a private CA, a **client certificate** for mutual
TLS, per-signal endpoints computed by Compose, and the batching, queue and retry figures — all under
the delivery channel's names one prefix along.

The tuning defaults are **the collector's own** (200ms, 8192, 10 consumers, 1000 queued, 5s), so
the shipped stack behaves exactly as before. That is the opposite choice from the delivery channel,
whose defaults are deliberately polite because its destination is usually one somebody is trying for
the first time; this channel's destination has been receiving all along.

## 4. Functional requirements

- **FR-1** Both channels are reachable over **either** OTLP transport, chosen by configuration.
- **FR-2** The wire encoding is `json` or `proto` on **both** channels, chosen by configuration —
  over HTTP, since OTLP/gRPC defines no JSON.
- **FR-3** Both channels can send a credential, in any of four shapes, under any header name.
- **FR-4** **Both at once**: a credential selected on each channel authenticates each channel.
- **FR-5** Every setting one channel has, the other has under the same name one prefix along, but
  for the two that name the transport a channel does not default to.
- **FR-6** Compression, a private CA, a client certificate, per-signal endpoints, batching, queue
  and retry are configurable on both.
- **FR-7** Switching a channel off outranks its transport fragment.
- **FR-8** A credential fragment selected without its values leaves the collector running and sends
  a value naming the variable that is missing.
- **FR-9** Every state above leaves the *other* channel exactly as it was — its exporter, its
  encoding, its credential and its batch window.

## 5. Testing

`tools/tests/test_both_channels_are_configured_the_same_way.py` — **68 cases**. The one that
carries the round is `test_the_two_channels_take_the_same_settings`: it subtracts one family of
variable names from the other and requires the remainder to be the two transport-specific names.
That comparison is the test because that comparison is what nobody made for two rounds — each half
was correct on its own.

The rest: three slots per channel, the merge order that makes *off* outrank a transport, no
fragment owning `service::extensions`, every authenticator a fragment names being declared **and
started** in the base, each auth fragment covering both of its own channel's exporters and neither
of the other's, no header keyed by a variable, the fallbacks spelled where an empty string cannot
override them, both backend transports carrying TLS and compression and a queue, the encoding being
a variable, the two batch processors, and the transport fragment moving pipelines and nothing else.

`test_the_siem_gets_requests_not_plumbing.py` keeps its 47 and one is re-aimed at the base
configuration, which is where a credential fragment's extension now lives.

**Ten mutations** (`CHAN1`–`CHAN10`), each caught. `ID41` went **stale** rather than surviving —
its anchor was `extensions: [oauth2client]` in a fragment that no longer owns that list — and was
re-aimed at the base configuration rather than deleted: the property it guards is unchanged.

## 6. Demonstrated, not asserted

Every line below is from the running stack on 2026-09-07, driven end to end against receivers that
print what they got — never from `validate` alone, which answered `rc=0` while only one of two
extensions was starting.

**All 220 merged configurations validate** — two backend transports × five backend credential
states × the off switch × three delivery states × five delivery credential states — against
`otel/opentelemetry-collector-contrib:0.157.0`.

**Raw JSON on the observability channel**, which is the thing that was asked for and was not
possible. Channel 1 pointed at one receiver and channel 2 at another, at the same time:

```
channel 1  (observability)   application/json        identity   x-backend-key: (opaque, 26 chars)
channel 2  (delivery)        application/x-protobuf  gzip       x-delivery-key: (opaque, 27 chars)
```

and the body on channel 1 is real OTLP/JSON, not something shaped like it:

```json
{"resourceMetrics": [{"resource": {"attributes": [
  {"key": "telemetry.sdk.language", "value": {"stringValue": "python"}}, …
```

**Two encodings, two credentials, two header names, two receivers, at once.** Both channels'
authenticators started — the state that used to be silently impossible:

```
Extension is starting...  headers_setter/backend      basicauth/backend      oauth2client/backend
Extension is starting...  headers_setter/forward      basicauth/forward      oauth2client/forward
Extension is starting...  azure_auth/backend          azure_auth/forward
```

**The encoding variable moves the wire**, and so do the others. Flipping channel 1 to `proto` with
`zstd` and basic auth, without touching channel 2:

```
application/x-protobuf   zstd   authorization: Basic (26 chars)
```

— and the password nowhere on the page.

**Independent delivery**, from the collector's own counters:

```
otelcol_exporter_sent_spans{exporter="otlphttp/backend", server_address="otlp-inspector-2"}  8
otelcol_exporter_sent_metric_points{exporter="otlphttp/forward", server_address="otlp-inspector"}  4
processor="batch"   processor="batch/siem"
```

**And the off switch outranks the transport.** Both `nobackend.yaml` and `backend-http.yaml`
selected:

```
otelcol_exporter_sent_metric_points{exporter="debug"}             4
otelcol_exporter_sent_metric_points{exporter="file/arrived"}      4
otelcol_exporter_sent_metric_points{exporter="otlphttp/forward"}  4
```

No `*/backend` exporter at all — the channel is off, the two arrivals exporters keep receiving, and
the delivery channel is untouched.

## 7. What this does not do

**It does not give the observability channel a filter.** It carries everything, deliberately —
that is the difference between the two channels and the reason there are two. `filter/siem` stays
the delivery channel's.

**It does not make either queue survive a restart.** The sending queue is in memory on both
channels now rather than on one, which is more of the same limitation rather than a new one:
`file_storage` is in this image, needs a writable volume and a size policy, and is `FRD-616` §6's.

**It does not change what either channel receives.** Every default is what the stack was already
doing — the gRPC transport, the bundled backend, the collector's own batching figures. A stack
that sets none of the new variables is byte-for-byte the stack from before.
