"""Gateway-specific settings.

Extends :class:`aira_common.config.BaseAiraSettings` with the connection details the
gateway needs for its readiness checks and (later) persistence and eventing.
"""

from __future__ import annotations

from aira_common.config import BaseAiraSettings


class GatewaySettings(BaseAiraSettings):
    """Configuration for the Gateway API service."""

    app_name: str = "aira-gateway"

    # Postgres (system of record). Defaults target the local Compose stack from the host.
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "aira_gateway"
    postgres_user: str = "aira"
    postgres_password: str = "aira-local"

    # Kafka event bus. Host-facing listener from the Compose stack.
    kafka_bootstrap_servers: str = "localhost:29092"

    # Use an in-memory SQLite DB (set under pytest) instead of Postgres.
    test_database: bool = False

    # Require authentication on the API routes; when False (pure demo) routes are open.
    auth_required: bool = True

    # Require an explicit use case on authenticated (non-demo) requests (FRD-102).
    require_use_case: bool = False

    # Persist request/response payloads (FRD-103). When False, only metadata is stored.
    store_payloads: bool = True

    # Retention for stored payloads of requests that carry no use case, in days (FRD-404).
    # Use-case traffic follows the period configured on its use case.
    default_retention_days: int = 7

    # Delete whole request_log rows older than this many days. 0 keeps them forever, which is
    # the historical behaviour and what the cost reporting reads — opt in deliberately.
    log_retention_days: int = 0

    # Enforce use-case/member usage budgets pre-dispatch (FRD-401).
    enforce_budgets: bool = True

    # Shared counter store for rate-limit buckets and budget reservations (ADR-0008 / FRD-405).
    # Empty disables it: rate limits then hold per process instead of across instances, and
    # budget enforcement uses the racy read-then-book path. Both are documented degradations,
    # not silent ones.
    redis_url: str = "redis://localhost:6379/0"

    # Enforce per-use-case/per-member request rate limits pre-dispatch (FRD-405). A use case
    # without a configured limit stays unlimited regardless of this toggle.
    enforce_rate_limits: bool = True

    # Tokens assumed for a request whose output length the caller did not bound, used to reserve
    # budget before the real usage is known (FRD-405 §4.2). Reconciled to the actual figure the
    # moment the response arrives; erring high is the safe direction for a spend limit.
    budget_estimate_output_tokens: int = 1024

    # Requests buffered for the off-path request-log writer (FRD-405 §4.4). A full queue makes
    # the write happen inline rather than dropping the record. **0 writes every record on the
    # request path**, which is the pre-FRD-405 behaviour — available for installations that need
    # a request durably logged before its response goes out, at the latency cost that implies.
    log_queue_size: int = 512

    # Trust ``X-Forwarded-For`` for the recorded source IP. Only enable when the gateway sits
    # behind a reverse proxy that *overwrites* the header — otherwise any client can forge the
    # audit trail. Off by default: the socket peer is used (ADR-0007).
    trust_forwarded_for: bool = False

    # Hard ceiling on an accepted request body; larger bodies are rejected with 413 before
    # they are buffered (ADR-0007).
    max_request_bytes: int = 8 * 1024 * 1024

    # Real Google Gemini upstream (FRD-304). Registered only when an API key is present.
    google_api_key: str = ""
    gemini_models: str = "gemini-2.0-flash,gemini-1.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    #: Where requests may be processed — **one list for every cloud** (`ADR-0012` §6). Google says
    #: `europe-west1`, Azure says `westeurope`; the names differ and the policy does not, so a
    #: per-cloud setting would mean a per-cloud audit. Empty falls back to the EU regions of every
    #: supported cloud, because a residency constraint that must be switched on is one that will
    #: be found switched off.
    allowed_regions: str = ""

    # Vertex AI / Model Garden (FRD-115). Registered only when a project and credentials are
    # configured; a laptop keeps working on the Generative Language adapter above.
    vertex_project: str = ""
    #: The service-account key, as JSON. An interim source — `FRD-116` moves it to Vault, which is
    #: where a private key of this value belongs.
    vertex_credentials: str = ""
    #: ``region/publisher/model`` per entry. The three things the URL and the dialect need.
    vertex_models: str = ""
    vertex_timeout_seconds: float = 120.0
    #: Backstop for Anthropic's required ``max_tokens`` when the catalog declares no default.
    vertex_default_max_tokens: int = 4096

    # OIDC bearer validation (Keycloak). When disabled, only API keys are accepted.
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_audience: str = ""  # empty → skip audience verification (set in enterprise deployments)
    oidc_jwks_uri: str = ""  # empty → derived from the issuer

    def jwks_uri(self) -> str:
        """Return the JWKS URI, deriving it from the issuer when not set explicitly."""
        if self.oidc_jwks_uri:
            return self.oidc_jwks_uri
        return f"{self.oidc_issuer.rstrip('/')}/protocol/openid-connect/certs"

    def database_url(self, *, use_sqlite: bool) -> str:
        """Return the async SQLAlchemy URL for the gateway database."""
        if use_sqlite:
            return "sqlite+aiosqlite:///:memory:"
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def kafka_host_port(self) -> tuple[str, int]:
        """Return the (host, port) of the first Kafka bootstrap server for TCP checks."""
        first = self.kafka_bootstrap_servers.split(",")[0].strip()
        if ":" in first:
            host, _, port = first.rpartition(":")
            return host or "localhost", int(port)
        return first or "localhost", 9092
