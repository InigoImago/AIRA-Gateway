# ADR-0024 — The collector has a small core of variables; everything else is a recipe

- **Status:** Accepted
- **Date:** 2026-09-13
- **Deciders:** project owner

## Context
The OpenTelemetry Collector is configured through `AIRA_OTEL_*` environment variables. They select
`--config` fragments, because Compose has no conditionals. Every destination attached so far added
variables:
- per-signal endpoints for a receiver with a route in front of OTLP (`FRD-618`);
- HTTP basic, OAuth2 and platform-identity credentials (`FRD-618`);
- all of those again on the observability channel (`FRD-620`), whose symmetry test makes every knob
  exist twice;
- Splunk HEC (`FRD-621`).

Within a week the channel variables declared in Compose went from 32 to 66, and the fragments from 9
to 16. Each variable was spelled in six places:
- in Compose, with a fallback that must live there, because Compose passes an empty string for an
  unset variable;
- in three guard lists;
- in `CONFIGURATION.md`;
- in `.env.example`.

The fragments carried 871 comment lines on 349 lines of configuration. The owner asked why the
configuration kept growing.

No single step caused this; the approach did. Wrapping the collector's own configuration language
in variables rebuilds its surface one knob at a time. A destination with a new requirement adds
knobs to both channels.

## Options considered
- **Keep adding variables.** Every destination is one setting away, but the surface grows without
  bound, and each knob costs six edits and a mutation.
- **Generate the collector configuration from a typed file.** One validated source, at the price of
  a second configuration language in front of the collector's and a renderer to maintain.
- **A closed core of variables for what nearly every destination needs, and recipes for the rest.**
  The collector's YAML stays the language for special cases. A recipe is a fragment an operator
  selects as it is, or copies and edits.

## Decision
The third option.

- **The core.** Each channel has these, under the same names:
  - whether it is on (`_CONFIG`);
  - where it sends (`_ENDPOINT`, plus the other transport's endpoint and plaintext switch);
  - how: `_PROTOCOL_CONFIG`, for OTLP/HTTP or OTLP/gRPC;
  - `_ENCODING` and `_COMPRESSION`;
  - one credential header: `_AUTH_CONFIG`, `_AUTH_HEADER`, `_AUTHORIZATION`;
  - TLS: `_CA_FILE`, `_CLIENT_CERT_FILE`, `_CLIENT_KEY_FILE`, `_INSECURE`;
  - batching, queue and retry.

  HTTP basic is a header value (`Basic <base64>`), not a credential kind of its own.
- **Recipes.** They live in `deploy/compose/otel/recipes/`, mounted read-only. There are three:
  Splunk HEC; OAuth2 client credentials; and Azure Monitor, which needs per-signal paths, protobuf
  and a managed identity. A recipe is selected through the existing `_PROTOCOL_CONFIG` or
  `_AUTH_CONFIG` slot.
- **Recipe values.** A recipe reads its own values from `deploy/compose/otel/custom/collector.env`,
  an optional, git-ignored env file. The names carry no `AIRA_` prefix, because they are not product
  settings. Compose does not declare them, so an unset one is absent and the recipe's inline
  fallback applies.
- **An installation's own fragments** go in `deploy/compose/otel/custom/`. That directory is
  git-ignored, mounted read-only, and its fragments are selected the same way.
- **The core is closed.** A test lists it. A new variable joins only if nearly every destination
  needs it; otherwise it is a recipe. The symmetry rule of `FRD-620` still holds, for the core.
- **Extensions in recipes.** A recipe that brings an extension restates the whole
  `service::extensions` list, because a merged list replaces. A test checks that it keeps the
  base's entries.
- **Comments.** Comments in the fragments and in Compose state the rule and its reason
  (`ADR-0023`).

## Consequences
- **Positive:**
  - Compose declares 32 channel variables instead of 66, and there are 8 fragments instead of 16.
  - A new destination is one file, not six edits across the product.
  - The collector's own documentation applies to a recipe directly.
- **Negative / trade-offs:**
  - An installation that set a removed variable must move to a header or a recipe. The removed
    variables are the per-signal endpoints and the basic, OAuth2, Azure-identity and HEC variables.
  - The hermetic suite checks a recipe's structure. `otelcol validate` checks it by hand, not in CI.
  - HEC on the observability channel is no longer shipped. An installation that needs it copies the
    delivery recipe and renames the exporters.
- **Follow-ups:**
  - `FRD-618`, `FRD-620` and `FRD-621` are amended.
  - `CONFIGURATION.md`, `INTEGRATIONS.md` §6 and `.env.example` describe the core and the recipes.
