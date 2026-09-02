# FRD-618 — Any OTLP consumer, not one of them

> Phase: 1 (observability) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner asked whether an external SIEM could be attached to the OTLP interfaces as they
> are, naming Microsoft Sentinel **as an example**, and then — after a first round answered the
> example — corrected the target: *generic compatibility with OTel consumers is more important than
> any Azure integration.* That correction is this document's subject, and it improved the design:
> what was one vendor's four requirements became the axes on which **any** destination varies.
> Related: [`FRD-616`](FRD-616-the-audit-trail-as-an-event-stream.md) (what the content is),
> [`FRD-617`](FRD-617-watching-the-wire-to-another-system.md) (watching the wire),
> [`FRD-615`](FRD-615-a-trace-crosses-the-bus.md) (what telemetry carries),
> [`ADR-0004`](../adr/ADR-0004-observability-grafana-otel-lgtm.md).

## 1. Problem

The answer to *"can I attach a SIEM"* was **"point `AIRA_OTEL_FORWARD_ENDPOINT` at it"**, and the
shape of that answer is right: the applications speak OTLP, the collector is the seam, and swapping
a destination is a configuration change (`ADR-0004`). Held against real destinations it did not
survive contact.

**The receiving side needed nothing.** Verified first, because it is half the question and the half
that turned out to be fine: the collector accepts OTLP over gRPC on 4317 and over HTTP on 4318, in
protobuf **or** JSON, gzipped or not — measured with a hand-written OTLP/JSON document from outside
the stack, `200`, and the span in the pipeline; a malformed one answered `400`. Any conformant OTel
producer can send to this installation today.

**The sending side was a single shape.** One URL with `/v1/traces` appended, JSON only, HTTP only,
one hard-coded header name, no client certificate, no way to turn compression off. A destination
varies on every one of those axes, and each one that could not be said was a destination that could
not be reached.

The example the question came with — Azure Monitor's OTLP ingestion, which is how telemetry reaches
a Log Analytics workspace and therefore Microsoft Sentinel — needed four of them at once. That made
it a good test case and a bad specification, which is what the owner's correction caught.

### 1.1 Seven axes, and what each one being fixed cost

| | was | a destination that needs otherwise |
| --- | --- | --- |
| **transport** | OTLP/HTTP only | OTLP defines gRPC *and* HTTP and a receiver may implement either. A gRPC-only collector was unreachable. |
| **encoding** | `json`, hard-coded | The specification makes **protobuf required and JSON optional**, so a conformant receiver may refuse JSON — and managed endpoints do. |
| **path** | `<endpoint>` + `/v1/traces` | A per-tenant path, a stream or index name, an API version in the query, a different host for one signal. |
| **credential name** | `Authorization`, hard-coded | `x-api-key`, `api-key`, `DD-API-KEY`, `X-Honeycomb-Team`, `X-Seq-ApiKey` — `Authorization` is what a *minority* ask for. |
| **credential kind** | a static string | Basic auth; OAuth2 client credentials, which is what every identity provider implements and what a token that expires requires. |
| **who we are** | nothing | Mutual TLS, where the receiver authenticates the sender by certificate rather than by a header. |
| **compression** | `gzip`, always | A receiver that does not unwrap it — the symptom is a `400` about a malformed payload, which reads as *our telemetry is wrong*. |

Two of those deserve their own paragraph, because the reasoning is not obvious.

**The encoding was a literal for a good reason, which is what made it hard to see.** OTLP/JSON is
the readable half of the wire, and being able to read what goes over is worth a great deal while
wiring something up. But the OTLP specification requires protobuf and makes JSON optional, so
"readable" was a preference imposed on every future integrator — and against a protobuf-only
receiver it was not a preference at all, it was a wall. **A literal in a configuration file is a
preference until somebody's destination makes it a constraint.**

**A static header is the wrong kind of credential for anything an identity provider mints.** A
token lives an hour; a header set at start-up stops working during the first afternoon, and the
failure is a `401` in a container log. The generic answer is OAuth2 client credentials
(RFC 6749 §4.4) — Keycloak, Okta, Auth0, Ping, Entra, and every product that sits behind one — so
that is one fragment rather than one per vendor.

