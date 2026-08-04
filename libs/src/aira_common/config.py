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
