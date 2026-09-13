# FRD-621 — Splunk HEC on either channel

> Phase: 1 (observability) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner asked whether the delivery channel can send plain JSON. It can — OTLP/JSON with
> the compression off — but OTLP/JSON is one nested document per batch, not one event per request,
> and the destination in question was Splunk. The owner asked for the fragment that speaks Splunk's
> own protocol.
> Related: [`FRD-618`](FRD-618-any-otlp-consumer.md) (the seven axes of an OTLP consumer),
> [`FRD-620`](FRD-620-both-channels-configured-the-same-way.md) (why every knob exists on both
> channels), [`FRD-617`](FRD-617-watching-the-wire-to-another-system.md) (the inspector this was
> driven against), [`FRD-616`](FRD-616-the-audit-trail-as-an-event-stream.md) (what the delivery
> channel carries).

## 1. Summary

Both telemetry channels could speak OTLP only, over HTTP or gRPC, as JSON or protobuf. OTLP/JSON
nests every record three levels deep — resource, scope, record — and writes each attribute as a
`{"key": …, "value": {"stringValue": …}}` pair. Splunk Enterprise and Splunk Cloud Platform take
events over the HTTP Event Collector (HEC) instead: one JSON event per record, sent with a token. To
reach Splunk, an installation needed a second collector of its own in between.

Now each channel has a third transport fragment that uses the collector's `splunk_hec` exporter.
Selecting it changes which exporters the channel's pipelines use and nothing else.

## 2. Goals & Non-Goals

- **Goals**: Splunk as a destination on either channel, configured the same way on both, behind the
  same filter and batching as the OTLP legs. The leg can be checked without a Splunk.
- **Non-Goals**: a Splunk app, dashboards or `props.conf` shipped by this repository. Splunk
  Observability Cloud, which takes OTLP and needs no fragment. Indexer acknowledgement.

## 3. User Stories

- As **IT Security**, I want the request records in Splunk as one event per request so that a
  search reads them without unpacking OTLP.
- As an **operator** whose trace backend is Splunk, I want the observability channel to reach it
  the same way the delivery channel does.

## 4. Functional Requirements

- **FR-1** `…/forward-splunk-hec.yaml` and `…/backend-splunk-hec.yaml`, selected by
  `AIRA_OTEL_FORWARD_PROTOCOL_CONFIG` and `AIRA_OTEL_BACKEND_PROTOCOL_CONFIG` like the gRPC and HTTP
  fragments.
- **FR-2** Only the exporters change. The delivery fragment moves `traces/siem`, `logs/siem` and
  `metrics/siem` and no base pipeline; the observability fragment keeps `debug` and `file/arrived`
  in its complete lists, because a merged list replaces.
- **FR-3** Metrics have an exporter of their own with `…_HEC_METRICS_INDEX`, because `mstats` reads
  a metrics index and nothing else and one index cannot be both kinds. The two exporters differ in
  the index and in nothing else.
- **FR-4** Five variables per channel, under the same names one prefix apart: `_HEC_ENDPOINT`,
  `_HEC_TOKEN`, `_HEC_INDEX`, `_HEC_METRICS_INDEX`, `_HEC_SOURCETYPE`. `FRD-620`'s symmetry test
  holds unchanged.
- **FR-5** A forgotten endpoint or token leaves the collector running. The endpoint falls back to a
  `.invalid` name (RFC 2606) and the token to the name of its variable, both spelled in Compose,
  because Compose passes an empty string for an unset variable and that overrides a default
  spelled in the fragment.
- **FR-6** HEC's token is the credential, so no `…_AUTH_CONFIG` fragment applies. `_ENCODING` and
  `_COMPRESSION` do not reach the leg: HEC is JSON, and the exporter gzips. TLS, queue and retry use
  the channel's existing variables.
- **FR-7** `make otlp-inspector` accepts HEC at `/services/collector` and
  `/services/collector/event`. It counts events, shows them as a list and answers with HEC's success
  body.

## 5. Design & Architecture

The merge order is unchanged: base, the observability channel's transport, credential and off
switch, then the delivery channel's switch, transport and credential. A transport fragment defines
its exporters and names pipelines. It is merged only when selected, so the exporters it defines are
validated only then.

The metrics exporter is a YAML merge (`<<: *hec`) of the events exporter with a different `index`.
`otelcol print-config` shows both expanded, with separate indexes.

The `splunk_hec` exporter sends one event per span, log record or metric data point. It gzips each
batch and posts it to the endpoint with `Authorization: Splunk <token>`. `source` is `aira`, and
`sourcetype` is `aira:otel` unless configured otherwise.

## 6. Data Model

None.

## 7. API / Interface Contract

Environment variables: `docs/CONFIGURATION.md`, both channel tables. What Splunk has to provide:
`docs/INTEGRATIONS.md` §6, "Splunk, over its HTTP Event Collector".

## 8. Security & Privacy

The token is a secret like the other channel credentials: it comes from the environment and is never
in a committed file. `otelcol print-config` redacts it. The inspector reports the
`Authorization: Splunk …` header by scheme and length and never shows its value (`FRD-617`).

## 9. Observability

`make otel-status` reports the HEC exporters by name, like any other exporter. A forgotten endpoint
shows there as failures against the `.invalid` name.

## 10. Testing & Acceptance Criteria

- `tools/tests/test_splunk_hec_is_a_transport_on_either_channel.py`:
  - the pipelines each fragment moves, with exact lists;
  - the metrics exporter differs only in its index;
  - each fragment reads only its own channel's variables;
  - the token, TLS, queue and retry;
  - the Compose fallbacks.
- `tools/tests/test_the_inspector_shows_what_was_forwarded.py`: HEC bodies are counted by event and
  answered as HEC answers.
- Mutations HEC1–HEC5 in `tools/mutation_check.py`.
- `otelcol validate` (collector-contrib 0.157.0), `rc=0` on every combination:
  - both channels on HEC;
  - the delivery channel alone;
  - HEC on the observability channel with `nobackend.yaml` outranking it;
  - HEC with a credential fragment selected beside it.
- Live, against the inspector standing in for Splunk: see the DEVLOG entry of 2026-09-13.

## 11. Dependencies & Risks

- The event shape is the exporter's, and it can change between collector releases. The shape shown
  in `INTEGRATIONS.md` was taken from 0.157.0.
- Spans and logs share one index. Splunk's own collector distribution offers a separate traces
  index; the delivery channel's traces are the request records, so one events index is the useful
  default there.

## 12. Rollout / Demo

Off unless selected. To try it without a Splunk, run `make otlp-inspector` and point
`AIRA_OTEL_FORWARD_HEC_ENDPOINT` at `http://otlp-inspector:4318/services/collector`.