**And a private CA had no answer but abandoning verification.** `tls` offered
`insecure_skip_verify` and nothing else. A destination behind an internal CA is the ordinary case
in the deployments this system is built for, and the only route to one was to stop checking every
certificate on the leg — a decision about the whole connection, taken to solve a missing root.

### 1a. And one defect that was nobody's destination

Measured on 2026-09-02 against a receiver that prints what it received, with no credential
configured:

```
/v1/traces  application/json  authorization=''
```

The `headers:` block was spelled in the forwarding fragment, so it was **always present**, and an
unset variable produced an empty header rather than no header. Harmless against a receiver that
ignores it; a `400` from one that parses it; and actively wrong beside an authenticator extension,
which sets that same header itself. This is the shape `LESSONS.md` §1 lists in another costume: the
line looked correct and did something nobody asked for.

## 2. What it costs to be missing

An integrator reads `INTEGRATIONS.md` §6, sets one variable, recreates the collector, and watches
nothing arrive. Every diagnostic this project has built then answers *yes*: the debug channel says
the export left, `make otel-status` says the collector forwarded it, the arrivals file has the
document in it. The refusal is at the far end, in somebody else's portal, and it is a `400` about
a content type.

That is worse than a missing feature. The stack asserts success at every hop it can see, so the
first hypothesis is *"our telemetry is wrong"* rather than *"the encoding is not negotiable"*.

## 3. Design

### 3.1 Four `--config` flags: where, how, and who

The collector is started with **four**, the last three the empty fragment until a variable names a
real file. Three slots because a destination varies in three ways that do not depend on each other,
and YAML has no conditional:

| variable | question | choices |
| --- | --- | --- |
| `AIRA_OTEL_FORWARD_CONFIG` | **whether**, and **where** | off · `forward.yaml` |
| `AIRA_OTEL_FORWARD_PROTOCOL_CONFIG` | **how it is reached** | OTLP/HTTP · `forward-grpc.yaml` for OTLP/gRPC |
| `AIRA_OTEL_FORWARD_AUTH_CONFIG` | **who we say we are** | nothing · header · basic · OAuth2 · a platform identity |

The credential fragments, generic first:

| | |
| --- | --- |
| _(unset)_ | no credential header of any kind |
| `…/forward-auth-header.yaml` | **any header name** and value — `x-api-key`, `DD-API-KEY`, `Authorization: Bearer …` |
| `…/forward-auth-basic.yaml` | HTTP basic |
| `…/forward-auth-oauth2.yaml` | OAuth2 client credentials (RFC 6749 §4.4), fetched and refreshed |
| `…/forward-auth-azure-identity.yaml` | the one thing OAuth2 cannot say: **no secret anywhere** |

**A vendor fragment earns its place only by doing something the generic one cannot say.** The first
round shipped an Azure *service principal* fragment; it has been deleted, because it was
`forward-auth-oauth2.yaml` with three values filled in — Entra implements RFC 6749 §4.4 like
Keycloak and Okta, and its token URL is a value in a `.env` file. What survives is the managed
identity, which has no secret to configure and no generic spelling: the token comes from the
instance metadata endpoint and *which* identity is a platform fact.

That rule is the correction the owner made, turned into something a future round can apply.

### 3.1a Both transports are defined; the fragment moves the pipelines

`otlphttp/forward` and `otlp/forward` are **both** declared in `collector-forward.yaml`, always, and
`forward-grpc.yaml` contains nothing but four pipeline exporter lists. That looks like the wrong
place for the gRPC exporter until you try the other arrangement:

**An exporter is validated whether or not a pipeline references it.** Measured:
`exporters::otlp/forward: requires a non-empty "endpoint"` from a configuration where nothing used
it. A credential fragment has to attach `auth:` to *both* exporters — it cannot know which
transport is in play — so a fragment that introduced the gRPC exporter that way would have stopped
the collector on every HTTP deployment. Both defined, both carrying the `.invalid` guard, and the
pipelines decide.

The fragment is deliberately **only** pipelines: same filter, same batching, same credential. The
transport is not supposed to change what the second destination receives.

### 3.2 One URL per signal, defaulted to the ordinary one

`traces_endpoint`, `logs_endpoint` and `metrics_endpoint` are named separately, and Compose
computes `<AIRA_OTEL_FORWARD_ENDPOINT>/v1/<signal>` when nothing overrides them. A plain receiver
is still one variable; a routed destination is now expressible.

