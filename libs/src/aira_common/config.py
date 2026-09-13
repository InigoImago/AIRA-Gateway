"""Base settings shared by AIRA services.

Read from ``AIRA_``-prefixed environment variables and an optional ``.env`` file, with Vault ranked
above both (`FRD-116`). Component settings subclass :class:`BaseAiraSettings`.
"""

from __future__ import annotations

from typing import Any

from pydantic import field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from aira_common.secrets import load_secrets


class VaultSource(PydanticBaseSettingsSource):
    """Settings read from Vault, ranked above the environment (`FRD-116` FR-3).

    A settings *source* rather than an injection into ``os.environ``: environment values are
    readable from `/proc`, inherited by every subprocess and dumped by crash handlers. Loaded once
    and cached, because Vault is a start-up dependency and never a request-path one (FR-5).
    """

    _cache: dict[str, str] | None = None

    @classmethod
    def reset(cls) -> None:
        """Forget the loaded secrets. For tests, which must not share a cache across cases."""
        cls._cache = None

    def _secrets(self) -> dict[str, str]:
        if VaultSource._cache is None:
            # Whatever this raises is a boot failure by design (`FRD-116` §5.3).
            VaultSource._cache = load_secrets()
        return VaultSource._cache

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        prefix = self.config.get("env_prefix", "")
        secrets = self._secrets()
        for candidate in (f"{prefix}{field_name}".upper(), field_name.upper()):
            if candidate in secrets:
                return secrets[candidate], field_name, False
        return None, field_name, False

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

    One per installation, because prices come from a single provider contract. Display only; no
    conversion happens.
    """

    otel_enabled: bool = False
    """Enable OpenTelemetry export (traces/metrics/logs) via OTLP (see FRD-001)."""

    otel_endpoint: str = "http://localhost:4318"
    """OTLP/HTTP endpoint of the OpenTelemetry Collector."""

    otel_sample_ratio: float = 1.0
    """Trace sampling ratio (parent-based); 1.0 = sample everything."""

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
