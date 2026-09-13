"""Splunk's HTTP Event Collector as a third transport, on either channel (`FRD-621`).

OTLP/JSON is one nested document per batch, with the attributes three levels down as key/value
pairs; Splunk indexes one event per record. The collector's `splunk_hec` exporter does that
conversion, so a Splunk destination is a **transport** fragment — the same kind of file as
`forward-grpc.yaml` and `backend-http.yaml` — and not an encoding of the OTLP leg.

What is guarded here is what a merged configuration hides and `otelcol validate` passes: which
pipelines a fragment moves (a merged list replaces), which channel's variables its exporters read,
and the fallbacks that let the collector start when a variable is forgotten.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OTEL = ROOT / "deploy" / "compose" / "otel"
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"

BACKEND, FORWARD = "AIRA_OTEL_BACKEND_", "AIRA_OTEL_FORWARD_"

#: Per channel: its fragment, the exporter for spans and logs, the one for metrics, and every
#: pipeline the fragment moves with the exact list that pipeline must end up with.
#:
#: The observability channel's lists keep `debug` and `file/arrived`, because a merged list
#: replaces and those two are what `make otel-arrivals` and `AIRA_OTEL_ARRIVED_FILE` read. The
#: delivery channel names only its own three pipelines, because a base pipeline named there would
#: move the trace backend onto Splunk.
CHANNELS = {
    BACKEND: (
        OTEL / "collector-backend-splunk-hec.yaml",
        "splunk_hec/backend",
        "splunk_hec/backend_metrics",
        {
            "traces": ["splunk_hec/backend", "debug", "file/arrived"],
            "logs": ["splunk_hec/backend", "debug", "file/arrived"],
            "metrics": ["splunk_hec/backend_metrics", "debug", "file/arrived"],
        },
    ),
    FORWARD: (
        OTEL / "collector-forward-splunk-hec.yaml",
        "splunk_hec/forward",
        "splunk_hec/forward_metrics",
        {
            "traces/siem": ["splunk_hec/forward"],
            "logs/siem": ["splunk_hec/forward"],
            "metrics/siem": ["splunk_hec/forward_metrics"],
        },
    ),
}

PREFIXES = list(CHANNELS)


def _document(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _exporters(prefix: str) -> dict[str, dict]:
    return _document(CHANNELS[prefix][0])["exporters"]


def _collector_environment() -> dict[str, str]:
    return _document(COMPOSE)["services"]["otel-collector"]["environment"]


@pytest.mark.parametrize("prefix", PREFIXES)
def test_the_fragment_defines_its_exporters_and_moves_only_its_channels_pipelines(
    prefix: str,
) -> None:
    """**A merged pipeline list replaces**, so a pipeline this fragment forgets stays on OTLP — half
    the signals in Splunk and half elsewhere, which reads as *Splunk is dropping things* — and a
    pipeline of the other channel named here moves that channel too."""
    path, events, metrics, pipelines = CHANNELS[prefix]
    document = _document(path)

    assert set(document) == {"exporters", "service"}
    assert set(document["exporters"]) == {events, metrics}, (
        "this channel's two and nothing else: a processor or a third exporter would make the HEC "
        "leg differ from the OTLP leg in ways nobody chose"
    )
    assert set(document["service"]) == {"pipelines"}, (
        "`service::extensions` is a list and a merged list replaces — the base owns it"
    )
    assert {name: p["exporters"] for name, p in document["service"]["pipelines"].items()} == (
        pipelines
    )


@pytest.mark.parametrize("prefix", PREFIXES)
def test_metrics_go_to_their_own_index_and_differ_in_nothing_else(prefix: str) -> None:
    """**`mstats` reads a metrics index and nothing else, and one `index` cannot be both kinds** —
    so metrics have an exporter of their own. Anything else that differed between the two would be
    a second configuration nobody chose: a metrics leg with another endpoint, token or CA."""
    _, events, metrics, _ = CHANNELS[prefix]
    exporters = _exporters(prefix)
    spans, numbers = dict(exporters[events]), dict(exporters[metrics])

    assert spans.pop("index") == f"${{env:{prefix}HEC_INDEX}}"
    assert numbers.pop("index") == f"${{env:{prefix}HEC_METRICS_INDEX}}"
    assert spans == numbers


@pytest.mark.parametrize("prefix", PREFIXES)
def test_the_exporters_read_their_own_channels_variables_and_no_others(prefix: str) -> None:
    """The two fragments are one file with the prefix changed, which is exactly how a copy keeps a
    variable of the wrong channel: the observability leg would then take the SIEM's token, or its
    CA, and `validate` would pass because both variables exist."""
    read = re.findall(r"\$\{env:([A-Z0-9_]+)", str(_exporters(prefix)))

    assert read, "the exporters read no variables at all"
    assert sorted(name for name in read if not name.startswith(prefix)) == []


@pytest.mark.parametrize("prefix", PREFIXES)
def test_the_token_is_the_credential_and_no_auth_fragment_applies(prefix: str) -> None:
    """HEC authenticates by `Authorization: Splunk <token>`, which the exporter sets from `token`.
    An `auth:` block here would be a second credential on the same request — and the auth
    fragments attach to the OTLP exporters only, so selecting one beside this is harmless."""
    for exporter in _exporters(prefix).values():
        assert "auth" not in exporter
        assert exporter["token"] == f"${{env:{prefix}HEC_TOKEN}}"


@pytest.mark.parametrize("prefix", PREFIXES)
def test_the_hec_leg_has_the_tls_queue_and_retry_the_otlp_leg_has(prefix: str) -> None:
    """A Splunk behind an internal CA, or one that authenticates the sender by certificate, is the
    ordinary case; and a destination that is down must hold records rather than drop them. The
    same variables drive both legs, so switching transport changes none of it."""
    for exporter in _exporters(prefix).values():
        assert {"ca_file", "cert_file", "key_file", "insecure_skip_verify"} <= set(exporter["tls"])
        assert exporter["sending_queue"]["enabled"] is True
        assert exporter["sending_queue"]["queue_size"] == f"${{env:{prefix}QUEUE}}"
        assert exporter["retry_on_failure"]["enabled"] is True


@pytest.mark.parametrize("prefix", PREFIXES)
def test_a_forgotten_endpoint_or_token_still_lets_the_collector_start(prefix: str) -> None:
    """**Compose passes an empty string for an unset variable, and an empty string overrides a
    `${env:…:-default}` inside the collector**, so the fallback is spelled at the Compose end.

    The endpoint falls back to a name that never resolves (RFC 2606): the collector starts, these
    exporters fail by name in `make otel-status`, Grafana keeps working. The token falls back to
    the name of the variable, which is what Splunk quotes back in its refusal."""
    environment = _collector_environment()
    endpoint = environment[f"{prefix}HEC_ENDPOINT"]
    token = environment[f"{prefix}HEC_TOKEN"]

    assert ".invalid" in endpoint and "/services/collector" in endpoint, endpoint
    assert token.endswith(f":-{prefix}HEC_TOKEN-is-not-set}}"), token
