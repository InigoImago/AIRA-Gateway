# Collector recipes

These are fragments for destinations the core variables do not cover (ADR-0024).

| recipe | select it in | for |
| --- | --- | --- |
| `forward-splunk-hec.yaml` | `AIRA_OTEL_FORWARD_PROTOCOL_CONFIG` | Splunk over HEC: one event per record |
| `forward-azure-monitor.yaml` | `AIRA_OTEL_FORWARD_PROTOCOL_CONFIG` | Azure Monitor and Sentinel: per-signal paths, protobuf, a managed identity |
| `forward-oauth2.yaml` | `AIRA_OTEL_FORWARD_AUTH_CONFIG` | OAuth2 client credentials, fetched and refreshed |

## How a recipe is set up

The directory is mounted read-only at `/etc/otelcol-contrib/recipes`. You select a recipe through a
slot that already exists:

    AIRA_OTEL_FORWARD_CONFIG=/etc/otelcol-contrib/forward.yaml
    AIRA_OTEL_FORWARD_PROTOCOL_CONFIG=/etc/otelcol-contrib/recipes/forward-splunk-hec.yaml

A recipe reads its own values from `../custom/collector.env`. That file is optional and git-ignored,
and `collector.env.example` lists every name the recipes read.

The names carry no `AIRA_` prefix, because they are not product settings: Compose passes the file
through without declaring the names. So an unset value is absent inside the collector, and the
recipe's own fallback applies.

## When a recipe does not fit

Use it as a starting point:
1. Copy it to `../custom/` and edit it.
2. Select it as `/etc/otelcol-contrib/custom/<file>.yaml`.

For the observability channel, change two things in the copy:
- Rename the exporters from `…/forward` to `…/backend`.
- Name the base pipelines `traces`, `metrics` and `logs`, and keep `debug` and `file/arrived` in
  each exporter list.

## Rules every recipe keeps

`tools/tests/test_the_collector_has_a_core_and_recipes.py` checks these:

- **Only its own pipelines.** A recipe names only its own channel's pipelines, with complete exporter
  lists, because a merged list replaces.
- **The whole extension list.** A recipe that brings an extension restates the whole
  `service::extensions` list, including the base's entries. Two such recipes on one collector
  become one file.
- **A fallback for every value.** Every value a recipe reads has a fallback that lets the collector
  start: an address that never resolves, or a secret that names its variable.

## Checking a combination before you use it

    docker compose -f deploy/compose/docker-compose.yml --profile observability run --rm --no-deps \
      otel-collector validate --config=/etc/otelcol-contrib/config.yaml \
      --config=/etc/otelcol-contrib/forward.yaml \
      --config=/etc/otelcol-contrib/recipes/forward-splunk-hec.yaml
