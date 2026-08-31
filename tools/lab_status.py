"""Did the laboratory endpoint receive anything, and if not, why not.

**"No errors" and "it arrived" are different statements**, and only one of them can be read off a
log. The collector logs a delivery *failure* — with the endpoint and a growing retry interval — and
says nothing whatsoever about a success, at any log level. So a reader watching `docker logs` for
reassurance is watching for the absence of something, which is indistinguishable from nothing
having been sent at all.

Its own metrics answer the question properly: `otelcol_exporter_sent_*` against
`otelcol_exporter_send_failed_*`, **per exporter**. This prints both, and then the recent failure
lines underneath — because a zero in the first is explained by the second, and reporting one
without the other is how somebody concludes their SIEM is fine.

    make lab-status

Reads the metrics port the laboratory overlay publishes (`LAB_METRICS_PORT`, default 8889). Without
the overlay running there is nothing to ask, and it says so rather than failing.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

#: The two families worth reading. Logs and metrics have their own counters; a SIEM feed that is
#: silently dropping one signal while delivering another is exactly the shape worth seeing.
SENT = re.compile(r"^otelcol_exporter_sent_(\w+)\{([^}]*)\}\s+([0-9.e+]+)", re.MULTILINE)
FAILED = re.compile(r"^otelcol_exporter_send_failed_(\w+)\{([^}]*)\}\s+([0-9.e+]+)", re.MULTILINE)

METRICS_PORT = os.environ.get("LAB_METRICS_PORT", "8889")
CONTAINER = f"{os.environ.get('AIRA_STACK', 'aira')}-otel-collector"

#: The exporter the overlay adds. Named so that its **absence** can be reported — see the table.
LAB_EXPORTER = "otlphttp/lab"


def _exporter(labels: str) -> str:
    match = re.search(r'exporter="([^"]+)"', labels)
    return match.group(1) if match else "?"


def _counts(body: str, pattern: re.Pattern[str]) -> dict[tuple[str, str], float]:
    return {
        (_exporter(labels), signal): float(value) for signal, labels, value in pattern.findall(body)
    }


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


def main() -> int:
    url = f"http://localhost:{METRICS_PORT}/metrics"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        print(f"No collector metrics on {url} ({exc}).")
        print("The laboratory overlay publishes them; start it with:")
        print("  make up-lab LAB_SIEM_ENDPOINT=http://your-endpoint:4318")
        return 1

    sent, failed = _counts(body, SENT), _counts(body, FAILED)
    exporters = sorted({*sent, *failed})
    if not exporters:
        print("The collector has exported nothing yet — send a request through the gateway first.")
        return 0

    print(f"{'exporter':22} {'signal':10} {'delivered':>10} {'failed':>8}")
    for exporter, signal in exporters:
        delivered = sent.get((exporter, signal), 0.0)
        lost = failed.get((exporter, signal), 0.0)
        mark = "  <-- some lost" if lost else ""
        print(f"{exporter:22} {signal:10} {delivered:10.0f} {lost:8.0f}{mark}")

    failures = _recent_failures()

    # **An exporter that has delivered nothing is missing from the table, not zero in it.**
    # `send_failed_*` counts a *give-up*, and the retry sender does not give up until
    # `max_elapsed_time` — so for the first five minutes an endpoint that does not exist produces
    # no row at all, and a reader has to notice an absence. Absence is the one thing a table cannot
    # show, so it is said in words.
    if LAB_EXPORTER not in {exporter for exporter, _ in exporters}:
        print(f"\n{LAB_EXPORTER} has delivered nothing.")
        print(
            "It is still retrying — `send_failed` counts a give-up, not an attempt."
            if failures
            else "Nothing has gone through it yet either; produce some traffic first."
        )

    if failures:
        # Printed whenever there are any, not only when the count is zero: a feed that delivers
        # *and* fails is a feed that is losing some of it, and that is the case nobody looks for.
        print("\nWhy, in the collector's own words:")
        for reason in failures:
            print(f"  {reason}")
    elif any(failed.values()):
        print("\nFailures are counted but none is in the last 400 log lines — look further back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
