"""Database configuration builder.

Kept separate from ``settings.py`` so both the Postgres and the test (in-memory SQLite)
branches are unit-testable. ``settings.py`` passes ``use_sqlite=True`` under pytest so the
suite is hermetic and does not require a running Postgres.
"""

from __future__ import annotations

from typing import Any

from aira_management.config.app_settings import ManagementSettings


def build_databases(settings: ManagementSettings, use_sqlite: bool) -> dict[str, dict[str, Any]]:
    """Return Django ``DATABASES``; ``use_sqlite`` selects an in-memory SQLite DB."""
    if use_sqlite:
        return {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
    return {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": settings.postgres_db,
            "USER": settings.postgres_user,
            "PASSWORD": settings.postgres_password,
            "HOST": settings.postgres_host,
            "PORT": str(settings.postgres_port),
        }
    }
