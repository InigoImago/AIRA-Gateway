"""Base settings shared by AIRA services.

Read from ``AIRA_``-prefixed environment variables and an optional ``.env`` file, with Vault ranked
above both (`FRD-116`). Component settings subclass :class:`BaseAiraSettings`.
"""

from __future__ import annotations

from typing import Any, ClassVar, Self

from pydantic import Field, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from aira_common.clock import DEFAULT_TIME_ZONE, zone_named
from aira_common.secrets import by_setting, load_secrets

#: The settings a plane may also hold under its own name, `AIRA_<PLANE>_<SETTING>` (`FRD-150`).
#: Both planes read one name for each, so an installation whose single Vault path serves both
#: could not give the gateway and Management different database users or Kafka identities — the
#: separation of privilege a database and a broker are set up to have.
PLANE_CREDENTIALS = (
    "postgres_user",
    "postgres_password",
    "kafka_sasl_username",
    "kafka_sasl_password",
)


class VaultSource(PydanticBaseSettingsSource):
    """Settings read from Vault, ranked above the environment (`FRD-116` FR-3).

    A settings *source* rather than an injection into ``os.environ``: environment values are
    readable from `/proc`, inherited by every subprocess and dumped by crash handlers. Loaded once
    and cached, because Vault is a start-up dependency and never a request-path one (FR-5).

    A key reaches the setting it names however it is spelled — case and the prefix aside — by
    `aira_common.secrets.setting_key`, the one rule for that.
    """

    _cache: dict[str, str] | None = None

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._prefix = str(self.config.get("env_prefix") or "").upper()
        self._named: dict[str, str] | None = None

    @classmethod
    def reset(cls) -> None:
        """Forget the loaded secrets. For tests, which must not share a cache across cases."""
        cls._cache = None

    def _secrets(self) -> dict[str, str]:
        if VaultSource._cache is None:
            # Whatever this raises is a boot failure by design (`FRD-116` §5.3).
            VaultSource._cache = load_secrets()
        return VaultSource._cache

    def _by_setting(self) -> dict[str, str]:
        """The loaded keys under the names a setting is looked up by — normalised once."""
        if self._named is None:
            self._named = by_setting(self._secrets(), self._prefix)
        return self._named

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Spelled as the environment variable would be, which is what `setting_key` maps to.
        return self._by_setting().get(f"{self._prefix}{field_name}".upper()), field_name, False

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            value, key, complex_value = self.get_field_value(field, field_name)
            if value is not None:
                values[key] = self.prepare_field_value(field_name, field, value, complex_value)
        return values


