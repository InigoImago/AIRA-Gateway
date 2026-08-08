"""Typed application settings for the management backend.

Reuses the shared :class:`aira_common.config.BaseAiraSettings` so configuration is
consistent with the gateway. Django's ``settings.py`` reads values from an instance of
:class:`ManagementSettings`. Secrets default to dev-only values and are sourced from
Vault in real deployments (see PRD §9).
"""

from __future__ import annotations

from aira_common.config import BaseAiraSettings

# The well-known development signing key. ``config.security`` refuses to start any non-local
# environment that is still using it.
DEV_SECRET_KEY = "dev-insecure-secret-key-change-me"  # noqa: S105


class ManagementSettings(BaseAiraSettings):
    """Configuration for the Management backend service."""

    app_name: str = "aira-management"

    # Django core (dev defaults; real secrets come from Vault)
    secret_key: str = DEV_SECRET_KEY
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

    # Use an in-memory SQLite DB (set by the test harness) instead of Postgres.
    test_database: bool = False

    # OIDC (Keycloak) — the Angular SPA sends a bearer JWT that the API validates.
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_uri: str = ""

    # Directory search (`FRD-209`). A **read-only** service account with `view-users` and
    # `query-groups` on the realm — the least it can be given. Absent by default: without it the
    # console falls back to what Management already knows and says so, rather than pretending the
    # search is complete.
    directory_client_id: str = ""
    directory_client_secret: str = ""

    @property
    def oidc_issuer_base(self) -> str:
        """The Keycloak root, derived from the issuer (`.../realms/<realm>`).

        Derived rather than configured separately: two settings for one server is two settings to
        get out of step, and the admin API lives beside the realm the issuer already names.
        """
        issuer = self.oidc_issuer.rstrip("/")
        marker = "/realms/"
        return issuer.split(marker)[0] if marker in issuer else issuer

    @property
    def oidc_realm(self) -> str:
        """The realm name, from the issuer."""
        issuer = self.oidc_issuer.rstrip("/")
        marker = "/realms/"
        return issuer.split(marker)[-1] if marker in issuer else ""

    def jwks_uri(self) -> str:
        """Return the JWKS URI, deriving it from the issuer when not set explicitly."""
        if self.oidc_jwks_uri:
            return self.oidc_jwks_uri
        return f"{self.oidc_issuer.rstrip('/')}/protocol/openid-connect/certs"

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
