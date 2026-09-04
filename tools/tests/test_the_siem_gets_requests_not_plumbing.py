"""The second destination sees requests, not the database.

Two destinations want opposite things, and that is why they are two pipelines rather than one with
a filter bolted on the end. Grafana wants **everything** — SQL statements, pool connections, ASGI
internals — because that is how *"was the gateway slow or was the model slow"* gets answered. A
SIEM wants one record per request, plus the calls that carried data outside this installation.

Measured on the shipped stack: three requests produced **184 spans**, of which **6** are those two
things. Sending a SIEM the other 178 is worse than sending it nothing — it buries the six, and
every one of them costs whatever the far end charges, which is how this started: a receiver
answering `429`.

Read as YAML, because a pipeline is a structure and a grep for a name would pass on a name inside
a comment explaining why it is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "deploy" / "compose" / "otel" / "collector-config.yaml"
FORWARD = ROOT / "deploy" / "compose" / "otel" / "collector-forward.yaml"

FORWARD_EXPORTER = "otlphttp/forward"


def _pipelines(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["service"]["pipelines"]


def test_the_forwarding_fragment_still_describes_its_pipelines() -> None:
    """A guard on the guard: a renamed key makes every assertion below vacuous."""
    assert set(_pipelines(FORWARD)) == {"traces/siem", "metrics/siem", "logs/siem"}


#: The observability stream's pipelines. The delivery fragment must not name one.
BASE_PIPELINES = {"traces", "metrics", "logs"}


def test_the_delivery_stream_never_names_the_observability_stream() -> None:
    """**They are two independent streams, not a branch and its parent.**

    One is observability — every span, for *was the gateway slow or was the model slow*. The other
    is delivery: one record per API access and per model access, for whoever consumes that
    elsewhere. Different contents, volumes, tuning and failure consequences.

    This fragment used to reach into the base pipelines to append its exporter, which made the
    second stream a branch of the first in three ways at once — shared batching, no pipelines of
    its own for metrics and logs, and a restated exporter list that silently unhooked Grafana if
    anybody forgot an entry. Naming nothing from the base is what makes the base byte-for-byte the
    same whether this fragment is merged or not.
    """
    named = set(_pipelines(FORWARD)) | set(_pipelines(GRPC))

    assert not (named & BASE_PIPELINES), (
        f"the delivery fragment names {sorted(named & BASE_PIPELINES)} — a merged pipeline "
        "replaces, so touching the observability stream at all is how it gets retuned or unhooked "
        "by a change that was about somewhere else entirely."
    )


def test_the_two_streams_batch_independently() -> None:
    """`AIRA_OTEL_FORWARD_BATCH_*` must reach the stream it names and no other.

    It used to redefine the **shared** `batch`, and the base pipelines use `batch` — so setting the
    forwarding batch window retimed the trace backend's delivery too. Measured on 2026-09-03 with
    `AIRA_OTEL_FORWARD_BATCH_SECONDS=30s`: the collector reported exactly one
    `processor="batch"`, and Grafana was batching on the second destination's clock. After the
    split it reports `batch` **and** `batch/siem`.
    """
    processors = yaml.safe_load(FORWARD.read_text(encoding="utf-8"))["processors"]

    assert "batch/siem" in processors
    assert "batch" not in processors, (
        "redefining the shared `batch` reaches the observability stream, which this fragment does "
        "not own — name the delivery stream's own processor instead"
    )
    for pipeline in _pipelines(FORWARD).values():
        assert "batch" not in pipeline["processors"]
        assert "batch/siem" in pipeline["processors"]


@pytest.mark.parametrize("signal", ["traces", "metrics", "logs"])
def test_each_signal_reaches_the_second_destination_on_a_pipeline_of_its_own(signal: str) -> None:
    """Metrics and logs used to have none — the forward exporter was appended to the base
    pipelines, so those two signals *were* a branch: no filter of their own, no batching of their
    own, and nothing that could be tuned or fail independently. Whether they are filtered stays a
    decision now, rather than a consequence of where they happened to be attached."""
    pipeline = _pipelines(FORWARD)[f"{signal}/siem"]

    assert pipeline["exporters"] == [FORWARD_EXPORTER]
    assert pipeline["receivers"] == ["otlp"]


def test_only_the_trace_pipeline_selects() -> None:
    """The filter is the traces question. Metrics and logs go over whole — they are small, and an
    `oidc_jwks_unavailable` is exactly what a second destination is for."""
    pipelines = _pipelines(FORWARD)

    assert "filter/siem" in pipelines["traces/siem"]["processors"]
    for signal in ("metrics", "logs"):
        assert "filter/siem" not in pipelines[f"{signal}/siem"]["processors"]


def test_the_filter_names_the_two_things_a_siem_needs() -> None:
    """The expression is a negation — `filter` drops what it makes true — so it reads backwards,
    and the parts are asserted rather than the string, which would break on whitespace.

    `parent_span_id.string == "0000000000000000"` is what separates a real upstream call from the
    reachability prober (`FRD-117`), which asks every model every 60 seconds whether it is there.
    An empty parent is sixteen zeroes and not an empty string — the first draft used `""`, and only
    replaying a real sample through a real collector showed it letting 32 prober spans through.
    """
    conditions = yaml.safe_load(FORWARD.read_text(encoding="utf-8"))["processors"]["filter/siem"][
        "traces"
    ]["span"]

    assert len(conditions) == 1, conditions
    expression = conditions[0]
    assert 'attributes["aira.use_case"] == nil' in expression
    assert 'attributes["http.url"] == nil' in expression
    assert 'parent_span_id.string == "0000000000000000"' in expression


def test_the_filter_names_both_spellings_of_the_url_attribute() -> None:
    """`http.url` is what this build's clients write — verified on the running stack — and the
    semantic conventions have since renamed it `url.full`.

    The rename arrives with an opt-in in `opentelemetry-instrumentation-httpx`, and on the day it
    does, an expression naming only the old one starts dropping every upstream call **silently**:
    a filter that selects nothing looks exactly like a system making no calls, and the second
    destination would go on receiving the request spans, so nothing would look broken. Naming both
    costs one clause and survives the upgrade in either direction.
    """
    expression = yaml.safe_load(FORWARD.read_text(encoding="utf-8"))["processors"]["filter/siem"][
        "traces"
    ]["span"][0]

    assert 'attributes["url.full"] == nil' in expression


# --- and it is genuinely optional ----------------------------------------------------------------


COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"
NOFORWARD = ROOT / "deploy" / "compose" / "otel" / "collector-noforward.yaml"


def test_the_off_state_is_an_empty_fragment() -> None:
    """One collector, two `--config` flags, and the second is `{}` until somebody selects the
    forwarding one. Off, the merge is a no-op — there is no second container, no second exporter
    and no filter pipeline."""
    assert yaml.safe_load(NOFORWARD.read_text(encoding="utf-8")) == {}


def test_compose_defaults_to_the_empty_fragment() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "--config=${AIRA_OTEL_FORWARD_CONFIG:-/etc/otelcol-contrib/noforward.yaml}" in compose


def test_the_endpoint_has_a_fallback_that_lets_the_collector_start() -> None:
    """**Compose passes an empty string for an unset variable**, and an empty string overrides a
    `${env:…:-default}` inside the collector — so the fallback has to be spelled at this end or it
    never applies.

    Without it, switching forwarding on and forgetting the endpoint fails *validation*, and the
    collector restarts for ever — taking Grafana with it, because one container carries every
    exporter. Measured: `Restarting (1)`, with the reason only in the logs of a container nobody
    is watching. `.invalid` never resolves (RFC 2606), so instead the collector starts, that one
    exporter fails by name, and everything else keeps working.
    """
    compose = COMPOSE.read_text(encoding="utf-8")

    assert (
        "AIRA_OTEL_FORWARD_ENDPOINT: ${AIRA_OTEL_FORWARD_ENDPOINT:-"
        "http://forward-endpoint-not-set.invalid:4318}" in compose
    )


# --- and it can reach a destination that is not a plain OTLP receiver -----------------------------


OTEL = ROOT / "deploy" / "compose" / "otel"
GRPC = OTEL / "collector-forward-grpc.yaml"

#: Every credential fragment, and the transport-neutral property each must have.
#:
#: **Generic first, and the ordering is the design.** A vendor fragment earns its place only by
#: doing something the generic ones cannot say — which is why there is no Azure service-principal
#: file: it was `forward-auth-oauth2.yaml` with three values filled in, and Entra implements
#: RFC 6749 §4.4 like everybody else. What is left of Azure is a managed identity, which has no
#: secret to configure anywhere and therefore no generic spelling.
AUTH_FRAGMENTS = [
    OTEL / "collector-forward-auth-header.yaml",
    OTEL / "collector-forward-auth-basic.yaml",
    OTEL / "collector-forward-auth-oauth2.yaml",
    OTEL / "collector-forward-auth-azure-identity.yaml",
]

#: The two transports OTLP defines, as the two exporters that speak them.
BOTH_EXPORTERS = {"otlphttp/forward", "otlp/forward"}


def _exporter(path: Path, name: str = FORWARD_EXPORTER) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["exporters"][name]


def test_the_forwarding_fragment_carries_no_authorization_header() -> None:
    """**The defect this split exists for.** With `headers: {authorization: ${env:…}}` spelled in
    the forwarding fragment, an unset variable did not omit the header — it sent an empty one, on
    every request. Measured on 2026-09-02 against a receiver that prints what it received:
    `authorization: ''`.

    Harmless against a receiver that ignores it, a `400` from one that parses it, and actively
    wrong beside an authenticator extension, which sets that same header itself. So the header
    lives in a fragment nobody merges unless they mean to.
    """
    exporter = _exporter(FORWARD)

    assert "headers" not in exporter, (
        "an always-present headers block is an always-present header: with nothing configured the "
        "collector sends `authorization: ''` rather than omitting it"
    )


def test_each_signal_has_its_own_endpoint() -> None:
    """A single `endpoint` makes the collector append `/v1/traces`, which is right for a plain
    receiver and unreachable for one with a route in front of it — Azure Monitor's OTLP ingestion
    puts a data-collection-rule id and a stream name mid-path and uses a different host for
    metrics. Compose computes the ordinary suffixes when nothing overrides them."""
    exporter = _exporter(FORWARD)

    assert set(exporter) >= {"traces_endpoint", "logs_endpoint", "metrics_endpoint"}
    assert "endpoint" not in exporter, (
        "a bare `endpoint` beside the per-signal ones is a second answer to the same question"
    )


def test_the_encoding_is_a_variable_and_not_a_literal() -> None:
    """It was `json`, hard-coded, and that is a wall rather than a preference: Azure Monitor
    documents **HTTP/protobuf only** for OTLP ingestion, so the shipped fragment could not reach
    the destination this project's owners are most likely to have."""
    assert "${env:AIRA_OTEL_FORWARD_ENCODING" in str(_exporter(FORWARD)["encoding"])


