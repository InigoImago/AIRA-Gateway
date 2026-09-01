"""Base application settings shared by AIRA components.

Settings are read from environment variables (prefixed ``AIRA_``) and an optional
``.env`` file, following 12-factor configuration. Component-specific settings subclass
:class:`BaseAiraSettings` and add their own fields.
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

    A settings *source* rather than an injection into ``os.environ``, and the distinction is the
    security half of the feature: values placed in the environment are readable from `/proc`, are
    inherited by every subprocess, and reach any library that dumps the environment on a crash.
    Here they exist only inside the settings object.

    Loaded **once**, on first construction, and cached — reading Vault per settings object would
    make the number of calls depend on how often somebody happens to construct one, and `FRD-116`
    FR-5 is explicit that Vault is a startup dependency and never a request-path one.
    """

    _cache: dict[str, str] | None = None

    @classmethod
    def reset(cls) -> None:
        """Forget the loaded secrets. For tests, which must not share a cache across cases."""
        cls._cache = None

    def _secrets(self) -> dict[str, str]:
        if VaultSource._cache is None:
            # Whatever this raises is a boot failure by design: a configured Vault that cannot be
            # read must not degrade into "carry on with the environment" (`FRD-116` §5.3).
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

        Docker Compose passes optional variables as `${AIRA_X:-}`, which expands to an *empty
        string* when nobody set one. For a string setting that is harmless and is what the whole
        compose file already relies on. For a number it is fatal: pydantic cannot parse `""` as a
        float, and the process refuses to start with a validation error naming a variable the
        operator never touched.

        Found on 2026-08-08 by adding two timeout settings to the compose file the same way every
        string setting is added, and watching the gateway stop booting. The idiom is not going to
        change — so the settings tolerate it, and only where it cannot mean anything else.

        Deliberately **not** applied to `str` fields: there, an empty value is a real answer, and
        dropping it would silently substitute a non-empty default for a deployment that meant to
        clear the setting (`AIRA_CORS_ORIGINS=` is exactly that).
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
        """Explicit arguments still win, then **Vault**, then the environment.

        Init arguments stay first because that is how the tests construct settings, and a test
        that could not override a value would be testing the deployment rather than the code.
        Vault above the environment is FR-3: a key present in Vault wins, a key absent from it
        falls back — and there is no third source.
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
    """How long a newly issued API key lives, in days (`ADR-0015`, 2026-08-08).

    **A key is always bounded.** The first version of this made an expiry optional with "NULL means
    never", on the argument that an expiry which cannot be omitted is one somebody sets to the year
    3000. That argument is about the *maximum*, not about the default: the answer is a bound on
    both ends, not an opt-in. A credential with no end date has to be inventoried by a person who
    remembers to, and nobody does.

    Shared by both planes on purpose — Management issues keys, the gateway's CLI mints the
    break-glass one, and a policy with two definitions is a policy with two answers.
    """

    api_key_max_days: int = 180
    """The longest lifetime anybody may ask for.

    A ceiling rather than a fixed term, because integrations differ and a rotation everybody has to
    do on the same day is a rotation that gets postponed. Asking for more is **refused by name**,
    with the maximum in the message — a silently truncated lifetime would have the requester
    believing a date that is not the one in the database.
    """

    currency: str = "EUR"
    """Currency all prices and cost budgets are expressed in (FRD-403).

    One currency per installation: prices come from a single provider contract, so quoting some
    of them in another currency would require exchange rates and a rate date per booking — a
    standing source of figures nobody can reconcile. Display only; no conversion happens.
    """

    otel_enabled: bool = False
    """Enable OpenTelemetry export (traces/metrics/logs) via OTLP (see FRD-001)."""

    otel_endpoint: str = "http://localhost:4318"
    """OTLP/HTTP endpoint of the OpenTelemetry Collector."""

    otel_sample_ratio: float = 1.0
    """Trace sampling ratio (parent-based); 1.0 = sample everything."""

    debug_integrations: str = ""
    """Which external systems to narrate one line per call for (`FRD-617`).

    A comma-separated selection from `otel`, `kafka`, `auth`, `vault`, `redis`, `postgres` — or
    `all`. **Empty is off and is the default**, which is what a working installation runs; this
    exists for the days when something is being integrated and *"did we send it, and did it
    arrive"* has no answer anywhere.

    Off is genuinely off: a call site then costs one set membership test and emits nothing. On
    needs no second switch — the lines go out at `INFO`, so nobody has to discover that
    `AIRA_LOG_LEVEL=DEBUG` is also required before the feature appears to work.
    """

    @field_validator("debug_integrations")
    @classmethod
    def _integrations_are_known(cls, value: str) -> str:
        """A misspelled system name refuses the process, rather than watching nothing.

        `LESSONS.md` §3: a setting that silently means nothing is worse than one that is missing,
        because the operator concludes the *feature* is broken and stops using it. Imported here
        rather than at module scope so that reading settings does not pull in the OpenTelemetry
        SDK by way of the logging module.
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
