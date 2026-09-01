# ADR-0022 — A call to another system says so, on one switch

> Status: **Accepted** · Date: 2026-09-01 · Deciders: Vadim Scheibe
> Supersedes: none · Related: [`ADR-0004`](ADR-0004-observability-grafana-otel-lgtm.md),
> [`ADR-0007`](ADR-0007-security-hardening-baseline.md),
> [`FRD-617`](../features/FRD-617-watching-the-wire-to-another-system.md)

## Context

Every signal this system produces describes a request it **received**: a server span per request,
an audit row per answer, a degradation entry per feature. The calls it *makes* — to the OTel
collector, the broker, Keycloak, Vault, Redis, Postgres — were visible only where the caller had
happened to log a failure, and never when they went well.

That gap has a shape, and it is the one `tools/lab_status.py` was written for one layer further
out: **"no errors" and "it arrived" are different statements**, and only the first can be read off
a log. An OTLP exporter reports a success nowhere at any level. A Kafka producer discarded the
partition and offset the broker gave it. An unreachable Keycloak was reported as every caller's
token being invalid, at `INFO`.

Three ways to close it were considered.

## Decision

**One process-wide channel, selected by system, off by default, emitted as ordinary structured log
lines.** `AIRA_DEBUG_INTEGRATIONS` names systems from a closed vocabulary; a call to one of them is
wrapped in `watch(...)`, which emits exactly one line saying what happened and how long it took.

Three properties are the decision, and each rules something out:

**It is a log line, not an endpoint and not a metric.** An HTTP endpoint exposing recent
integration failures is a second authorization surface carrying internal topology — hostnames,
ports, realms, driver messages — and `ADR-0007` spent real effort making `/readyz` say *less* than
it knew. A metric answers *how many*, and the question during an integration is *what did that one
say*. The service log is the place this project already treats as readable by operators and not by
callers.

**The lines go out at `INFO`, not at `DEBUG`.** A debug facility whose first use is discovering
that it also needs `AIRA_LOG_LEVEL=DEBUG` is one switch too many, and lowering the root level buys
every library's opinion along with the six lines that were wanted.

**The vocabulary is closed and a misspelling refuses the process.** A setting that silently means
nothing is worse than one that is missing: the operator concludes the feature does not work.

## Consequences

- Integration failures — a wrong port, an expired trust store, a SASL mechanism the broker does not
  offer, a Vault path nobody has written — are one line with the reason in it, instead of a silence
  or somebody else's fault.
- Anything the channel says about `otel` is written to stdout and never exported, because a line
  reporting a failed export would otherwise become a record queued for export. The same rule fixed
  a live defect: the SDK's own explanation of every failed export was being posted, through the
  root logger, to the exporter that had just failed.
- A seventh system is a name in one tuple and a `watch(...)` at its call site.
- What the channel says is *that* a call happened and how it ended — never a payload, a token, a
  secret value, a message body or a bound parameter. Addresses pass the shared credential
  redaction, extended for this decision to cover a `user:password@` authority as well as a
  credential-bearing query parameter.
- **Not** a replacement for the trace. A span says how a request spent its time; these lines say
  whether a dependency answered at all, including in the processes that serve no requests.

## Alternatives considered

**Turn up the log level of every client library.** Free, and unusable: the shape and the vocabulary
differ per library, half of them say nothing on success, and `DEBUG` on `opentelemetry`, `aiokafka`
and `urllib3` together is thousands of lines an hour with the six that matter inside them.

**An endpoint or a page in the console.** Rejected for the authorization surface above, and because
the failures that matter most happen at start-up, in processes that serve no HTTP at all — the
outbox relay, the config consumer, the retention sweep.

**Always on.** Rejected: on a busy gateway the `redis` and `postgres` lines alone would dominate the
log, and an installation that is working should not pay for a facility it is not using. Off is the
default and off is genuinely off — one set membership test per call site.
