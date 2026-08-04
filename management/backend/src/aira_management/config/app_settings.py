"""Typed application settings for the management backend.

Reuses the shared :class:`aira_common.config.BaseAiraSettings` so configuration is
consistent with the gateway. Django's ``settings.py`` reads values from an instance of
:class:`ManagementSettings`. Secrets default to dev-only values and are sourced from
Vault in real deployments (see PRD §9).
"""

from __future__ import annotations

from aira_common.config import BaseAiraSettings


class ManagementSettings(BaseAiraSettings):
    """Configuration for the Management backend service."""

    app_name: str = "aira-management"

    # Django core (dev defaults; real secrets come from Vault)
    secret_key: str = "dev-insecure-secret-key-change-me"  # noqa: S105
    debug: bool = True
    allowed_hosts: str = "*"

    # Postgres (aira_mgmt database from the Compose stack)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "aira_mgmt"
    postgres_user: str = "aira"
    postgres_password: str = "aira-local"  # noqa: S105

    # Kafka event bus
    kafka_bootstrap_servers: str = "localhost:29092"

    @property
    def allowed_hosts_list(self) -> list[str]:
        """Return ``allowed_hosts`` as a list for Django's ``ALLOWED_HOSTS``."""
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def kafka_host_port(self) -> tuple[str, int]:
        """Return the (host, port) of the first Kafka bootstrap server for TCP checks."""
        first = self.kafka_bootstrap_servers.split(",")[0].strip()
        if ":" in first:
            host, _, port = first.rpartition(":")
            return host or "localhost", int(port)
        return first or "localhost", 9092
