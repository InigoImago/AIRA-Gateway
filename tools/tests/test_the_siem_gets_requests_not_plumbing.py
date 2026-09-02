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