class BaseAiraSettings(BaseSettings):
    """Common configuration fields for every AIRA service."""

    model_config = SettingsConfigDict(
        env_prefix="AIRA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: The plane whose own credentials replace the shared ones (`PLANE_CREDENTIALS`); a subclass
    #: that names one declares a `<plane>_<setting>` field for each.
    credential_plane: ClassVar[str] = ""

    @model_validator(mode="after")
    def _the_planes_own_credentials(self) -> Self:
        """A plane's own name wins over the shared one, **wherever each was read from**.

        So a Vault path holding `AIRA_POSTGRES_PASSWORD` and `AIRA_MANAGEMENT_POSTGRES_PASSWORD`
        gives Management its own and the gateway the shared one, and an installation that never
        writes a plane's name runs as it always has. Empty is unset, as compose passes an optional
        variable. Written into the shared field, so every reader — the connection, the broker, the
        check against the published development password — uses the value in effect.
        """
        if self.credential_plane:
            for name in PLANE_CREDENTIALS:
                own = getattr(self, f"{self.credential_plane}_{name}")
                if own:
                    # Past validation, so not through `setattr`, which would validate again.
                    self.__dict__[name] = own
        return self

    @model_validator(mode="before")
    @classmethod
    def _empty_means_unset(cls, values: Any) -> Any:
        """An empty environment variable is **absent**, not a value — for non-string settings.

        Compose passes optional variables as `${AIRA_X:-}`, an empty string when unset, and pydantic
        cannot parse `""` as a number. Not applied to `str` fields, where empty is a real answer
        (`AIRA_CORS_ORIGINS=` clears the setting).
        """
        if not isinstance(values, dict):
            return values
        return {
            key: value
            for key, value in values.items()
            if not (value == "" and _is_non_string_field(cls, key))
        }

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Explicit arguments first, then **Vault**, then the environment.

        Init arguments stay first because that is how the tests construct settings. Vault above the
        environment is FR-3; there is no third source.
        """
        return (
            init_settings,
            VaultSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    app_name: str = "aira"
    """Human-readable service name; also used as the OTel ``service.name``."""

    environment: str = "local"
    """Deployment environment (``local``, ``staging``, ``production``)."""

    log_level: str = "INFO"
    """Root log level (``DEBUG``/``INFO``/``WARNING``/``ERROR``)."""

    log_json: bool = True
    """Emit JSON logs (True) or human-friendly console logs (False)."""

    demo_mode: bool = False
    """When True, enable the mock upstream and demo-safe defaults (see FRD-002)."""

    api_key_default_days: int = 30
    """How long a newly issued API key lives, in days (`ADR-0015`).

    **A key is always bounded**: a credential with no end date has to be inventoried by somebody
    who remembers to. Shared by both planes — Management issues keys and the gateway's CLI mints
    the break-glass one — so the policy has one definition.
    """

    api_key_max_days: int = 180
    """The longest lifetime anybody may ask for. Asking for more is refused by name, with the
    maximum in the message, never silently truncated."""

    currency: str = "EUR"
    """Currency all prices and cost budgets are expressed in (FRD-403).

    One per installation, because prices come from a single provider contract. Display only: a
    stored price is never converted. When a price is *looked up* for the catalog, it is asked for in
    this currency — Google's billing account answers in it (`FRD-509`), and a public list price in
    another is converted only at a rate the installation set.
    """

    time_zone: str = DEFAULT_TIME_ZONE
    """The installation's clock (`FRD-629`): an IANA name such as ``Europe/Berlin``, or ``UTC``.

    Where a budget's day and month begin, a report's days and hours, and the clock every timestamp
    in the console is printed on. Shared by both planes, because the gateway counts on it and the
    console shows it: one setting, or a budget would reset at a midnight the console does not show.
    UTC by default, which is how an installation that sets nothing has always counted. Refused at
    start-up when the time-zone database does not know it, or when it is ever a part of an hour off
    UTC (`aira_common.clock`).
    """

    @field_validator("time_zone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        # The zone's own name, so every answer that states the clock spells it one way.
        return str(zone_named(value))

    store_payloads: bool = True
    """Persist request/response payloads (FRD-103). When False, only metadata is stored.

    Shared by both planes: the gateway obeys it, and Management's privacy notice (`FRD-625`) says
    what it is set to — one definition, so the notice cannot describe a different installation.
    """

    strict_data_mode: bool = True
    """Withhold every person from everybody but Global Administrator and IT Security (`FRD-627`).

    On by default, because a system able to judge employees' conduct or performance is
    co-determined and an assurance any use-case administrator could lift is not one (`ADR-0036`).
    While on: content is not stored or readable by those two roles only; figures split by person,
    the person and key on a request row, and the person a finding or stop names reach only them and
    the person concerned; nobody sees latency per person; and `payload.read_any` and
    `incident.investigate` cannot be granted to another role. Decided when read, in both planes,
    so turning it off restores every use case's own setting.
    """

    pseudonym_secret: str = ""
    """The key telemetry and logs pseudonymise a person's name with under strict data mode
    (`FRD-627` FR-14). A credential, so it comes from Vault, and shared by both planes so one
    person has one pseudonym. Empty, each process draws its own: no name leaks, but a SIEM can no
    longer follow one caller across processes and restarts."""

    default_retention_days: int = Field(default=7, ge=1, le=3650)
    """Payload retention in days for requests carrying no use case (FRD-404); use-case traffic
    follows its use case's period. Shared for the same reason as ``store_payloads``. Within the
    bounds a use case's own period has: below one day the sweep would keep payloads a day anyway
    while the privacy notice, which prints this value, said they were kept without limit."""

    log_retention_days: int = 0
    """Delete whole request_log rows older than this many days. 0 keeps them forever, which is
    what the cost reporting reads — opt in deliberately. Printed in the privacy notice."""

    otel_enabled: bool = False
    """Enable OpenTelemetry export (traces/metrics/logs) via OTLP (see FRD-001)."""

    otel_endpoint: str = "http://localhost:4318"
    """OTLP/HTTP endpoint of the OpenTelemetry Collector."""

    otel_sample_ratio: float = 1.0
    """Trace sampling ratio, decided by trace id in every process and never by a caller's
    `traceparent` flag (`request_sampler`); 1.0 = every trace. Below 1 it thins the delivery
    channel too, whose records are spans."""

    debug_integrations: str = ""
    """Which external systems to report one line per call for (`FRD-617`).

    Comma-separated from `otel`, `kafka`, `auth`, `vault`, `redis`, `postgres`, or `all`. **Empty is
    off and the default**, costing one set lookup per call site. The lines go out at `INFO`, so no
    second switch is needed.
    """

    debug_otel_payload: int = 0
    """Print this many items of each OTLP batch as **OTLP/JSON** (`FRD-617` §3.10). 0 is off.

    The protobuf-JSON mapping of what is sent — what a collector writes with `encoding: json` —
    not what goes over the wire (`docs/INTEGRATIONS.md` §6). A debugging setting: it renders on
    every export and carries span attributes (subject, use case, model, source address), though
    never a prompt or response (`ADR-0016`).
    """

    @field_validator("debug_integrations")
    @classmethod
    def _integrations_are_known(cls, value: str) -> str:
        """A misspelled system name refuses the process rather than watching nothing.

        Imported here so that reading settings does not pull in the OpenTelemetry SDK by way of
        the logging module.
        """
        from aira_common.integration_debug import parse_systems

        parse_systems(value)
        return value


def _is_non_string_field(model: type[BaseSettings], name: str) -> bool:
    """Whether ``name`` is a declared field whose type is not ``str``.

    Aliases and unknown keys answer False: an empty value for something this model does not
    declare is not ours to reinterpret.
    """
    field = model.model_fields.get(name)
    if field is None:
        return False
    return field.annotation is not str
