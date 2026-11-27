"""Both planes read the caller's address with one setting and one default (`FRD-156`).

A request through the console passes the same proxies to the gateway and to Management, so both
must name the same ones. Before, Compose handed the hop count to the gateway only and Management
kept its own default behind every proxy of an installation's — two planes, two answers.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APPS = ROOT / "deploy/compose/docker-compose.apps.yml"


def test_compose_hands_both_planes_the_same_list_with_the_same_default() -> None:
    services = yaml.safe_load(APPS.read_text(encoding="utf-8"))["services"]
    given = {
        name: (service.get("environment") or {}).get("AIRA_TRUSTED_PROXIES")
        for name, service in services.items()
        if name in {"gateway", "management"}
    }

    assert given == {
        "gateway": "${AIRA_TRUSTED_PROXIES:-}",
        "management": "${AIRA_TRUSTED_PROXIES:-}",
    }


def test_the_settings_classes_agree_on_the_name_and_the_default() -> None:
    from aira_management.config.app_settings import ManagementSettings

    from aira_gateway.config import GatewaySettings

    gateway = GatewaySettings.model_fields["trusted_proxies"]
    management = ManagementSettings.model_fields["trusted_proxies"]

    assert gateway.default == management.default == ""


def test_neither_plane_declares_the_settings_it_replaced() -> None:
    from aira_management.config.app_settings import ManagementSettings

    from aira_gateway.config import GatewaySettings

    for settings in (GatewaySettings, ManagementSettings):
        assert not {"trust_forwarded_for", "trusted_proxy_hops"} & set(settings.model_fields)
