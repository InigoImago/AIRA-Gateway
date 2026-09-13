"""Two channels, configured the same way — or the second one is an afterthought again.

This collector feeds two independent streams (`FRD-618`): **observability**, every span and metric
and log to a trace backend, and **delivery**, one record per API access and per model access to
whoever consumes that elsewhere. `FRD-618` made the pipelines independent and a guard test in
`test_the_siem_gets_requests_not_plumbing.py` keeps them that way.

It did not make the *configuration* independent, and that is what this file is for. Reported by the
owner: setting the second channel up does not look like setting the first one up. It did not,
measurably — on 2026-09-07 the delivery channel had **seventeen** variables, three `--config` slots
and four credential fragments, and the observability channel had **four** variables, one slot and no
credential at all. So a trace backend behind any authentication, or one that wanted OTLP/JSON, or
one reachable only over HTTP, meant editing a file this repository ships — the exact complaint
`FRD-618` §3.1c had recorded about the endpoint being a literal, one layer up and still open.

The asymmetry is not visible in any single file, which is why it survived two rounds of review: each
half is correct on its own, and only holding the two variable families side by side shows that one
of them is a quarter the size of the other. So that comparison is the test.

`FRD-620` is the round; `docs/CONFIGURATION.md` §5a is what a reader sets.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OTEL = ROOT / "deploy" / "compose" / "otel"
BASE = OTEL / "collector-config.yaml"
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"

BACKEND, FORWARD = "AIRA_OTEL_BACKEND_", "AIRA_OTEL_FORWARD_"

#: The only two names that may differ between the families, and the rule that makes them differ.
#:
#: **`_ENDPOINT` is the channel's default transport; the other transport gets its own name.** The
#: observability channel defaults to gRPC, so its `_ENDPOINT` is `host:port` and `_HTTP_ENDPOINT`
#: is the extra; the delivery channel defaults to HTTP, so its `_ENDPOINT` is a URL and
#: `_GRPC_ENDPOINT` is the extra. `_PLAINTEXT` follows its `_ENDPOINT` for the same reason — it is
#: the gRPC leg's switch, and over HTTP the scheme already says it.
#:
#: Any *other* difference is the asymmetry coming back.
TRANSPORT_SPECIFIC = {
    BACKEND: {"HTTP_ENDPOINT", "PLAINTEXT"},
    FORWARD: {"GRPC_ENDPOINT", "GRPC_PLAINTEXT"},
}


def _document(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _collector_environment() -> dict[str, str]:
    return _document(COMPOSE)["services"]["otel-collector"]["environment"]


def _suffixes(prefix: str) -> set[str]:
    return {
        name[len(prefix) :] for name in _collector_environment() if name.startswith(prefix)
    } - TRANSPORT_SPECIFIC[prefix]


# --- the comparison the reviewer could not make ---------------------------------------------------


def test_the_two_channels_take_the_same_settings() -> None:
    """**The test this file exists for.** Held side by side rather than read one at a time.

    Every knob one channel has, the other has under the same name one prefix along: the transport,
    the encoding, the compression, the credential header, the CA, the client certificate, the
    batching, the queue and the retry — plus the two transport-specific names above. Which knobs
    exist at all is `ADR-0024`'s closed core, listed in
    `test_the_collector_has_a_core_and_recipes.py`.
    """
    backend, forward = _suffixes(BACKEND), _suffixes(FORWARD)

    assert backend == forward, (
        f"the observability channel is missing {sorted(forward - backend)} and the delivery "
        f"channel is missing {sorted(backend - forward)}. A knob on one channel and not the other "
        "is how one of them became the product and the other an addition to it — which is the "
        "report FRD-620 answers, and it was invisible in every file taken on its own."
    )


@pytest.mark.parametrize("prefix", [BACKEND, FORWARD])
def test_each_channel_has_three_config_slots(prefix: str) -> None:
    """**Whether, how, and who** — a destination varies in three ways that do not depend on each
    other, and each is its own `--config` fragment because YAML has no conditional.

    The observability channel had only the first of the three, which is why it could not be reached
    over HTTP and could not send a credential."""
    command = "\n".join(_document(COMPOSE)["services"]["otel-collector"]["command"])

    for slot in ("CONFIG", "PROTOCOL_CONFIG", "AUTH_CONFIG"):
        assert f"${{{prefix}{slot}:-/etc/otelcol-contrib/empty.yaml}}" in command, (
            f"{prefix}{slot} is not a slot, or does not default to the empty fragment — an "
            "unset slot must merge nothing rather than fail to open a file"
        )


def test_switching_the_observability_channel_off_outranks_its_transport() -> None:
    """A merged pipeline **replaces**, so of two fragments naming the same pipeline the later wins.

    `nobackend.yaml` strips the backend exporter out of the base pipelines and `backend-http.yaml`
    puts a different one in, so with the other ordering a stale `_PROTOCOL_CONFIG` quietly turns a
    disabled channel back on — and a channel that is off is not a thing anybody re-checks. Measured
    on 2026-09-07 with both selected: no `*/backend` exporter sent anything, `debug` and
    `file/arrived` kept receiving, and the delivery channel was unaffected.
    """
    command = _document(COMPOSE)["services"]["otel-collector"]["command"]
    position = {flag.split(":-")[0].split("${")[-1]: index for index, flag in enumerate(command)}

    assert position["AIRA_OTEL_BACKEND_PROTOCOL_CONFIG"] < position["AIRA_OTEL_BACKEND_CONFIG"], (
        "the off switch has to be merged last of the two, or it does not switch anything off"
    )
    # And the delivery channel's transport fragment moves pipelines `forward.yaml` defines, so it
    # has to come after. (Selecting it alone names an exporter that does not exist and stops the
    # collector, which is loud, and therefore the acceptable half of this pair.)
    assert position["AIRA_OTEL_FORWARD_CONFIG"] < position["AIRA_OTEL_FORWARD_PROTOCOL_CONFIG"]


# --- the list that replaces, and why no fragment may own it ---------------------------------------


#: Everything the seven `--config` slots can name. `collector-config.yaml` is the base and is not
#: here: it is the one file that *may* name `service::extensions`.
FRAGMENTS = sorted(set(OTEL.glob("collector-*.yaml")) - {BASE})

AUTH_FRAGMENTS = sorted(OTEL.glob("collector-*-auth-*.yaml"))


@pytest.mark.parametrize("path", FRAGMENTS, ids=lambda p: p.stem)
def test_no_fragment_owns_the_extension_list(path: Path) -> None:
    """**`service::extensions` is a list, and a merged list replaces.**

    Measured on 2026-09-07 against collector-contrib 0.157 with an extension named in each of two
    fragments: only the **last** one started. No error, no warning, and `otelcol validate` answered
    `rc=0` throughout — a configured-but-unstarted extension is a perfectly valid configuration.

    While each credential fragment carried its own list there was room for exactly one credential in
    the whole stack, and nothing said so. The moment the observability channel gained credentials of
    its own, selecting one on each channel would have left the first destination anonymous, and the
    symptom would have been a `401` from one of two receivers — which reads as a wrong credential
    rather than an absent one, and sends the reader to check the value they got right.

    So the base configuration declares and lists every authenticator, and a fragment attaches
    `auth:` to exporters and nothing else.
    """
    assert "extensions" not in _document(path).get("service", {}), (
        f"{path.name} names `service::extensions`. A merged list replaces, so this silently "
        "unregisters whatever another fragment registered — with `validate` clean. The base "
        "configuration owns that list; attach `auth:` to the exporters here."
    )


@pytest.mark.parametrize("path", AUTH_FRAGMENTS, ids=lambda p: p.stem)
def test_every_authenticator_a_fragment_names_is_declared_and_started(path: Path) -> None:
    """The other half of the rule above: taking the list away from the fragments only works while
    the base actually carries every name they use. An `auth.authenticator` the service list omits
    is refused at start-up — the good outcome, and still a restart loop that takes every other
    exporter with it."""
    base = _document(BASE)

    for exporter in _document(path)["exporters"].values():
        named = exporter["auth"]["authenticator"]
        assert named in base["extensions"], f"{named} is not declared in the base configuration"
        assert named in base["service"]["extensions"], f"{named} is declared but never started"


@pytest.mark.parametrize("path", AUTH_FRAGMENTS, ids=lambda p: p.stem)
def test_an_auth_fragment_authenticates_its_own_channel_and_only_its_own(path: Path) -> None:
    """A credential is per destination. A fragment that reached the other channel's exporters would
    send a trace backend the SIEM's key — and both channels may be authenticated at once, which is
    the state that makes the mistake possible at all."""
    channel = "backend" if "-backend-" in path.name else "forward"
    other = "forward" if channel == "backend" else "backend"

    exporters = _document(path)["exporters"]

    assert set(exporters) == {f"otlp/{channel}", f"otlphttp/{channel}"}, (
        "both of this channel's transports and neither of the other's: protocol and credential are "
        "chosen by two independent variables, so a fragment that authenticated one transport would "
        f"leave the other anonymous. Got {sorted(exporters)}."
    )
    assert all(other not in name for name in exporters)


@pytest.mark.parametrize("path", AUTH_FRAGMENTS, ids=lambda p: p.stem)
def test_no_auth_fragment_puts_a_variable_in_a_header_name(path: Path) -> None:
    """**`${env:…}` is substituted in a value and not in a key**, and `validate` cannot see the
    difference — a key is a string and that string is a valid one.

    Measured on 2026-09-02: a `headers:` map keyed by `${env:…:-authorization}` validated `rc=0` and
    every export then failed with *invalid header field name "${env:…}"*, in a container log nobody
    was watching. A configurable header name goes through `headers_setter`, where the name sits in a
    value position.
    """
    for exporter in _document(path)["exporters"].values():
        for key in exporter.get("headers") or {}:
            assert "${env:" not in str(key), (
                f"{path.name} keys a header by an environment variable. That validates and does "
                "not work: substitution happens in values, not in keys. Use `headers_setter`."
            )


# --- the fallback that has to be spelled at the Compose end ---------------------------------------


#: The credential field the collector **refuses to build empty**: `headers_setter` answers *missing
#: header source*. The extension is declared on every deployment, so its fallback must exist on
#: every one. Other credentials are recipes, whose fallbacks are inline (`ADR-0024`).
MUST_NOT_BE_EMPTY = ["AUTHORIZATION"]


@pytest.mark.parametrize("prefix", [BACKEND, FORWARD])
@pytest.mark.parametrize("suffix", MUST_NOT_BE_EMPTY)
def test_the_credential_fallbacks_are_spelled_where_they_apply(prefix: str, suffix: str) -> None:
    """**Compose passes an empty string for an unset variable, and an empty string overrides a
    `${env:…:-default}` inside the collector.** So a default written only at the collector end never
    applies — which is the trap `collector-forward.yaml` already records about the endpoint, and
    which this round walked straight into: with the placeholders only in `collector-config.yaml`,
    **all 220 merged configurations failed validation** on 2026-09-07, because the authenticators
    are declared on every deployment and `headers_setter` refuses to be built empty.

    The value is deliberately the **name of the variable that is missing**. A credential fragment
    selected without its values then reaches the destination as
    `AIRA_OTEL_FORWARD_AUTHORIZATION-is-not-set` — legible in `make otlp-inspector` and in the far
    end's own rejection. Before `FRD-620` the same mistake stopped the collector dead, taking
    Grafana with it, while the fragment's documentation promised it would send an empty header.
    """
    name = f"{prefix}{suffix}"
    value = _collector_environment()[name]

    assert value != f"${{{name}:-}}", (
        f"{name} falls back to the empty string, and an empty string overrides the collector's own "
        "default — so the extension is built empty and the collector refuses to start, on every "
        "deployment, because it is declared whether or not anything selects it"
    )
    assert "is-not-set" in value, (
        f"{name}'s fallback should name the variable that is missing, so that what reaches the "
        "destination says which one to set"
    )


# --- and the observability channel can now say all seven things -----------------------------------


BACKEND_EXPORTERS = {"otlp/backend", "otlphttp/backend"}


def test_both_transports_are_defined_for_the_observability_channel() -> None:
    """**Both, always, and not one per fragment** — the arrangement the delivery channel already
    used, for a measured reason: an exporter is validated whether or not a pipeline references it,
    so a fragment that *introduced* one would stop the collector on every deployment that had not
    selected it, and a credential fragment has to attach `auth:` to both because it cannot know
    which transport is in play."""
    exporters = _document(BASE)["exporters"]

    assert set(exporters) >= BACKEND_EXPORTERS
    for name in BACKEND_EXPORTERS:
        assert exporters[name].get("compression"), f"{name} cannot turn compression off"
        assert {"ca_file", "cert_file", "key_file"} <= set(exporters[name]["tls"]), (
            f"{name} cannot name a private CA or present a client certificate"
        )


def test_the_observability_channel_can_choose_its_encoding() -> None:
    """**The report this round started from**, and the one that needed a second transport to be
    answerable at all: OTLP/gRPC is protobuf by definition — the specification defines no JSON over
    gRPC — so while this channel had one transport it had one encoding, and there was nothing for a
    variable to set.

    Driven end to end on 2026-09-07 against a receiver that prints what it got: `application/json`
    with `resourceMetrics`/`stringValue` in the body, then `application/x-protobuf` after flipping
    the variable, while the *other* channel carried the opposite encoding at the same time.
    """
    http = _document(BASE)["exporters"]["otlphttp/backend"]

    assert "${env:AIRA_OTEL_BACKEND_ENCODING" in str(http["encoding"])
    assert "${env:AIRA_OTEL_BACKEND_HTTP_ENDPOINT" in str(http["endpoint"])
    assert not {"traces_endpoint", "logs_endpoint", "metrics_endpoint"} & set(http), (
        "per-signal paths are a recipe's (ADR-0024); the core has one endpoint per transport"
    )


def test_the_two_channels_batch_independently() -> None:
    """`batch` is the observability channel's and `batch/siem` is the delivery channel's, and each
    is driven by the variable that names it. The shared processor was `batch: {}` — the collector's
    defaults, unreachable — which is the same subordination in the other direction: the delivery
    channel could be tuned and the observability channel could not."""
    base = _document(BASE)

    assert "${env:AIRA_OTEL_BACKEND_BATCH_SECONDS" in str(base["processors"]["batch"]["timeout"])
    for pipeline in base["service"]["pipelines"].values():
        assert pipeline["processors"][-1] == "batch"


def test_the_transport_fragment_only_moves_the_pipelines() -> None:
    """The mirror of `test_the_grpc_fragment_only_moves_the_pipelines` for the other channel. If
    this fragment grew an exporter or a processor the two legs would start to differ in ways nobody
    chose, and the transport is not supposed to change what the backend receives.

    The exporter lists must stay **complete**: a merged list replaces, and `debug` and
    `file/arrived` are what `make otel-arrivals` and `AIRA_OTEL_ARRIVED_FILE` read — dropping either
    unhooks a diagnostic rather than a destination, which is the quieter of the two failures.
    """
    document = _document(OTEL / "collector-backend-http.yaml")

    assert set(document) == {"service"}
    assert set(document["service"]) == {"pipelines"}

    pipelines = document["service"]["pipelines"]
    assert set(pipelines) == {"traces", "metrics", "logs"}, (
        "the observability channel's own three, and none of the delivery channel's: naming "
        "`traces/siem` here would move the other channel onto a transport chosen for this one"
    )
    for pipeline in pipelines.values():
        assert pipeline["exporters"] == ["otlphttp/backend", "debug", "file/arrived"]


@pytest.mark.parametrize("path", FRAGMENTS, ids=lambda p: p.stem)
def test_every_selectable_fragment_is_mounted(path: Path) -> None:
    """A fragment a variable can name and no container has is a path that fails at start-up, and the
    failure names a file rather than the variable that chose it."""
    assert f"./otel/{path.name}:" in COMPOSE.read_text(encoding="utf-8")
