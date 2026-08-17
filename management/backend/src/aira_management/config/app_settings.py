"""Typed application settings for the management backend.

Reuses the shared :class:`aira_common.config.BaseAiraSettings` so configuration is
consistent with the gateway. Django's ``settings.py`` reads values from an instance of
:class:`ManagementSettings`. Secrets default to dev-only values and are sourced from
Vault in real deployments (see PRD §9).
"""

from __future__ import annotations

from aira_common.config import BaseAiraSettings
from aira_common.kafka import KafkaSecurity
from aira_common.oidc import DEFAULT_CLOCK_SKEW_SECONDS, DEFAULT_EXPIRY_LEEWAY_SECONDS

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

    #: How fast one caller may ask, in DRF's `<n>/<period>` notation (2026-08-15).
    #:
    #: There was no bound at all. Every request here verifies a token against a JWKS and then
    #: reconciles the caller's groups, so an unauthenticated probe is not free — and the gateway
    #: has bounded exactly that since `ADR-0015`, on the argument that a limit keyed by use case or
    #: member cannot bound somebody who has neither. This plane had the same gap and not the
    #: reasoning.
    #:
    #: `user` is deliberately generous: a console screen loads five panels at once and paging
    #: through traces is what the product is *for*, so this is sized to stop a script rather than
    #: to shape ordinary use.
    #:
    #: `throttle_auth_failures` bounds **refusals**, keyed by source address, and is not a DRF
    #: throttle: DRF checks permissions before throttles, so on an API where every view requires
    #: authentication an `AnonRateThrottle` never runs at all. It is applied in the authentication
    #: class instead (`apps.api.attempts`), which is where the expensive part — a JWKS verification
    #: of a token that turns out to be invalid — actually happens. `0` switches it off.
    #:
    #: **Per process, and stated rather than implied.** DRF counts through Django's cache, and no
    #: `CACHES` is configured, so this is `LocMemCache`: N workers admit N × the rate. That is the
    #: same degradation `FallbackTokenBucket` documents on the other plane — *"imprecise across
    #: instances and still prevents that"* — and the same reason it is worth having anyway: the
    #: thing being stopped is one client looping, and a bound that is off by the worker count still
    #: stops it. A deployment that wants it exact points `CACHES` at the Redis it already runs.
    throttle_auth_failures: str = "60/minute"
    throttle_user: str = "600/minute"

    # OIDC (Keycloak) — the Angular SPA sends a bearer JWT that the API validates.
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_uri: str = ""
    #: `FRD-134`, and the same two values as the gateway's — a tolerance that held on one plane and
    #: not the other would sign somebody into the console and refuse the same token at the gateway.
    oidc_clock_skew_seconds: float = DEFAULT_CLOCK_SKEW_SECONDS
    oidc_expiry_leeway_seconds: float = DEFAULT_EXPIRY_LEEWAY_SECONDS

    # How this service authenticates to Kafka (2026-08-09). `PLAINTEXT` keeps the Compose stack
    # working and is **refused outside `local`**: the gateway applies whatever arrives on these
    # topics into the read-model its authorization comes from, so an unauthenticated broker is a
    # way to grant yourself administrator access to any use case without a credential and without
    # an audit row.
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str = ""
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""
    kafka_ssl_cafile: str = ""

    def kafka_security(self) -> KafkaSecurity:
        return KafkaSecurity(
            protocol=self.kafka_security_protocol,
            sasl_mechanism=self.kafka_sasl_mechanism,
            sasl_username=self.kafka_sasl_username,
            sasl_password=self.kafka_sasl_password,
            ssl_cafile=self.kafka_ssl_cafile,
        )

    # Directory search (`FRD-209`). A **read-only** service account with `view-users` and
    # `query-groups` on the realm — the least it can be given. Absent by default: without it the
    # console falls back to what Management already knows and says so, rather than pretending the
    # search is complete.
    directory_client_id: str = ""
    directory_client_secret: str = ""

    #: Which Keycloak group confers which AIRA role (`ADR-0017`), as
    #: ``role=/path[,/path];role=/path``. Group membership is the **only** source of a role — a
    #: realm role on the same token is not read, so assigning one directly grants nothing.
    #:
    #: `use-case-admin` and `use-case-user` are deliberately not settable here: administering a use
    #: case is a relationship between a group and *that* use case, held in `UseCaseGroupGrant`
    #: (`FRD-209`), and naming it here would grant somebody every use case at once.
    role_groups: str = ""

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
