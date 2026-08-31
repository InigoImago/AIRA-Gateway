"""Gateway-specific settings.

Extends :class:`aira_common.config.BaseAiraSettings` with the connection details the
gateway needs for its readiness checks and (later) persistence and eventing.
"""

from __future__ import annotations

from aira_common.config import BaseAiraSettings
from aira_common.kafka import KafkaSecurity
from aira_common.logging import configure_logging
from aira_common.observability import configure_observability
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

    # Require an explicit use case on authenticated (non-demo) requests (FRD-102).
    #
    # **Default flipped to True on 2026-08-11.** Off, an authenticated caller belonging to no use
    # case could name none and be served: charged to no budget, bounded by no use-case rate limit,
    # outside the model release (`FRD-308`), with an audit row naming nobody. Measured before it
    # was changed — 200, 200 tokens, `use_case = NULL`.
    #
    # A default is what most deployments run, so the safe answer belongs here; turning it off
    # outside `local`/demo is refused at startup (`security.py`), which is `ADR-0015`'s shape:
    # environment-shaped rather than merely stricter, so the demo still works and a production
    # deployment cannot quietly opt out.
    require_use_case: bool = True

    # Persist request/response payloads (FRD-103). When False, only metadata is stored.
    store_payloads: bool = True

    # Extra redaction patterns for stored payloads (`FRD-406`), ';'- or newline-separated regexes.
    # **Additive**: the built-in credential shapes always apply, because a deployment naming its
    # own token format must not thereby stop redacting Google keys. An invalid pattern, or one
    # that backtracks exponentially, stops the gateway rather than silently matching nothing.
    redact_patterns: str = ""

    # Retention for stored payloads of requests that carry no use case, in days (FRD-404).
    # Use-case traffic follows the period configured on its use case.
    default_retention_days: int = 7

    # Delete whole request_log rows older than this many days. 0 keeps them forever, which is
    # the historical behaviour and what the cost reporting reads — opt in deliberately.
    log_retention_days: int = 0

    # Enforce use-case/member usage budgets pre-dispatch (FRD-401).
    enforce_budgets: bool = True
    #: Anomaly detection (`FRD-501`). Off means no evaluation at all; rules already authored stay
    #: authored, which is the difference between "switched off" and "deleted".
    detect_anomalies: bool = True
    #: Whether a suspension actually stops traffic (`FRD-503`). Off records findings and refuses
    #: nobody — the setting an installation uses while it learns what its rules do.
    enforce_suspensions: bool = True
    #: How often the detector wakes. It evaluates only the scopes that saw traffic, so a longer
    #: interval costs findings latency rather than accuracy.
    anomaly_interval_seconds: float = 60.0

    # Shared counter store for rate-limit buckets and budget reservations (ADR-0008 / FRD-405).
    # Empty disables it: rate limits then hold per process instead of across instances, and
    # budget enforcement uses the racy read-then-book path. Both are documented degradations,
    # not silent ones.
    redis_url: str = "redis://localhost:6379/0"

    # Enforce per-use-case/per-member request rate limits pre-dispatch (FRD-405). A use case
    # without a configured limit stays unlimited regardless of this toggle.
    enforce_rate_limits: bool = True

    # Failed authentications one source address may make per minute before it is asked to wait
    # (2026-08-08). Every limit `FRD-405` built is keyed by use case or member, so it needs a
    # verified identity and cannot bound the traffic of somebody who has none — an unauthenticated
    # caller could probe credentials, and each attempt a database round trip, without ever meeting
    # a bound.
    #
    # It counts **refusals only**: a caller with a working credential never touches this bucket, so
    # no legitimate integration can be throttled by it however busy it is. 0 disables it.
    max_auth_failures_per_minute: int = 60

    # Tokens assumed for a request whose output length the caller did not bound, used to reserve
    # budget before the real usage is known (FRD-405 §4.2). Reconciled to the actual figure the
    # moment the response arrives; erring high is the safe direction for a spend limit.
    budget_estimate_output_tokens: int = 1024

    # Requests buffered for the off-path request-log writer (FRD-405 §4.4). A full queue makes
    # the write happen inline rather than dropping the record. **0 writes every record on the
    # request path**, which is the pre-FRD-405 behaviour — available for installations that need
    # a request durably logged before its response goes out, at the latency cost that implies.
    log_queue_size: int = 512

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

    # Trust ``X-Forwarded-For`` for the recorded source IP. Off by default: the socket peer is
    # used (ADR-0007). Enable it only when this gateway sits behind a reverse proxy it controls.
    trust_forwarded_for: bool = False

    # How many reverse proxies append to ``X-Forwarded-For`` in front of this gateway.
    #
    # The address is read **that many entries from the right**, never from the left. The left end
    # is whatever the *caller* sent: `X-Forwarded-For: 10.9.9.9` from a client arrives at the
    # gateway as `10.9.9.9, <real address>` because a proxy **appends** — the shipped nginx uses
    # `$proxy_add_x_forwarded_for`, and so does every default configuration in the wild. Reading
    # the left end therefore let a caller choose the address that lands in the audit trail, the
    # address `FRD-505`'s incident filter searches, and the key the failed-authentication bound
    # counts against — so rotating the header defeated the brute-force bound entirely.
    #
    # 1 is the shipped topology (one nginx). Raise it by one for each additional proxy that
    # appends. A chain **shorter** than this is a request that did not come through them, and its
    # header is ignored in favour of the socket peer.
    trusted_proxy_hops: int = 1

    # Hard ceiling on an accepted request body; larger bodies are rejected with 413 before
    # they are buffered (ADR-0007).
    max_request_bytes: int = 8 * 1024 * 1024

    # A response schema is caller-supplied *structure* whose recursion is caller-controlled
    # (FRD-112 FR-3). The schema is forwarded and never executed, so these bounds are the whole
    # of the gateway's exposure to it — and counting, unlike a regex, cannot backtrack.
    max_response_schema_bytes: int = 32 * 1024
    max_response_schema_depth: int = 8
    max_response_schema_properties: int = 256

    # Embedding batch bounds (FRD-113 FR-5). Chosen **with the default rate limits in view**: a
    # batch bound larger than any configured bucket would make large batches fail permanently,
    # since the request is admissible here and refused a moment later by a limit it can never
    # satisfy. The refusal names which of the two said no.
    max_embedding_batch: int = 256
    max_embedding_chars: int = 1_000_000

    # Google AI Studio (FRD-304). Registered only when an API key is present, and only in a
    # deployment whose `allowed_regions` include `global` — that endpoint names no region and
    # guarantees none, which is the whole difference from Vertex (FRD-115).
    google_api_key: str = ""
    #: Which models to offer. **Empty by default, deliberately since 2026-08-10.** It used to name
    #: `gemini-2.0-flash,gemini-1.5-flash`, and a key issued today cannot use either: the endpoint
    #: still lists them and answers `no longer available to new users` on the first request. A
    #: default that names something unusable is worse than none — it produces a 404 that reads as
    #: our fault. Ask the endpoint instead: the catalog screen's discovery (FRD-507) lists what a
    #: key actually serves.
    gemini_models: str = ""
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
    #: The service-account key, as JSON. From the environment or from Vault (`FRD-116`), which is
    #: where a private key of this value belongs.
    vertex_credentials: str = ""
    #: The **other** credential this adapter accepts (`FRD-115` FR-3a): an Agent Platform API key,
    #: sent as `x-goog-api-key` instead of an exchanged bearer token.
    #:
    #: Google issues these to accounts that never create a service account, and until this existed
    #: an installation holding one could reach nothing: AIRA's only API-key path was AI Studio, on
    #: a different host, which refuses such a key with `API_KEY_SERVICE_BLOCKED`.
    #:
    #: **The region is unaffected**, and that is the point of putting it here rather than building a
    #: fifth provider on Google's global endpoint: the same key answers on the locational hosts this
    #: adapter already uses, so `FRD-115` FR-5's residency check applies unchanged.
    #:
    #: Set both and the service account wins — a deployment that has one has made the more
    #: deliberate choice, and silently preferring the key would be a downgrade nobody asked for.
    vertex_api_key: str = ""
    #: ``region/publisher/model`` per entry. The three things the URL and the dialect need.
    vertex_models: str = ""
    vertex_timeout_seconds: float = 120.0
    #: Backstop for Anthropic's required ``max_tokens`` when the catalog declares no default.
    vertex_default_max_tokens: int = 4096

    # Servers speaking the OpenAI dialect (FRD-123) — Ollama boxes, self-deployed endpoints,
    # later Foundry. **A list, because they are separate systems**: a deployment can attach several,
    # and each one is configured, priced and *audited* under its own name. With a single endpoint
    # setting, "which machine served this request" has no answer, which for a self-hosted fleet is
    # exactly the question an audit exists to answer.
    #
    #   name=url|models|embedding_models|region, entries separated by ';'
    #   gpu-a=http://gpu-a:11434|qwen3:8b|nomic-embed-text|dc-frankfurt;gpu-b=http://gpu-b:11434|...
    openai_servers: str = ""

    # The single-endpoint shorthand, equivalent to one entry above and named `ollama`. Registered
    # **only** when a URL is set — a system that appears in a deployment nobody asked for it in
    # eventually serves production traffic. `AIRA_OLLAMA_REGION` is recorded on every audit row: a
    # self-hosted model is the strongest residency story available, and one nothing records is a
    # claim rather than evidence.
    ollama_url: str = ""
    ollama_models: str = ""
    ollama_embedding_models: str = ""
    #: Empty by default: **no residency claim**, so nothing to enforce and a laptop keeps working.
    #: Naming one opts in to the evidence *and* to the check — the name must then also appear in
    #: `AIRA_ALLOWED_REGIONS`, or the gateway refuses to start and says so.
    ollama_region: str = ""
    #: Generous on purpose: a cold self-deployed model loads for a minute or more, and treating
    #: that as an outage is the first way to get `ADR-0012` §5 wrong.
    ollama_timeout_seconds: float = 300.0

    # Cross-origin access (FRD-117 §5.4). **An allow-list, empty by default** — the SPA is served
    # from the same origin through the proxy, so anything cross-origin is a deliberate choice.
    # `*` together with credentials refuses to start: browsers reject the combination, and a server
    # that implements it by reflecting the origin lets any site a user visits call this API with
    # their credentials. Compatibility is never a reason to relax it.
    cors_origins: str = ""
    cors_allow_credentials: bool = False

    # Microsoft Foundry / Azure OpenAI (FRD-120). Registered only when the endpoint, a credential
    # and at least one deployment are all present — half a configuration is a gateway that starts
    # and answers 401 for everything, which reads as a broken credential rather than a missing one.
    #
    # A deployment name is chosen by whoever created the resource and says nothing reliable about
    # the model, so the mapping is declared rather than inferred (`ADR-0011` rule 2):
    #   model=deployment[|region][|embed], entries separated by ';'
    foundry_endpoint: str = ""
    foundry_api_key: str = ""
    foundry_deployments: str = ""
    #: Pinned rather than "latest": a version that moves on its own changes response shapes with no
    #: deploy, and the first sign is a mapper reading a field that stopped being sent.
    foundry_api_version: str = "2024-10-21"
    foundry_timeout_seconds: float = 120.0

    #: The KIRA compatibility surface's stated end date, RFC 8594 (`ADR-0010` Option C). Empty
    #: means "announced as deprecated, no date yet" — a layer with no date is a permanent one, so
    #: this should be set the day a migration plan exists.
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
    #: **Several Keycloak issuers at once** (`FRD-118` FR-1), for one organisation whose people
    #: live in more than one realm — a migration between realms, a second instance, a merger.
    #:
    #: ``issuer|audience|jwks_uri`` per entry, entries separated by ``;``; the JWKS URI is optional
    #: and derived from the issuer when omitted. Empty means the single-issuer pair above, which is
    #: what every deployment had before this existed and what most keep.
    #:
    #: **Trusted equally, on purpose** (owner's answer, 2026-08-17): the same group path from
    #: either realm means the same thing, because it is the same directory content. That holds only
    #: while the issuers describe one population — two *unrelated* directories would need the
    #: issuer to be part of the identity, which is a different feature and a schema change.
    oidc_issuers: str = ""
    #: How far the **issuer's** clock may run ahead of this gateway's (`FRD-134`). Applies to `iat`
    #: and `nbf`. Costs nothing: a token that is "too new" was still genuinely minted, and
    #: accepting it extends nobody's access. At `0` — PyJWT's default, and what this was until
    #: 2026-08-17 — a gateway one second behind its issuer refuses **every** fresh token.
    oidc_clock_skew_seconds: float = DEFAULT_CLOCK_SKEW_SECONDS
    #: How long past `exp` a token is still accepted. **Zero**, and that is the half with a cost:
    #: it extends a credential's life beyond what the issuer granted.
    oidc_expiry_leeway_seconds: float = DEFAULT_EXPIRY_LEEWAY_SECONDS

    #: Which Keycloak group confers which AIRA role (`ADR-0017`), as
    #: ``role=/path[,/path];role=/path``. Group membership is the **only** source of a role; a
    #: realm role on the same token is not read. Empty here grants no oversight to anybody, which
    #: is the safe direction for a data plane — Management is the plane that refuses to boot
    #: without a global-admin group, because it is the one an installation is repaired from.
    role_groups: str = ""

    def issuers(self) -> tuple[tuple[str, str, str], ...]:
        """``(issuer, audience, jwks_uri)`` per configured realm, the single pair included.

        One list either way, so nothing downstream has to ask which form was configured — the
        shape `FRD-126` keeps arriving at: a caller that has to know which of two configurations it
        got is a caller that will one day handle only one of them.
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


def configure_worker(settings: GatewaySettings) -> bool:
    """Logging and OpenTelemetry for a process that is **not** the API (`FRD-615`).

    `create_app` has configured both since `FRD-001`, and the three background processes — the
    config consumer, the retention sweep, and anything that joins them — never called anything at
    all. They therefore had no tracer provider, so `trace.get_tracer` handed back the API's
    non-recording implementation and every span they opened was discarded before it was built.

    Found the hard way: the consumer span that makes a configuration change one trace across both
    planes was written, tested, and **inert in the deployment**, because the process it runs in
    exports nothing. A wire is not closed until the process at each end is one that can speak.

    Returns whether telemetry was configured, so a caller can say so.
    """
    configure_logging(settings.log_level, json_output=settings.log_json)
    return configure_observability(
        service_name=settings.app_name,
        service_version=__version__,
        environment=settings.environment,
        endpoint=settings.otel_endpoint,
        enabled=settings.otel_enabled,
        sample_ratio=settings.otel_sample_ratio,
    )
