"""Base application settings shared by AIRA components.

Settings are read from environment variables (prefixed ``AIRA_``) and an optional
``.env`` file, following 12-factor configuration. Component-specific settings subclass
:class:`BaseAiraSettings` and add their own fields.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAiraSettings(BaseSettings):
    """Common configuration fields for every AIRA service."""

    model_config = SettingsConfigDict(
        env_prefix="AIRA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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