The defaults are spelled **in Compose** rather than in the fragment, for the reason the endpoint's
already was: Compose passes an empty string for an unset variable, and an empty string overrides a
`${env:…:-default}` inside the collector.

### 3.3 The encoding is a variable, and a typo stops the collector

`AIRA_OTEL_FORWARD_ENCODING` is `json` or `proto`. A third value fails validation and the container
restarts, taking Grafana with it, because one container carries every exporter. Measured on
2026-09-02 with `protobuf` — the plausible typo — against a collector started with these fragments:
**`restarting`, 7 restarts in 12 seconds**, saying

```
'exporters' error reading configuration for "otlphttp/forward": decoding failed due to the
following error(s):

'encoding' invalid encoding type: protobuf
```

That is the opposite of how a missing *endpoint* is treated, and the difference is deliberate. An
endpoint can be **forgotten** — somebody sets the switch while configuring something else, and a
stack that dies for that is a stack that punishes an ordinary mistake. An encoding is one of two
words, typed on purpose, in the same minute as the endpoint it belongs to. The same reasoning
applies to a credential fragment selected without its credentials: that file is only ever read
because a person named it, with the values in front of them.

Written down rather than smoothed over, because the one thing that must not happen is a reader
concluding that *any* forwarding mistake is survivable.

### 3.4 A private CA is nameable

`AIRA_OTEL_FORWARD_CA_FILE` points at a certificate inside the collector;
`deploy/compose/otel/ca/` is mounted at `/etc/otelcol-contrib/ca` for it, git-ignored but for its
README. Empty means the system trust store. `AIRA_OTEL_FORWARD_INSECURE` stays, and is now the
thing it should always have been: a reachability test, not the way to reach production.

`AIRA_OTEL_FORWARD_CLIENT_CERT_FILE` / `_CLIENT_KEY_FILE` are the other direction — mutual TLS,
where the receiver authenticates *us* by certificate instead of, or as well as, a credential in a
header. And `AIRA_OTEL_FORWARD_COMPRESSION` (`gzip` · `none` · `zstd` · `snappy`) exists because a
receiver that does not unwrap gzip answers `400` about a malformed payload, which reads as *our
telemetry is wrong* and sends a reader to look at the wrong end of the wire.

### 3.4a A configurable header name, and why it is an extension

`Authorization` is what a minority of OTLP receivers ask for, so the header **name** is
`AIRA_OTEL_FORWARD_AUTH_HEADER`. The obvious way to write that is

```yaml
headers:
  ${env:AIRA_OTEL_FORWARD_AUTH_HEADER:-authorization}: ${env:AIRA_OTEL_FORWARD_AUTHORIZATION}
```

and it **validates and does not work.** `otelcol validate` answered `rc=0`; on the running stack
every export then failed with

```
net/http: invalid header field name "${env:AIRA_OTEL_FORWARD_AUTH_HEADER:-authorization}"
```

The collector substitutes `${env:…}` in a **value** and not in a **key**, and validation cannot see
the difference because a key is a string and that string is a perfectly good one. Nothing on the
sending side said anything: the retries went into a container log, the page showed no arrivals, and
the symptom was indistinguishable from a receiver that is down.

This project's own sentence one layer further down: **"no errors" and "it worked" are different
statements**, and a configuration validator only ever answers the first. It is also the reason
every one of these is driven against a real receiver rather than validated — this one passed every
check short of looking at the far end.

The `headers_setter` extension takes the name in a *value* position (`key:` inside a list item),
where substitution does happen. Verified end to end: `x-api-key: (opaque, 13 chars)` on the
receiver. A guard test now fails on any fragment that keys a header by a variable.

### 3.5 The SIEM filter survives a rename it has not had yet

The expression selecting what the second destination receives tests `attributes["http.url"]`,
which is what this build's clients actually write — verified on the running stack rather than taken
from the specification, which has since renamed it `url.full`. The rename arrives with an opt-in in
`opentelemetry-instrumentation-httpx`.

On the day it lands, an expression naming only the old spelling starts dropping **every upstream
call**, silently: the request spans still arrive, so the feed looks alive, and the calls that
carried data outside this installation — the half a SIEM is there for — simply stop. Both spellings
are named, and a test asserts both, so the upgrade fails something rather than quietly emptying
half the feed.

