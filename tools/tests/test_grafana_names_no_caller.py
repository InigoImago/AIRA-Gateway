"""The local Grafana shows no caller to whoever opens it (`FRD-627` FR-13).

It runs without a sign-in, so what its dashboards select and what an anonymous visitor may query
is visible to anybody who reaches the port. Strict data mode keeps a person with Global
Administrator and IT Security; a dashboard listing callers, or an anonymous Editor able to query any
span in Explore, would hand them to everyone.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = sorted((ROOT / "deploy" / "compose" / "grafana" / "dashboards").glob("*.json"))
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"

#: The span attributes that name a person or the machine they called from.
PERSON_ATTRIBUTES = ("aira.subject", "aira.credential", "aira.source_ip")


def test_no_dashboard_selects_a_caller() -> None:
    assert DASHBOARDS
    for dashboard in DASHBOARDS:
        text = json.dumps(json.loads(dashboard.read_text(encoding="utf-8")))
        named = [attribute for attribute in PERSON_ATTRIBUTES if attribute in text]
        assert not named, f"{dashboard.name} selects {named}"


def test_an_anonymous_visitor_is_a_viewer() -> None:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    environment = services["otel-lgtm"]["environment"]

    if str(environment.get("GF_AUTH_ANONYMOUS_ENABLED", "false")).lower() == "true":
        assert environment["GF_AUTH_ANONYMOUS_ORG_ROLE"] == "Viewer"