def test_a_private_ca_is_nameable_without_abandoning_verification() -> None:
    """`insecure_skip_verify` answers a missing root by not checking any certificate — a decision
    about the whole leg taken to solve one file. A SIEM behind an internal CA is the ordinary case
    in the deployments this system is for."""
    tls = _exporter(FORWARD)["tls"]

    assert "ca_file" in tls
    assert "insecure_skip_verify" in tls


@pytest.mark.parametrize("path", AUTH_FRAGMENTS, ids=lambda p: p.stem)
def test_an_auth_fragment_adds_authentication_and_nothing_else(path: Path) -> None:
    """A merged exporter list replaces and a merged pipeline replaces, so an auth fragment that
    mentioned either would silently unhook something. They exist to answer one question."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert set(document) <= {"exporters", "extensions", "service"}
    assert "pipelines" not in document.get("service", {})


@pytest.mark.parametrize("path", AUTH_FRAGMENTS, ids=lambda p: p.stem)
def test_an_auth_fragment_covers_both_transports(path: Path) -> None:
    """**A credential must not depend on which transport is in play.** Protocol and credential are
    chosen by two independent variables, so a fragment that authenticated only `otlphttp/forward`
    would leave the gRPC leg anonymous — and nothing would say so until the receiver answered
    `401`, which reads as a wrong credential rather than as an absent one."""
    exporters = yaml.safe_load(path.read_text(encoding="utf-8"))["exporters"]

    assert set(exporters) == BOTH_EXPORTERS


@pytest.mark.parametrize("path", AUTH_FRAGMENTS, ids=lambda p: p.stem)
def test_an_auth_fragment_registers_the_extension_it_points_at(path: Path) -> None:
    """An `auth.authenticator` naming an extension that `service.extensions` does not list is a
    configuration the collector refuses at start-up — which is the good outcome, and still a
    restart loop that takes Grafana with it, because one container carries every exporter."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    for exporter in document["exporters"].values():
        named = exporter["auth"]["authenticator"]
        assert named in document["extensions"]
        assert named in document["service"]["extensions"]


