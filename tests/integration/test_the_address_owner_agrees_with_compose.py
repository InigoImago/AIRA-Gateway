"""`tools/stack_addresses.py` and `docker compose` resolve the same port from the same variables.

The owner module's whole promise is that everything talking to the stack goes where the stack
actually is. That promise rests on it reproducing Compose's resolution — environment, then the env
file, then the written default — and reproducing it is not the same as intending to.

The first version did not. Compose writes `${AIRA_PUBLISH_GATEWAY_PORT:-${AIRA_GATEWAY_PORT:-8001}}`
for the three application services: the inner name predates the `AIRA_PUBLISH_` family and is still
what `docs/SETUP.md` tells a reader to set. The module read only the outer one, so a reader who
followed the documentation got a stack published on their chosen port and every tool still asking
for 8001 — the exact defect the module exists to prevent, committed by the module itself, on the
day it was written.

Asked of Compose rather than reasoned about, because reasoning about it is what produced the bug.
It lives in the integration layer because it needs the `docker compose` binary, and it needs no
running stack: `config` renders the model and exits.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import stack_addresses

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(ROOT / "deploy" / "compose" / "docker-compose.yml"),
    "-f",
    str(ROOT / "deploy" / "compose" / "docker-compose.apps.yml"),
    # Every profile, or the model omits the services behind one — `ollama` lives behind `verify`
    # and its absence looked like a disagreement rather than a service that was simply not
    # rendered. The same list `COMPOSE_ALL` uses in the Makefile, and the same reason it is written
    # out there rather than passed as `--profile "*"`.
    "--profile",
    "observability",
    "--profile",
    "demo",
    "--profile",
    "verify",
    "config",
    "--format",
    "json",
]

#: Which container port identifies each service in the rendered model. Taken from the service's own
#: listen port, which is fixed — only the *published* side is configurable.
CONTAINER_PORT = {
    "gateway": 8001,
    "management": 8002,
    "console": 8080,
    "keycloak": 8080,
    "postgres": 5432,
    "vault": 8200,
    "ollama": 11434,
}

#: One case per shape of the resolution, including the two that were wrong.
CASES = [
    pytest.param({}, id="nothing-set"),
    pytest.param({"AIRA_PUBLISH_GATEWAY_PORT": "17001"}, id="the-current-name"),
    pytest.param({"AIRA_GATEWAY_PORT": "19001"}, id="the-legacy-name-SETUP-documents"),
    pytest.param(
        {"AIRA_PUBLISH_GATEWAY_PORT": "17001", "AIRA_GATEWAY_PORT": "19001"},
        id="both-set-outer-wins",
    ),
    pytest.param({"AIRA_PUBLISH_KEYCLOAK_PORT": "18080"}, id="a-service-with-no-legacy-name"),
]


def _compose_published(environment: dict[str, str]) -> dict[str, set[str]]:
    """Every published port in the rendered model, keyed by the service name Compose gives it."""
    result = subprocess.run(
        COMPOSE, cwd=ROOT, capture_output=True, text=True, env={**os.environ, **environment}
    )
    if result.returncode != 0:  # pragma: no cover - reported rather than swallowed
        pytest.skip(f"docker compose config failed: {result.stderr[:200]}")
    model = json.loads(result.stdout)
    published: dict[str, set[str]] = {}
    for name, service in model.get("services", {}).items():
        for entry in service.get("ports", []) or []:
            if entry.get("published") is not None:
                published.setdefault(name, set()).add(f"{entry['published']}:{entry.get('target')}")
    return published


@pytest.mark.parametrize("environment", CASES)
def test_the_owner_names_the_port_compose_publishes(
    environment: dict[str, str], monkeypatch
) -> None:
    published = _compose_published(environment)
    rendered = {entry for entries in published.values() for entry in entries}

    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    mismatches: list[str] = []
    for service, container_port in CONTAINER_PORT.items():
        ours = stack_addresses.port(service)
        if f"{ours}:{container_port}" not in rendered:
            mismatches.append(
                f"{service}: the owner says {ours} → {container_port}; "
                f"Compose publishes "
                f"{sorted(e for e in rendered if e.endswith(f':{container_port}'))}"
            )

    assert not mismatches, (
        f"With {environment or 'nothing set'}, the address owner and Compose disagree:\n  "
        + "\n  ".join(mismatches)
        + "\n\nEverything that talks to this stack asks the owner. A disagreement here is every "
        "tool and both upper test layers pointing at a port with nothing behind it."
    )