## 4. Seeing it before a SIEM exists

`make otlp-inspector` starts an ordinary OTLP/HTTP receiver in the `debug` profile with a page in
front of it: the last few hundred batches, the spans flattened to one row each with their `aira.*`
attributes, the content type and encoding, and **whether a credential was on the request**.

It answers the leg no existing tool did. `make otel-arrivals` and `AIRA_OTEL_ARRIVED_FILE` are what
*arrived* at the collector, before the SIEM filter and before anything is forwarded;
`make otel-status` is counters. None of them is *what left, on the leg that leaves* — which is the
one an integrator is actually asking about, and the one where the filter, the encoding and the
credential all take effect.

It is a debugging tool and is shaped like one. In memory, capped, lost on restart, unauthenticated,
and in a profile `make up` does not start. It holds `aira.subject`, `aira.source_ip` and whatever a
log line carried, which is the content `FRD-505` puts behind a role check and a retention clock —
so it belongs on a developer's machine and nowhere else, and the code says so where somebody
deploying it would read.

The one thing it deliberately does not keep is the **value** of an `Authorization` header. *Is my
credential on the request* is the question; the credential is not the answer, and a page people
leave open is a page that ends up in a screenshot.

Protobuf is accepted, counted, sized and labelled — not decoded. `AIRA_OTEL_FORWARD_ENCODING=proto`
is what Azure needs, so batches will arrive that way; decoding them would mean a schema and a
dependency, and the page says which variable makes them readable instead.

## 5. Functional requirements

- **FR-1** The forwarding leg sends **no** credential header unless an auth fragment is selected.
  An unset credential is an absent header, not an empty one.
- **FR-2** Each signal's destination URL is nameable in full, and defaults to
  `<endpoint>/v1/<signal>`.
- **FR-3** The wire encoding is `json` or `proto`, chosen by configuration.
- **FR-4** Either OTLP transport is reachable — HTTP or gRPC — with the same filter, batching and
  credential, chosen by configuration.
- **FR-5** A credential can be sent under **any header name**.
- **FR-6** HTTP basic and OAuth2 client credentials are available without editing a shipped file;
  the OAuth2 token is fetched and refreshed by the collector.
- **FR-7** A destination behind a private CA is reachable without disabling verification, and one
  that authenticates the sender by certificate is reachable at all.
- **FR-8** Compression is configurable, including off.
- **FR-9** Every state above leaves the *other* destination working: Grafana, the arrivals file and
  the debug exporter keep receiving.
- **FR-10** The `traces/siem` filter selects the same spans under either spelling of the HTTP URL
  attribute.
- **FR-11** What the forwarding leg sends can be read on a page, without a receiver of one's own,
  and that page never shows a credential — whatever header it arrived under.
- **FR-12** A conformant OTel producer can send to this installation over either transport, in
  protobuf or JSON. (Already true; verified rather than built.)

## 6. Testing

`tools/tests/test_the_siem_gets_requests_not_plumbing.py` went from 13 cases to **42** — the absent
headers block, the per-signal endpoints, the encoding and compression variables, the CA and client
certificate, both `--config` defaults, the shape of a credential fragment, that each one registers
the extension it names and **covers both transports**, that none of them keys a header by a
variable, that the gRPC fragment moves every pipeline and nothing else, and that every fragment a
variable can name is mounted.

`tools/tests/test_the_inspector_shows_what_was_forwarded.py` is **19** more, built on a *real* OTLP
body taken off the forwarding leg — hand-written OTLP is OTLP as somebody remembers it, and the
memory of `{"key": …, "value": {"stringValue": …}}` is exactly what is unreliable about it.

**Thirteen mutations** (`ID37`–`ID45`, `OI1`–`OI4`), each caught. Two of them survived their first
run and both are worth recording:

- `ID43` — *the header name goes through the extension* — because the mutation added a second
  `exporters:` key and YAML keeps the last, so the fragment it produced was one no test recognised
  as the shape being forbidden. Re-aimed at the block that already existed.
- `OI1` — *the inspector reports a credential without printing it* — went **stale** rather than
  surviving: the line it anchored on had been rewritten when credential handling stopped being
  about `Authorization`. A mutation whose anchor has moved reports green about nothing, which is
  why the harness calls that out separately.

