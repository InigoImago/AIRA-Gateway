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
It also reads the collector's log for the **reason** a delivery failed, which the counters cannot
give — carried here when the laboratory overlay was folded into the reference stack.
"""

from __future__ import annotations

import os
import re
import subprocess
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

#: Telemetry an exporter is holding because the far end will not take it.
#:
#: **The counter that says "stuck" rather than "lost".** `send_failed` is a *give-up*, and an
#: exporter retrying a host that does not resolve never gives up inside a five-minute window — so
#: the table below read `undelivered 0` and this tool said *"nothing is being lost"* while nothing
#: at all was arriving. Reported from use, with one letter wrong in a hostname
#: (`otel-inspector` for `otlp-inspector`): the collector ran, the counters looked clean, and the
#: page stayed empty.
#:
#: A queue that is not draining is the earliest honest signal there is, and it is per exporter, so
#: it names *which* destination rather than only that something is wrong.
QUEUED = re.compile(
    r'^otelcol_exporter_queue_size\{[^}]*data_type="(?P<signal>[^"]+)"[^}]*'
    r'exporter="(?P<exporter>[^"]+)"[^}]*\}\s+(?P<value>[0-9.e+]+)',
    re.MULTILINE,
)


def _totals(body: str, pattern: re.Pattern[str]) -> dict[str, float]:
    """Summed per signal, across receivers/exporters.

    Per **signal** and not per component on purpose: this answers *is telemetry getting through*,
    and a reader who needs to know which of several exporters is losing it has the raw endpoint.
    One table that fits on a screen beats one that is complete.
    """
    totals: dict[str, float] = {}
    for signal, _labels, value in pattern.findall(body):
        totals[signal] = totals.get(signal, 0.0) + float(value)
    return totals


CONTAINER = f"{os.environ.get('AIRA_STACK', 'aira')}-otel-collector"


#: The reason, inside the collector's structured log line. Extracted rather than printed with the
#: line, because the line begins with a timestamp, a file position and a resource block — and the
#: first draft of this tool truncated at 200 characters and cut the reason off. What a reader needs
#: is `no such host`, not the first two hundred characters of a log format.
_REASON = re.compile(r'"error":\s*"((?:[^"\\]|\\.)*)"')


def _recent_failures(lines: int = 3) -> list[str]:
    """The collector's own words about why. Its log is the only place the *reason* exists."""
    try:
        result = subprocess.run(  # noqa: S603
            ["docker", "logs", "--tail", "400", CONTAINER],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return []
    reasons: list[str] = []
    for line in (result.stdout + result.stderr).splitlines():
        if "Exporting failed" not in line and "Dropping data" not in line:
            continue
        match = _REASON.search(line)
        reasons.append(match.group(1).replace('\\"', '"') if match else line.strip())
    # De-duplicated, newest last: a retry loop produces the same sentence every few seconds, and
    # five identical lines say no more than one while hiding a second, different failure.
    unique: list[str] = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return unique[-lines:]


def _fetch(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        print(f"No collector metrics on {url} ({exc}).")
        print("The observability profile publishes them:  make up")
        return None


def _queued(body: str) -> dict[str, float]:
    """How much each exporter is holding, summed over its signals."""
    held: dict[str, float] = {}
    for match in QUEUED.finditer(body):
        value = float(match.group("value"))
        if value:
            held[match.group("exporter")] = held.get(match.group("exporter"), 0.0) + value
    return held


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

    # **The state the four columns above cannot show.** An exporter whose far end does not resolve
    # retries rather than gives up, so `undelivered` stays 0 — and this tool used to conclude
    # "nothing is being lost" while nothing was arriving anywhere. What it is holding says so.
    held = _queued(body)
    if held:
        print()
        for exporter, amount in sorted(held.items()):
            print(
                f"{exporter} is holding {amount:.0f} batch(es) it has not been able to deliver. "
                "That is a destination refusing, unreachable, or named wrongly — not a give-up, "
                "so the column above still reads 0."
            )
        failures = _recent_failures()
        if failures:
            print("\nThe collector's own words:")
            for line in failures:
                print(f"  {line}")
        return 1

    if not losing:
        print("\nNothing is being lost between the applications and the collector's exporters.")
        return 0

    # **The counters say how many; only the log says why.** Carried here when `lab_status.py` was
    # folded in — a table of four numbers that ends in "something is wrong, go and look" is one
    # that makes its reader do the last step by hand, every time.
    failures = _recent_failures()
    if failures:
        print("\nWhy, in the collector's own words:")
        for reason in failures:
            print(f"  {reason}")
    else:
        print("\nSomething is being dropped and no reason is in the recent log — look further")
        print("back with:  make otel-arrivals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
