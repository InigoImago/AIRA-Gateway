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
    assert {"traces", "traces/siem", "metrics", "logs"} <= set(_pipelines(FORWARD))


def test_the_second_destination_has_its_own_trace_pipeline() -> None:
    """Its own, because it selects. A shared one could only send both destinations the same
    thing — the design that made this a volume problem."""
    siem = _pipelines(FORWARD)["traces/siem"]

    assert siem["exporters"] == [FORWARD_EXPORTER]
    assert "filter/siem" in siem["processors"]


def test_the_grafana_trace_pipeline_does_not_carry_the_filter() -> None:
    """The whole point: everything still reaches the backend a person reads traces in."""
    traces = _pipelines(FORWARD)["traces"]

    assert FORWARD_EXPORTER not in traces["exporters"], (
        "the unfiltered pipeline forwards as well, so the SIEM gets every SQL span after all"
    )
    assert "otlp_grpc/lgtm" in traces["exporters"]


@pytest.mark.parametrize("signal", ["metrics", "logs"])
def test_the_other_signals_still_reach_both(signal: str) -> None:
    """Only traces are selective. Metrics and logs are small and were asked for whole."""
    exporters = _pipelines(FORWARD)[signal]["exporters"]

    assert FORWARD_EXPORTER in exporters
    assert "otlp_grpc/lgtm" in exporters


def test_every_base_exporter_is_repeated_in_the_fragment() -> None:
    """**A merged exporter list replaces, it does not extend.** Leaving one out silently unhooks
    it — Grafana, or the arrivals file — and nothing says so, because a pipeline with fewer
    exporters is a valid pipeline.
    """
    base, forward = _pipelines(BASE), _pipelines(FORWARD)

    for signal in ("traces", "metrics", "logs"):
        missing = set(base[signal]["exporters"]) - set(forward[signal]["exporters"])
        assert not missing, (
            f"the {signal} pipeline in the fragment drops {sorted(missing)} from the base — a "
            "merged list replaces, so anything not repeated here stops receiving."
        )


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


def test_the_grpc_fragment_moves_every_pipeline_and_keeps_grafana() -> None:
    """A merged exporter list replaces, so a pipeline this fragment forgets keeps sending over
    HTTP — half the signals on one transport and half on the other, which is the kind of state
    that reads as *the receiver is dropping things*."""
    pipelines = _pipelines(GRPC)

    assert set(pipelines) == {"traces", "traces/siem", "metrics", "logs"}
    assert pipelines["traces/siem"]["exporters"] == ["otlp/forward"]
    for signal in ("traces", "metrics", "logs"):
        assert "otlp_grpc/lgtm" in pipelines[signal]["exporters"]
        assert FORWARD_EXPORTER not in pipelines[signal]["exporters"]


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