@pytest.mark.parametrize("path", AUTH_FRAGMENTS, ids=lambda p: p.stem)
def test_no_auth_fragment_puts_a_variable_in_a_header_name(path: Path) -> None:
    """**`${env:…}` is substituted in a value and not in a key**, and the collector's own
    `validate` cannot see the difference — a key is a string and that string is a valid one.

    Measured on 2026-09-02 on the running stack: a `headers:` map keyed by
    `${env:AIRA_OTEL_FORWARD_AUTH_HEADER:-authorization}` validated `rc=0` and every export then
    failed with *invalid header field name "${env:AIRA_OTEL_FORWARD_AUTH_HEADER:-authorization}"*,
    in a container log nobody was watching. A configurable header name has to go through the
    `headers_setter` extension, where the name is in a value position.
    """
    for exporter in yaml.safe_load(path.read_text(encoding="utf-8"))["exporters"].values():
        for key in exporter.get("headers") or {}:
            assert "${env:" not in str(key), (
                f"{path.name} keys a header by an environment variable. That validates and does "
                "not work: substitution happens in values, not in keys. Use `headers_setter`."
            )


def test_compose_defaults_both_overlay_fragments_to_the_empty_one() -> None:
    """Four `--config` flags, always. The last three are `{}` until somebody names a real file —
    the same shape for each, for the same reason: a destination is configured in steps, and no step
    may leave the collector unable to start."""
    compose = COMPOSE.read_text(encoding="utf-8")

    for variable in ("AIRA_OTEL_FORWARD_PROTOCOL_CONFIG", "AIRA_OTEL_FORWARD_AUTH_CONFIG"):
        assert f"--config=${{{variable}:-/etc/otelcol-contrib/noforward.yaml}}" in compose


