"""Gateway-specific settings, on top of :class:`aira_common.config.BaseAiraSettings`.

Every field is an `AIRA_*` environment variable; `docs/CONFIGURATION.md` is the reference for all
of them.
"""

from __future__ import annotations

from pydantic import field_validator

from aira_common.config import BaseAiraSettings
from aira_common.counters import SentinelConfig, SentinelConfigError, parse_sentinels
from aira_common.integration_debug import configure_integration_debug
from aira_common.kafka import KafkaSecurity, validate_stage
from aira_common.logging import configure_logging
from aira_common.observability import configure_observability, set_payload_rendering
from aira_common.oidc import DEFAULT_CLOCK_SKEW_SECONDS, DEFAULT_EXPIRY_LEEWAY_SECONDS
from aira_common.roles import Role, parse_role_groups
from aira_gateway import __version__


def _default_jwks_uri(issuer: str) -> str:
    """Keycloak's certificate endpoint for an issuer. One place, because two would drift."""
    return f"{issuer.rstrip('/')}/protocol/openid-connect/certs"


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

    # Require an explicit use case on authenticated requests (FRD-102). Off, a caller in no use
    # case is served outside every budget, limit and release; refused outside local/demo (ADR-0015).
    require_use_case: bool = True

    # Extra redaction patterns for stored payloads (`FRD-406`), ';'- or newline-separated regexes.
    # Additive to the built-in credential shapes; an invalid or backtracking pattern stops startup.
    redact_patterns: str = ""

    # Enforce use-case/member usage budgets pre-dispatch (FRD-401).
    enforce_budgets: bool = True
    #: Anomaly detection (`FRD-501`). Off evaluates nothing; authored rules stay authored.
    detect_anomalies: bool = True
    #: Whether a suspension stops traffic (`FRD-503`). Off records findings and refuses nobody.
    enforce_suspensions: bool = True
    #: How often the detector wakes. A longer interval costs findings latency, not accuracy.
    anomaly_interval_seconds: float = 60.0

    # Shared counter store for rate limits and budget reservations (ADR-0008 / FRD-405). Empty
    # makes rate limits per process and budgets racy — documented degradations, not silent ones.
    redis_url: str = "redis://localhost:6379/0"
    #: Redis Sentinel: the sentinels as `host:port,…`. Set, they replace ``redis_url``, and the
    #: client asks them for the leader, which moves to another server on failover.
    redis_sentinels: str = ""
    #: The name the sentinels know the leader by (`sentinel monitor <name> …`).
    redis_sentinel_service: str = ""
    #: The data nodes' ACL user, if they have one, and its password; and the sentinels' own
    #: password where they require one. The passwords are secrets, from Vault.
    redis_username: str = ""
    redis_password: str = ""
    redis_sentinel_password: str = ""
    redis_db: int = 0

    # Enforce per-use-case/per-member request rate limits pre-dispatch (FRD-405). A use case
    # without a configured limit stays unlimited regardless of this toggle.
    enforce_rate_limits: bool = True

    # Failed authentications one source address may make per minute before it must wait. Counts
    # refusals only, so a working credential is never throttled by it. 0 disables it.
    max_auth_failures_per_minute: int = 60

    # Output tokens assumed when a request does not bound its output, for the budget reservation
    # (FRD-405 §4.2). Reconciled to actual usage; erring high is the safe direction.
    budget_estimate_output_tokens: int = 1024

    # Requests buffered for the off-path request-log writer (FRD-405 §4.4). A full queue writes
    # inline rather than dropping; 0 writes every record on the request path.
    log_queue_size: int = 512

    # How this service authenticates to Kafka. `PLAINTEXT` is refused outside `local`: these topics
    # feed the read-model authorization comes from, so an open broker grants admin to anyone.
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str = ""
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""
    kafka_ssl_cafile: str = ""
    #: The stage in every topic name and in the consumer group, on a cluster several stages share
    #: (`aira.t.usecases`). Empty keeps today's names. Management must be given the same one.
    kafka_stage: str = ""

    @field_validator("kafka_stage")
    @classmethod
    def _one_stage(cls, value: str) -> str:
        return validate_stage(value)

    @field_validator("redis_sentinels")
    @classmethod
    def _sentinel_addresses(cls, value: str) -> str:
        parse_sentinels(value)
        return value.strip()

    def redis_sentinel(self) -> SentinelConfig | None:
        """The Sentinel setup, or ``None`` when the URL is used. Sentinels without the leader's
        name are refused: they could only be asked about a leader nobody named."""
        sentinels = parse_sentinels(self.redis_sentinels)
        if not sentinels:
            return None
        if not self.redis_sentinel_service.strip():
            raise SentinelConfigError(
                "AIRA_REDIS_SENTINELS is set and AIRA_REDIS_SENTINEL_SERVICE is not: name the "
                "leader the sentinels monitor."
            )
        return SentinelConfig(
            sentinels=sentinels,
            service=self.redis_sentinel_service.strip(),
            username=self.redis_username,
            password=self.redis_password,
            sentinel_password=self.redis_sentinel_password,
            db=self.redis_db,
        )

    # Trust ``X-Forwarded-For`` for the recorded source IP. Off by default: the socket peer is
    # used (ADR-0007). Enable it only when this gateway sits behind a reverse proxy it controls.
    trust_forwarded_for: bool = False

    # How many reverse proxies append to ``X-Forwarded-For``; the address is read that many entries
    # from the **right**, because the left end is whatever the caller sent. 1 is the shipped nginx;
    # a shorter chain did not come through them and falls back to the socket peer.
    trusted_proxy_hops: int = 1

    # Hard ceiling on an accepted request body; larger bodies are rejected with 413 before
    # they are buffered (ADR-0007).
    max_request_bytes: int = 8 * 1024 * 1024

    # Bounds on a caller-supplied response schema (FRD-112 FR-3). It is forwarded, never executed,
    # so these are the gateway's whole exposure to it.
    max_response_schema_bytes: int = 32 * 1024
    max_response_schema_depth: int = 8
    max_response_schema_properties: int = 256

    # Embedding batch bounds (FRD-113 FR-5), chosen with the default rate limits in view: a batch
    # bound above every bucket would make large batches fail permanently.
    max_embedding_batch: int = 256
    max_embedding_chars: int = 1_000_000

    # Google AI Studio (FRD-304). Registered only with an API key, and only where `allowed_regions`
    # include `global` — that endpoint names no region and guarantees none.
    google_api_key: str = ""
    #: Which models to offer. Empty by default: the endpoint lists models a new key cannot use, so
    #: ask it instead — the catalog screen's discovery (FRD-507) lists what a key actually serves.
    gemini_models: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    #: Where requests may be processed — one list for every cloud (`ADR-0012` §6). Empty falls
    #: back to the EU regions of every supported cloud.
    allowed_regions: str = ""

    # Vertex AI / Model Garden (FRD-115). Registered only when a project and credentials are
    # configured; a laptop keeps working on the Generative Language adapter above.
    vertex_project: str = ""
    #: The service-account key, as JSON — from the environment or Vault (`FRD-116`).
    vertex_credentials: str = ""
    #: An Agent Platform API key, sent as `x-goog-api-key` instead of a bearer token (`FRD-115`
    #: FR-3a). Same locational hosts, so residency is unaffected. The service account wins if both.
    vertex_api_key: str = ""
    #: ``region/publisher/model`` per entry. The three things the URL and the dialect need.
    vertex_models: str = ""
    vertex_timeout_seconds: float = 120.0
    #: Backstop for Anthropic's required ``max_tokens`` when the catalog declares no default.
    vertex_default_max_tokens: int = 4096

    # Servers speaking the OpenAI dialect (FRD-123), each configured, priced and audited under its
    # own name so "which machine served this" has an answer:
    #   name=url|models|embedding_models|region, entries separated by ';'
    #   gpu-a=http://gpu-a:11434|qwen3:8b|nomic-embed-text|dc-frankfurt;gpu-b=http://gpu-b:11434|...
    openai_servers: str = ""

    # Single-endpoint shorthand for one entry above, named `ollama`; registered only when a URL is
    # set.
    ollama_url: str = ""
    ollama_models: str = ""
    ollama_embedding_models: str = ""
    #: Recorded on every audit row. Empty makes no residency claim; a name must also appear in
    #: `AIRA_ALLOWED_REGIONS`, or the gateway refuses to start.
    ollama_region: str = ""
    #: Generous: a cold self-deployed model loads for a minute or more (`ADR-0012` §5).
    ollama_timeout_seconds: float = 300.0

    # Cross-origin access (FRD-117 §5.4): an allow-list, empty by default. `*` with credentials
    # refuses to start.
    cors_origins: str = ""
    cors_allow_credentials: bool = False

    # Microsoft Foundry / Azure OpenAI (FRD-120). Registered only when the endpoint, a credential
    # and a deployment are all present. Deployment names say nothing reliable about the model, so
    # the mapping is declared (`ADR-0011` rule 2):
    #   model=deployment[|region][|embed], entries separated by ';'
    foundry_endpoint: str = ""
    foundry_api_key: str = ""
    foundry_deployments: str = ""
    #: Pinned rather than "latest", so response shapes never change without a deploy.
    foundry_api_version: str = "2024-10-21"
    foundry_timeout_seconds: float = 120.0

    #: The KIRA surface's stated end date, RFC 8594 (`ADR-0010` Option C). Empty means deprecated
    #: with no date yet; set it the day a migration plan exists.
    kira_sunset: str = ""
    #: Build metadata for `/version-info`. Absent is a valid state, not an error.
    build_number: int = 0
    build_time: str = ""
    git_commit: str = ""
    git_branch: str = ""

    # OIDC bearer validation (Keycloak). When disabled, only API keys are accepted.
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_audience: str = ""  # empty → skip audience verification (set in enterprise deployments)
    oidc_jwks_uri: str = ""  # empty → derived from the issuer
    #: Several Keycloak issuers at once (`FRD-118` FR-1): ``issuer|audience|jwks_uri`` per entry,
    #: ';'-separated, JWKS optional. Empty means the single pair above. Trusted equally — valid only
    #: while the realms describe one population.
    oidc_issuers: str = ""
    #: How far the issuer's clock may run ahead of ours (`FRD-134`), for `iat` and `nbf`. At 0 a
    #: gateway one second behind refuses every fresh token.
    oidc_clock_skew_seconds: float = DEFAULT_CLOCK_SKEW_SECONDS
    #: How long past `exp` a token is still accepted. Zero: this one extends a credential's life.
    oidc_expiry_leeway_seconds: float = DEFAULT_EXPIRY_LEEWAY_SECONDS

    #: Which Keycloak group confers which AIRA role (`ADR-0017`), as
    #: ``role=/path[,/path];role=/path``. Groups are the only source of a role; empty grants no
    #: oversight to anybody.
    role_groups: str = ""

    def kafka_security(self) -> KafkaSecurity:
        return KafkaSecurity(
            protocol=self.kafka_security_protocol,
            sasl_mechanism=self.kafka_sasl_mechanism,
            sasl_username=self.kafka_sasl_username,
            sasl_password=self.kafka_sasl_password,
            ssl_cafile=self.kafka_ssl_cafile,
        )

    def issuers(self) -> tuple[tuple[str, str, str], ...]:
        """``(issuer, audience, jwks_uri)`` per configured realm, the single pair included.

        One list either way, so nothing downstream has to ask which form was configured.
        """
        if not self.oidc_issuers.strip():
            if not self.oidc_issuer:
                return ()
            return ((self.oidc_issuer, self.oidc_audience, self.jwks_uri()),)
        parsed: list[tuple[str, str, str]] = []
        for entry in self.oidc_issuers.split(";"):
            if not entry.strip():
                continue
            parts = [part.strip() for part in entry.split("|")]
            issuer = parts[0]
            if not issuer:
                raise ValueError(
                    "AIRA_OIDC_ISSUERS has an entry with no issuer. Each is "
                    "'issuer|audience|jwks_uri', and only the last part may be omitted."
                )
            audience = parts[1] if len(parts) > 1 else ""
            jwks = parts[2] if len(parts) > 2 and parts[2] else _default_jwks_uri(issuer)
            parsed.append((issuer, audience, jwks))
        return tuple(parsed)

    def parsed_role_groups(self) -> dict[Role, tuple[str, ...]]:
        """The mapping, parsed once at startup so a malformed value fails loudly and early."""
        return parse_role_groups(self.role_groups)

    def jwks_uri(self) -> str:
        """Return the JWKS URI, deriving it from the issuer when not set explicitly."""
        if self.oidc_jwks_uri:
            return self.oidc_jwks_uri
        return _default_jwks_uri(self.oidc_issuer)

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


def configure_process(settings: GatewaySettings) -> bool:
    """Logging, integration debugging and OpenTelemetry for one gateway process (`FRD-615`).

    The API (`create_app`) and every background worker call this one function: a worker that
    configured nothing had no tracer provider, so every span it opened was silently discarded.
    Integration debugging comes first because exporters and tokens are watched call sites
    (`FRD-617`). Returns whether telemetry was configured.
    """
    configure_logging(settings.log_level, json_output=settings.log_json)
    configure_integration_debug(settings.debug_integrations)
    set_payload_rendering(settings.debug_otel_payload)
    return configure_observability(
        service_name=settings.app_name,
        service_version=__version__,
        environment=settings.environment,
        endpoint=settings.otel_endpoint,
        enabled=settings.otel_enabled,
        sample_ratio=settings.otel_sample_ratio,
    )


#: The name the background workers (config consumer, retention sweep) import it under.
configure_worker = configure_process
