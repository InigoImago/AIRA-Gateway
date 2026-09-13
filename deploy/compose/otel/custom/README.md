# An installation's own collector configuration

Everything in this directory except this file is git-ignored. The directory is mounted read-only at
`/etc/otelcol-contrib/custom` (ADR-0024). It holds two kinds of file:

- **`collector.env`** holds the values for the recipes in `../recipes/`. Copy
  `../recipes/collector.env.example` to start. Compose passes this file to the collector only, and
  only if it exists.
- **`*.yaml`** are fragments of your own, often a recipe copied and edited. You select one through a
  slot:

      AIRA_OTEL_FORWARD_PROTOCOL_CONFIG=/etc/otelcol-contrib/custom/my-destination.yaml

The rules in `../recipes/README.md` apply to these fragments too.