@pytest.mark.parametrize("path", [*AUTH_FRAGMENTS, GRPC], ids=lambda p: p.stem)
def test_every_selectable_fragment_is_mounted(path: Path) -> None:
    """A fragment a variable can name and no container has is a path that fails at start-up, and
    the failure names a file rather than the variable that chose it."""
    assert f"./otel/{path.name}:" in COMPOSE.read_text(encoding="utf-8")


# --- the two transports OTLP defines -------------------------------------------------------------


def test_both_transports_are_defined_in_the_forwarding_fragment() -> None:
    """**Both, always, and not one per fragment.** An exporter is validated whether or not a
    pipeline references it — measured: `exporters::otlp/forward: requires a non-empty "endpoint"`
    from a configuration where nothing used it. So an auth fragment that introduced the gRPC
    exporter by adding an `auth:` block to it, which it must, would stop the collector whenever the
    HTTP transport was the one in play."""
    exporters = yaml.safe_load(FORWARD.read_text(encoding="utf-8"))["exporters"]

    assert set(exporters) >= BOTH_EXPORTERS
    assert exporters["otlp/forward"]["endpoint"], "the gRPC leg needs the same `.invalid` guard"


def test_the_grpc_fragment_only_moves_the_pipelines() -> None:
    """The transport must not change what the second destination receives — same filter, same
    batching, same credential. If this fragment grew an exporter or a processor, the two legs would
    start to differ in ways nobody chose."""
    document = yaml.safe_load(GRPC.read_text(encoding="utf-8"))

    assert set(document) == {"service"}
    assert set(document["service"]) == {"pipelines"}


