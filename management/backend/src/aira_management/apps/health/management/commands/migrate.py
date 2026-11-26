"""``manage.py migrate``, saying what it did in one line (`FRD-631` FR-1).

Django's own command reports each migration as a line of text on stdout — the only lines of the
container in another format, and none of them through the export, so a failed upgrade was visible
only to whoever read the container's output. This is Django's command, with its progress counted
instead of printed (unless asked for with ``-v 2``), and one line when it ends:
``migrations_applied`` with what it applied and how long it took, or ``migrations_failed`` — handed
to the export before the process ends.
"""

from __future__ import annotations

import time
from typing import Any

from django.core.management.commands.migrate import Command as DjangoMigrate

from aira_common.logging import get_logger
from aira_common.observability import flush_telemetry
from aira_management.config.runtime import get_settings

_log = get_logger("aira_management.migrations")


class Command(DjangoMigrate):
    def handle(self, *args: Any, **options: Any) -> Any:
        self._applied: list[str] = []
        started = time.perf_counter()
        if get_settings().log_json and options.get("verbosity", 1) == 1:
            # The progress becomes the line below; asked for explicitly, Django still prints it.
            options = {**options, "verbosity": 0}
        try:
            result = super().handle(*args, **options)
        except Exception as exc:
            _log.error(
                "migrations_failed",
                plane="management",
                migrations=list(self._applied),
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
                exc_info=exc,
            )
            raise
        else:
            _log.info(
                "migrations_applied",
                plane="management",
                migrations=list(self._applied),
                count=len(self._applied),
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )
            return result
        finally:
            flush_telemetry()

    def migration_progress_callback(
        self, action: str, migration: Any = None, fake: bool = False
    ) -> None:
        if action == "apply_success" and migration is not None:
            self._applied.append(f"{migration.app_label}.{migration.name}")
        super().migration_progress_callback(action, migration, fake)
