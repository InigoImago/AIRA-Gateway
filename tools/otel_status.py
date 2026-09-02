"""Did it arrive — at the collector, and then wherever the collector sends it (`FRD-617`).

**The question `AIRA_DEBUG_INTEGRATIONS=otel` cannot answer from inside a service.** That channel
reports one thing well: the export left, how long it took, and what the next hop answered. What it
cannot see is what happened *after* the HTTP response — and OTLP makes that gap wider than it
looks, because a collector can answer **200 with a body saying it dropped half the batch**
(`partial_success`). The exporter reads `resp.ok` and returns `SUCCESS`.

So the chain has three hops and each is measured somewhere else:

    application ──▶ collector ──▶ Grafana / your SIEM
       │                │                   │
       │                │                   └── otelcol_exporter_sent_* / send_failed_*
       │                └── otelcol_receiver_accepted_* / refused_*
       └── AIRA_DEBUG_INTEGRATIONS=otel  (and now the rejected count, from the response body)

This prints the two the collector knows, side by side, per signal. A number that does not match the
one before it is where the telemetry is going missing.

    make otel-status

Reads the collector's own Prometheus endpoint (`AIRA_PUBLISH_OTLP_METRICS_PORT`, default 8889).
`make lab-status` is the same question for the laboratory overlay's second destination, and stays
because it also reads the collector's log for the *reason* a delivery failed.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request

import stack_addresses

#: What the collector took in, and what it turned away. `refused` is a *rejection*, which is the
#: number an application's own `SUCCESS` can be hiding.
ACCEPTED = re.compile(r"^otelcol_receiver_accepted_(\w+)\{([^}]*)\}\s+([0-9.e+]+)", re.MULTILINE)
REFUSED = re.compile(r"^otelcol_receiver_refused_(\w+)\{([^}]*)\}\s+([0-9.e+]+)", re.MULTILINE)

#: And what it managed to pass on. The same two families `tools/lab_status.py` reads.
SENT = re.compile(r"^otelcol_exporter_sent_(\w+)\{([^}]*)\}\s+([0-9.e+]+)", re.MULTILINE)
FAILED = re.compile(r"^otelcol_exporter_send_failed_(\w+)\{([^}]*)\}\s+([0-9.e+]+)", re.MULTILINE)

#: Spans, log records and data points are counted under their own names; a signal that appears on
#: one side and not the other is exactly the finding, so the union is what gets printed.
SIGNALS = ("spans", "log_records", "metric_points")


def _totals(body: str, pattern: re.Pattern[str]) -> dict[str, float]:
    """Summed per signal, across receivers/exporters.

    Per **signal** and not per component on purpose: this answers *is telemetry getting through*,
    and a reader who needs to know which of two exporters is losing it has `make lab-status` and
    the raw endpoint. One table that fits on a screen beats one that is complete.
    """
    totals: dict[str, float] = {}
    for signal, _labels, value in pattern.findall(body):
        totals[signal] = totals.get(signal, 0.0) + float(value)
    return totals


def _fetch(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        print(f"No collector metrics on {url} ({exc}).")
        print("The observability profile publishes them:  make up")
        return None


def main() -> int:
    base = stack_addresses.url("otlp_metrics")
    body = _fetch(f"{base}/metrics")
    if body is None:
        return 1

    accepted, refused = _totals(body, ACCEPTED), _totals(body, REFUSED)
    sent, failed = _totals(body, SENT), _totals(body, FAILED)
    signals = sorted({*accepted, *refused, *sent, *failed} or set(SIGNALS))

    if not (accepted or sent):
        print("The collector has received nothing yet.")
        print("Send a request through the gateway, and check AIRA_OTEL_ENABLED=true.")
        return 0

    print(f"{'signal':14} {'accepted':>10} {'refused':>9} {'forwarded':>10} {'undelivered':>12}")
    losing = False
    for signal in signals:
        took = accepted.get(signal, 0.0)
        turned_away = refused.get(signal, 0.0)
        onward = sent.get(signal, 0.0)
        lost = failed.get(signal, 0.0)
        marks = []
        if turned_away:
            marks.append("collector refused some")
        if lost:
            marks.append("delivery gave up on some")
        losing = losing or bool(marks)
        note = ("   <-- " + "; ".join(marks)) if marks else ""
        print(f"{signal:14} {took:10.0f} {turned_away:9.0f} {onward:10.0f} {lost:12.0f}{note}")

    # **Said, because a table of four numbers does not interpret itself.** `forwarded` counts one
    # entry per exporter, so a stack fanning out to two destinations doubles it — a reader
    # comparing it to `accepted` and finding twice as many would otherwise go looking for a bug.
    print()
    print("accepted vs refused  — what reached the collector, and what it turned away.")
    print("forwarded            — summed across exporters, so fan-out counts more than once.")
    print("undelivered          — a give-up, not an attempt: a retrying exporter shows 0 for now.")
    if not losing:
        print("\nNothing is being lost between the applications and the collector's exporters.")
    else:
        print("\nSomething is being dropped — the collector's log says why:  make lab-logs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
