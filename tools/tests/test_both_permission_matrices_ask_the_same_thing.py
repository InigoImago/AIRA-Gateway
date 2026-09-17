"""The hermetic matrix and the live one probe each gateway permission the same way.

Two suites state one rule. `gateway/tests/test_the_pairwise_permission_matrix.py` runs every pair
of permissions against a `TestClient`; `tests/integration/test_the_permission_matrix.py` runs a
growing role against the running stack. Both hold a hand-written list of *which endpoint a
permission opens*, and on 2026-09-17 they disagreed: listing suspensions became
`anomaly.read_all`'s as well, the hermetic probe was moved to **stopping** traffic, and the live one
was not. The hermetic suite went green and CI's integration stage failed twice — the shape
`LESSONS.md` §3 already names, with a test suite as the second place.

So the endpoints are compared here, by permission: whichever HTTP path each matrix uses as the
probe for a gateway permission must be the same path in both. What each does with the answer is
each suite's own business — one drives a client in-process and the other a real server.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERMETIC = ROOT / "gateway/tests/test_the_pairwise_permission_matrix.py"
LIVE = ROOT / "tests/integration/test_the_permission_matrix.py"

#: Each probe names its permission and then calls a helper with the path it asks.
PERMISSION = re.compile(r"P\.([A-Z_]+),")
CALL = re.compile(r"(_[a-z_]+)\(\s*(?:\"([A-Z]+)\",\s*)?\"(/[^\"]*)\"")

#: Which HTTP method each helper sends. A helper missing here fails the test rather than being
#: guessed at: the method is half of what a probe asks.
METHOD = {
    "_status": "GET",
    "_in_scope": "GET",
    "_gateway": "GET",
    "_refuses_body": "POST",
    "_gateway_write": "POST",
}

#: Placeholders each suite fills in differently: a slug or a row it created.
NORMALISE = (
    (re.compile(r"\{ctx\.[a-z_]+\}"), "X"),
    (re.compile(r"probe|row-1"), "X"),
    (re.compile(r"/models/[^:/]+"), "/models/X"),
)

#: How far after a permission its probe's call may sit.
WINDOW = 400


def _probes(path: Path) -> dict[str, set[str]]:
    """Permission → the gateway probes that file asks, each as `METHOD /path`.

    The gateway's only: the live matrix probes Management as well, and its half is compared with
    Management's own matrix by the suite that owns it.
    """
    source = path.read_text(encoding="utf-8")
    found: dict[str, set[str]] = {}
    unknown: list[str] = []
    for match in PERMISSION.finditer(source):
        window = PERMISSION.split(source[match.end() : match.end() + WINDOW])[0]
        call = CALL.search(window)
        if not call:
            continue
        helper, explicit, url = call.groups()
        if helper == "_mgmt":
            method = explicit or ""
        elif helper in METHOD:
            method = METHOD[helper]
        else:
            unknown.append(f"{path.name}: {helper}")
            continue
        if not url.startswith("/v1beta/"):
            continue
        for pattern, replacement in NORMALISE:
            url = pattern.sub(replacement, url)
        found.setdefault(match.group(1), set()).add(f"{method} {url}")
    assert not unknown, f"probe helpers this check does not know the method of: {unknown}"
    return found


def test_both_matrices_are_readable() -> None:
    """A regex that parses nothing would make the comparison below vacuously true."""
    hermetic, live = _probes(HERMETIC), _probes(LIVE)
    assert len(hermetic) >= 8, hermetic
    assert len(live) >= 8, live
    assert hermetic["INCIDENT_SUSPEND"] == {"POST /v1beta/suspensions"}
    assert live["INCIDENT_SUSPEND"] == {"POST /v1beta/suspensions"}


def test_a_permission_is_probed_at_the_same_endpoint_in_both() -> None:
    hermetic, live = _probes(HERMETIC), _probes(LIVE)
    shared = sorted(set(hermetic) & set(live))
    differing = [
        f"  {permission}: hermetic {sorted(hermetic[permission])} · live {sorted(live[permission])}"
        for permission in shared
        if hermetic[permission] != live[permission]
    ]
    assert not differing, (
        "These permissions are probed differently by the two matrices, so one of them is checking "
        "a rule that has moved:\n" + "\n".join(differing)
    )