def test_the_grpc_fragment_moves_every_delivery_pipeline_and_no_other() -> None:
    """A merged exporter list replaces, so a pipeline this fragment forgets keeps sending over
    HTTP — half the signals on one transport and half on the other, which reads as *the receiver is
    dropping things*. And a base pipeline it named would move the **trace backend** onto a
    transport chosen for somewhere else."""
    pipelines = _pipelines(GRPC)

    assert set(pipelines) == {"traces/siem", "metrics/siem", "logs/siem"}
    for pipeline in pipelines.values():
        assert pipeline["exporters"] == ["otlp/forward"]


def test_the_leg_can_be_compressed_or_not_on_either_transport() -> None:
    """`none` is for a receiver that does not unwrap gzip; the symptom otherwise is a `400` about a
    malformed payload, which reads as *our telemetry is wrong*."""
    forward = yaml.safe_load(FORWARD.read_text(encoding="utf-8"))["exporters"]

    for name in BOTH_EXPORTERS:
        assert "${env:AIRA_OTEL_FORWARD_COMPRESSION" in str(forward[name]["compression"])


def test_mutual_tls_is_available_on_either_transport() -> None:
    """Some receivers authenticate a sender by certificate instead of by a header. Without this the
    only answer was a credential in a header, or none."""
    forward = yaml.safe_load(FORWARD.read_text(encoding="utf-8"))["exporters"]

    for name in BOTH_EXPORTERS:
        assert {"cert_file", "key_file"} <= set(forward[name]["tls"])


# --- the two ways this was reported as "nothing arrives" -----------------------------------------


def test_the_inspector_answers_to_the_name_the_rest_of_the_stack_is_spelled_with() -> None:
    """**One letter should not be the difference between a working leg and an empty screen.**

    Every other observability service is `otel-…` — `otel-collector`, `otel-lgtm` — and this one is
    `otlp-`, because it inspects OTLP. Reported from use: the forwarding endpoint was written
    `http://otel-inspector:4318` from memory, the name did not resolve, the collector kept running,
    and the page stayed empty. An alias is cheaper than being right about which name is better.
    """
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"]["otlp-inspector"]

    assert "otel-inspector" in service["networks"]["aira"]["aliases"]


def test_the_status_tool_reads_what_an_exporter_is_holding() -> None:
    """`send_failed` is a **give-up**, and an exporter retrying a host that does not resolve never
    gives up inside the window — so the table read `undelivered 0` and `make otel-status` said
    *"nothing is being lost"* while nothing at all was arriving.

    A queue that is not draining is the earliest honest signal, and it is per exporter, so it names
    which destination rather than only that something is wrong.
    """
    status = (ROOT / "tools" / "otel_status.py").read_text(encoding="utf-8")

    assert "otelcol_exporter_queue_size" in status
    # And the verdict must be reached *after* it: a summary that returns before looking is the
    # defect this replaced.
    assert status.index("_queued(body)") < status.index("Nothing is being lost")


def test_the_inspector_is_reachable_from_off_the_machine_and_says_so() -> None:
    """**The container name is not an address anywhere else, and the ports differ.**

    Reported: the receiver was started, the collector was on another machine, and nothing arrived.
    Every instruction this repository gave said `http://otlp-inspector:4318` — a Docker name and
    the *container's* port, both of which exist only inside one stack's network. From elsewhere it
    is the host on the **published** port, and the same port serves the page and OTLP, so a browser
    somewhere else and a collector somewhere else want the same URL.

    Asserted on the publication rather than on prose: the page port and the OTLP port have to be
    one, or the sentence above stops being true and nothing says so.
    """
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    published = compose["services"]["otlp-inspector"]["ports"]

    assert all(str(entry).endswith(":4318") for entry in published), (
        "the published port must map to the container's 4318 — the page and "
        "/v1/{traces,logs,metrics} are one server, and the documentation says so"
    )
    assert any("AIRA_PUBLISH_OTLP_INSPECTOR_PORT" in str(entry) for entry in published)


def test_the_documentation_gives_the_address_for_a_collector_elsewhere() -> None:
    """One address is not enough, and the missing one is the one that fails silently: a name the
    other machine cannot resolve leaves the collector running and the page empty."""
    integrations = (ROOT / "docs" / "INTEGRATIONS.md").read_text(encoding="utf-8")

    assert "otlp-inspector:4318" in integrations, "the in-network address"
    assert "anywhere else" in integrations, "and the one for a collector that is not on this host"