## 7. Demonstrated, not asserted

Every line below is from the running stack on 2026-09-02, driven end to end against a receiver that
prints what it got — never from `validate` alone, which is the check that passed while the header
name was a literal `${env:…}`.

**The empty header, before and after.** With forwarding on and no credential configured:

```
before   /v1/traces  application/json  authorization=''
after    /v1/traces  application/json  authorization=None
```

**Protobuf and a credential**, through the header fragment:

```
/v1/logs    application/x-protobuf  authorization='Bearer test-siem-credential'
/v1/traces  application/x-protobuf  authorization='Bearer test-siem-credential'
```

**Any header name, and no compression** (`AIRA_OTEL_FORWARD_AUTH_HEADER=x-api-key`,
`AIRA_OTEL_FORWARD_COMPRESSION=none`), as the inspector shows it:

```
x-api-key: (opaque, 26 chars)     application/json     identity     1346→1346 B
```

**HTTP basic** — the password nowhere on the page:

```
authorization: Basic (34 chars)
```

**OAuth2 client credentials**, against a token endpoint stood up for the purpose. The collector
asked for a token, with **both scopes carried in one variable**, and the token it was issued
arrived at the receiver:

```
POST /token   grant_type=client_credentials&scope=telemetry.write+telemetry.read
              authorization: Basic YWlyYS1nYXRld2F5OmEtY2xpZW50LXNlY3JldA==
receiver      authorization: Bearer (21 chars)
```

**OTLP/gRPC**, against a real gRPC receiver chained into the inspector so the content is visible
after crossing:

```
otelcol_exporter_sent_spans{exporter="otlp/forward",server_address="aira-grpc-recv"}        13
otelcol_exporter_sent_log_records{exporter="otlp/forward",server_address="aira-grpc-recv"}  11
```

with `aira.use_case`, `aira.outcome`, `aira.cost_nanos` and the rest intact on the far side, and no
`send_failed` of either kind. (The credential over gRPC becomes call metadata, which a relay does
not pass on; the authenticator is the same object either way and is measured on the HTTP path.)

**All eleven merged configurations validate** against `otel/opentelemetry-collector-contrib:0.157.0`
— off, plus each of two transports against each of five credential states.

**And the receiving side takes what any producer sends.** A hand-written OTLP/JSON document posted
from outside the stack: `200`, and the span through the pipeline. Gzipped: `200`. Malformed: `400`.

**The filter still selects.** Across a demo run with forwarding on:

```
otelcol_exporter_sent_spans{exporter="otlp_grpc/lgtm"}   347
otelcol_exporter_sent_spans{exporter="otlphttp/forward"}  20
```

The twenty are eleven requests and the nine upstream calls they made — `FRD-616`'s point restated
at the other end of the wire, and a factor of seventeen.

## 8. What this does not do

**It does not make Azure's side of the work disappear.** A Data Collection Endpoint, a Data
Collection Rule pointing at the workspace, and **Monitoring Metrics Publisher** granted on that
rule to the collector's identity are Azure-side steps this repository cannot take. What changed is
that AIRA can now be pointed at the result.

**It does not send the audit trail.** That is `FRD-616`, and it is still open. What a SIEM receives
today is spans carrying attribution, model, outcome, tokens and cost, plus the log records AIRA's
own processes emit. The database rows — `request_logs`, `payload_access`, `access_suspensions`,
`anomaly_events` — are not published anywhere, and `FRD-616` §7 is the list of decisions the owner
has to take before they are.

**It does not survive a collector restart.** The sending queue is in memory: a restart while the
far end is down loses whatever is queued, with nothing said. `file_storage` is in this image and
would fix it, and needs a writable volume and a size policy — named in the fragment and left to its
own round, because for an audit feed the difference between *held* and *lost* is the whole
guarantee.

**And metrics into Azure will need one more processor.** Azure's Application Insights experiences
expect **delta** temporality and exponential histograms; `opentelemetry-python` produces cumulative
with explicit buckets. `cumulativetodelta` is in the image. Not wired up, because it is a change to
what the *Grafana* leg would receive as well and belongs with somebody who has the workspace in
front of them.
