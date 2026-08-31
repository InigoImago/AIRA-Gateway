"""`make lab-status` distinguishes *nothing failed* from *something arrived* (`FRD-615`).

The collector logs a delivery **failure** and says nothing whatsoever about a success, at any log
level. A reader watching `docker logs` for reassurance is therefore watching for the *absence* of
something — which is what an endpoint that has never been sent to also looks like. That is the
whole reason this tool reads counters instead of a log, and the whole reason it prints the log
underneath: the counter says whether, and only the log says why.

Two cases are easy to get wrong and are asserted here because a first draft got both:

- **An exporter that has delivered nothing is missing from the table, not zero in it.**
  `send_failed_*` counts a *give-up*, and the retry sender does not give up until
  `max_elapsed_time` — so for the first five minutes an unreachable endpoint produces no row at
  all. Absence is the one thing a table cannot show, so it has to be said in words.
- **The reason is extracted, not truncated.** The first draft printed the first 200 characters of
  the log line, which is a timestamp, a file position and a resource block — everything except
  `no such host`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import lab_status  # noqa: E402

METRICS = """\
# HELP otelcol_exporter_sent_spans Number of spans successfully sent to destination.
otelcol_exporter_sent_spans{exporter="otlp_grpc/lgtm",service_name="otelcol-contrib"} 20
otelcol_exporter_sent_spans{exporter="otlphttp/lab",service_name="otelcol-contrib"} 20
otelcol_exporter_send_failed_spans{exporter="otlphttp/lab",service_name="otelcol-contrib"} 3
otelcol_exporter_sent_metric_points{exporter="otlp_grpc/lgtm"} 9
"""

FAILURE_LINE = (
    "2026-08-31T19:00:33.735Z\tinfo\tinternal/retry_sender.go:133\tExporting failed. Will retry "
    'the request after interval.\t{"resource": {"service.instance.id": "424c3ad5"}, '
    '"otelcol.component.id": "otlphttp/lab", "error": "failed to make an HTTP request: Post '
    '\\"http://t-siem-otel:4318/v1/traces\\": dial tcp: lookup t-siem-otel on 127.0.0.11:53: '
    'no such host", "interval": "24.7s"}'
)


def test_the_counters_are_read_per_exporter_and_signal() -> None:
    sent = lab_status._counts(METRICS, lab_status.SENT)
    failed = lab_status._counts(METRICS, lab_status.FAILED)

    assert sent[("otlphttp/lab", "spans")] == 20
    assert sent[("otlp_grpc/lgtm", "metric_points")] == 9
    assert failed[("otlphttp/lab", "spans")] == 3
    assert ("otlp_grpc/lgtm", "spans") not in failed


def test_a_body_with_no_counters_yields_nothing_rather_than_raising() -> None:
    assert lab_status._counts("# nothing here\n", lab_status.SENT) == {}


def test_the_reason_is_extracted_from_the_log_line() -> None:
    """`no such host` is what a reader needs; it sits at the end of a line that begins with a
    timestamp, a file position and a resource block."""
    match = lab_status._REASON.search(FAILURE_LINE)

    assert match is not None
    reason = match.group(1).replace('\\"', '"')
    assert reason.endswith("no such host")
    assert "t-siem-otel:4318/v1/traces" in reason
    assert "424c3ad5" not in reason, "the resource block is not the reason"


def test_the_exporter_name_is_read_off_the_labels() -> None:
    assert lab_status._exporter('exporter="otlphttp/lab",service_name="x"') == "otlphttp/lab"
    assert lab_status._exporter("no labels at all") == "?"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (METRICS, True),
        (METRICS.replace('exporter="otlphttp/lab"', 'exporter="otlp_grpc/lgtm"'), False),
    ],
    ids=["the lab exporter has delivered", "the lab exporter is absent"],
)
def test_absence_of_the_lab_exporter_is_detectable(body: str, expected: bool) -> None:
    """The condition the tool reports in words, asserted on its own terms.

    Detecting this from counters is the only way: the exporter is configured either way, and
    nothing in the metrics says an exporter exists until it has succeeded or given up.
    """
    present = {exporter for exporter, _ in lab_status._counts(body, lab_status.SENT)}
    assert (lab_status.LAB_EXPORTER in present) is expected


def test_the_tool_names_the_exporter_the_overlay_adds() -> None:
    """A rename in the collector configuration must not leave this reporting about a name nothing
    uses — silently, by finding no row and saying the same thing it says when all is well."""
    config = (ROOT / "deploy" / "compose" / "otel" / "collector-config.lab.yaml").read_text()
    assert f"  {lab_status.LAB_EXPORTER}:" in config, (
        f"{lab_status.LAB_EXPORTER} is not an exporter in the laboratory configuration, so "
        "`make lab-status` reports the absence of something that was never there."
    )
