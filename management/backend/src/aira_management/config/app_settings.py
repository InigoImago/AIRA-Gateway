"""Typed application settings for the management backend.

Reuses the shared :class:`aira_common.config.BaseAiraSettings` so configuration is
consistent with the gateway. Django's ``settings.py`` reads values from an instance of
:class:`ManagementSettings`. Secrets default to dev-only values and are sourced from
Vault in real deployments (see PRD §9).
"""

from __future__ import annotations

from pydantic import field_validator

from aira_common.config import BaseAiraSettings
from aira_common.kafka import KafkaSecurity, validate_stage
from aira_common.oidc import DEFAULT_CLOCK_SKEW_SECONDS, DEFAULT_EXPIRY_LEEWAY_SECONDS

#: The well-known development signing key. ``config.security`` refuses to start any non-local
#: environment that is still using it.
DEV_SECRET_KEY = "dev-insecure-secret-key-change-me"  # noqa: S105

#: What separates the Keycloak root from the realm name in an issuer URL.
_REALMS_MARKER = "/realms/"


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

    #: How fast one caller may ask, in DRF's `<n>/<period>` notation.
    #:
    #: `throttle_auth_failures` bounds **refusals** per source address, in the authentication class
    #: (`apps.api.attempts`, `ADR-0015`), where the expensive JWKS verification happens; `0`
    #: switches it off. `throttle_user` is generous — a console screen loads five panels at once —
    #: and sized to stop a script, not to shape ordinary use.
    #:
    #: **Per process**: with no `CACHES` configured, Django's cache is `LocMemCache`, so N workers
    #: admit N × the rate — still enough to stop one client looping. Point `CACHES` at a shared
    #: store for an exact bound.
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

    # How this service authenticates to Kafka. `PLAINTEXT` keeps the Compose stack working and is
    # **refused outside `local`**: the gateway builds the read-model its authorization comes from
    # out of these topics, so an unauthenticated broker lets anybody grant themselves access.
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str = ""
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""
    kafka_ssl_cafile: str = ""
    #: The stage in every topic name this service publishes to (`aira.t.usecases`); the gateway
    #: must be given the same one. Empty keeps today's names.
    kafka_stage: str = ""

    @field_validator("kafka_stage")
    @classmethod
    def _one_stage(cls, value: str) -> str:
        return validate_stage(value)

    def kafka_security(self) -> KafkaSecurity:
        return KafkaSecurity(
            protocol=self.kafka_security_protocol,
            sasl_mechanism=self.kafka_sasl_mechanism,
            sasl_username=self.kafka_sasl_username,
            sasl_password=self.kafka_sasl_password,
            ssl_cafile=self.kafka_ssl_cafile,
        )

    # Directory search (`FRD-209`): a **read-only** service account with `view-users` and
    # `query-groups` on the realm. Absent by default; the console then searches what Management
    # already knows, and says so.
    directory_client_id: str = ""
    directory_client_secret: str = ""
    #: Where this service reaches Keycloak's admin API, when that is not the address in the
    #: issuer. The issuer is the address *browsers* use; inside a container it is usually not
    #: Keycloak at all, and every group check would then fail as "could not be asked".
    directory_url: str = ""

    #: Which Keycloak group confers which AIRA role (`ADR-0017`), as
    #: ``role=/path[,/path];role=/path``. Group membership is the **only** source of a role — a
    #: realm role on the same token is not read, so assigning one directly grants nothing.
    #:
    #: `use-case-admin` and `use-case-user` are deliberately not settable here: administering a use
    #: case is a relationship between a group and *that* use case, held in `UseCaseGroupGrant`
    #: (`FRD-209`), and naming it here would grant somebody every use case at once.
    role_groups: str = ""

    #: When the console makes somebody read the privacy notice before working (`FRD-625`):
    #: ``once`` per edition of the notice, ``monthly`` — per edition and again at the first
    #: sign-in of each calendar month — or ``always``, on every start of the console, which is
    #: what lets whoever reviews the notice see the window without waiting a month.
    privacy_notice_mode: str = "monthly"
    #: The controller (Art. 13(1)(a) DSGVO) as it is printed: name, address and a contact.
    #: **Required outside `local`** — a notice that names nobody informs nobody.
    privacy_controller: str = ""
    #: How to reach the data protection officer (Art. 13(1)(b)); empty prints that none is named.
    privacy_dpo_contact: str = ""
    #: The works agreement (Betriebsvereinbarung) that governs this system, by name or reference;
    #: empty prints that none is recorded (`BetrVG` §87(1) no. 6).
    privacy_works_agreement: str = ""
    #: The language the notice is shown in when the browser asks for none this installation has.
    privacy_default_language: str = "de"

    @field_validator("privacy_notice_mode")
    @classmethod
    def _known_notice_mode(cls, value: str) -> str:
        """A misspelled mode refuses the process rather than quietly never showing the notice."""
        from aira_management.apps.privacy.schedule import NoticeMode

        try:
            return str(NoticeMode(value.strip().lower()))
        except ValueError:
            known = ", ".join(str(mode) for mode in NoticeMode)
            raise ValueError(f"AIRA_PRIVACY_NOTICE_MODE must be one of {known}") from None

    @field_validator("privacy_default_language")
    @classmethod
    def _known_default_language(cls, value: str) -> str:
        """A default the notice has no text for would answer every browser with an error."""
        from aira_management.apps.privacy.texts import LANGUAGES

        code = value.strip().lower()
        if code not in LANGUAGES:
            raise ValueError(f"AIRA_PRIVACY_DEFAULT_LANGUAGE must be one of {', '.join(LANGUAGES)}")
        return code

    @property
    def oidc_issuer_base(self) -> str:
        """The Keycloak root, derived from the issuer (`.../realms/<realm>`).

        Derived rather than configured, so two settings for one server cannot get out of step.
        """
        issuer = self.oidc_issuer.rstrip("/")
        return issuer.split(_REALMS_MARKER)[0] if _REALMS_MARKER in issuer else issuer

    @property
    def oidc_realm(self) -> str:
        """The realm name, from the issuer."""
        issuer = self.oidc_issuer.rstrip("/")
        return issuer.split(_REALMS_MARKER)[-1] if _REALMS_MARKER in issuer else ""

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
