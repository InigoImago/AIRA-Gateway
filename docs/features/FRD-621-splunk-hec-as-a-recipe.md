# FRD-621 — Splunk HEC, as a recipe

> Phase: 1 (observability) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner asked whether the delivery channel can send plain JSON. It can: OTLP/JSON with
> the compression off. But OTLP/JSON is one nested document per batch, not one event per request,
> and the destination in question was Splunk, so the owner asked for the fragment that speaks
> Splunk's own protocol. It first shipped as a transport on both channels with ten product
> variables. [`ADR-0024`](../adr/ADR-0024-the-collector-has-a-core-and-recipes.md) made it a recipe
> on the delivery channel the same day.
> Related: [`FRD-618`](FRD-618-any-otlp-consumer.md) (the axes of an OTLP consumer),
> [`FRD-620`](FRD-620-both-channels-configured-the-same-way.md) (the symmetric core),
> [`FRD-617`](FRD-617-watching-the-wire-to-another-system.md) (the inspector this was driven
> against), [`FRD-616`](FRD-616-the-audit-trail-as-an-event-stream.md) (what the delivery channel
> carries).

## 1. Summary

The delivery channel speaks OTLP. OTLP/JSON nests every record three levels deep, resource then
scope then record, and writes each attribute as a `{"key": …, "value": {"stringValue": …}}` pair.

Splunk Enterprise and Splunk Cloud Platform take events over the HTTP Event Collector (HEC)
instead: one JSON event per record, sent with a token.

`deploy/compose/otel/recipes/forward-splunk-hec.yaml` uses the collector's `splunk_hec` exporter to
send the delivery channel there.

## 2. Goals & Non-Goals

- **Goals:**
  - Splunk as the delivery channel's destination, behind the same filter and batching as the OTLP
    leg.
  - The leg can be checked without a Splunk.
- **Non-Goals:**
  - A Splunk app, dashboards, or a `props.conf` shipped by this repository.
  - Splunk Observability Cloud, which takes OTLP.
  - Indexer acknowledgement.
  - HEC on the observability channel as a shipped file. `recipes/README.md` says how to derive it.

## 3. User Stories

- As **IT Security**, I want the request records in Splunk as one event per request so that a
  search reads them without unpacking OTLP.

## 4. Functional Requirements

- **FR-1** A recipe selected by `AIRA_OTEL_FORWARD_PROTOCOL_CONFIG`, like the gRPC fragment.
- **FR-2** Only the exporters of `traces/siem`, `logs/siem` and `metrics/siem` change. No base
  pipeline is named.
- **FR-3** Metrics have an exporter of their own with `SPLUNK_HEC_METRICS_INDEX`, because `mstats`
  reads only a metrics index. The two exporters differ in the index and in nothing else.
- **FR-4** Its values come from the optional env file `deploy/compose/otel/custom/collector.env`:
  `SPLUNK_HEC_ENDPOINT`, `SPLUNK_HEC_TOKEN`, `SPLUNK_HEC_INDEX`, `SPLUNK_HEC_METRICS_INDEX`,
  `SPLUNK_HEC_SOURCETYPE`. They are not product settings (`ADR-0024`).
- **FR-5** With nothing filled in, the collector still starts:
  - the endpoint falls back to a `.invalid` name;
  - the token falls back to the name of its variable.
- **FR-6** HEC's token is the credential, so no `…_AUTH_CONFIG` fragment applies. `_ENCODING` and
  `_COMPRESSION` do not reach the leg: HEC is JSON, and the exporter gzips. TLS, queue and retry use
  the channel's core variables.
- **FR-7** `make otlp-inspector` accepts HEC at `/services/collector` and
  `/services/collector/event`. It counts events, shows them as a list, and answers with HEC's
  success body.

## 5. Design & Architecture

**One event per record.** The `splunk_hec` exporter sends one event per span, log record or metric
data point:
- span attributes are a flat object in `event.attributes`;
- resource attributes are in `fields`, which HEC indexes;
- each batch is gzipped and posted with `Authorization: Splunk <token>`;
- `source` is `aira`, and `sourcetype` defaults to `aira:otel`.

**Two exporters.** The metrics exporter is a YAML merge (`<<: *hec`) of the events exporter, with a
different index.

## 6. Data Model

None.

## 7. API / Interface Contract

The recipe's values are listed in `deploy/compose/otel/recipes/collector.env.example`. What Splunk
has to provide is in `docs/INTEGRATIONS.md` §6, under "Splunk, over its HTTP Event Collector".

## 8. Security & Privacy

The token lives in the git-ignored env file, never in a committed file. `otelcol print-config`
redacts it. The inspector reports the header by scheme and length and never shows its value
(`FRD-617`).

## 9. Observability

`make otel-status` reports the HEC exporters by name. A forgotten endpoint shows there as failures
against the `.invalid` name.

## 10. Testing & Acceptance Criteria

- **`tools/tests/test_the_collector_has_a_core_and_recipes.py`** checks that:
  - the recipe moves all three delivery pipelines;
  - the metrics exporter differs only in its index;
  - every value has a fallback and is listed in the example;
  - it reads no `AIRA_` variable outside the core.
- **`tools/tests/test_the_inspector_shows_what_was_forwarded.py`** checks that HEC bodies are
  counted by event and answered as HEC answers.
- **`otelcol validate`** returns `rc=0` with and without values, and beside a header credential.
- **Live:** see the DEVLOG entries of 2026-09-13.

## 11. Dependencies & Risks

- **Collector releases:** the event shape is the exporter's, and it can change between releases.
  The shape in `INTEGRATIONS.md` was taken from collector-contrib 0.157.0.
- **One events index:** spans and logs share one index. The delivery channel's traces are the
  request records, so that is the useful default.

## 12. Rollout / Demo

Off unless selected. To try it without a Splunk:
1. Start the inspector with `make otlp-inspector`.
2. Set `SPLUNK_HEC_ENDPOINT=http://otlp-inspector:4318/services/collector` in
   `deploy/compose/otel/custom/collector.env`.
