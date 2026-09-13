"""Remove the use cases a test suite created, by name, in a demo installation.

The browser suite retires each use case it creates, which takes it off every live list but keeps
its tombstone (`FRD-607`); this removes those tombstones. The API's thirty-day wait before a purge
is untouched — this command is not reachable over HTTP. It will not act:

- **outside a demo or local installation** — the same guard as `seed_demo`;
- **on anything not already retired** — purging in one step would rebuild the hole `FRD-607`
  closes;
- **on anything it was not given by name** — no pattern or prefix, because a sweep by shape is how
  a clean-up takes somebody's work with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase
from aira_management.config.runtime import get_settings
from aira_management.config.security import is_local


class Command(BaseCommand):
    help = "Purge named, already-retired use cases. Demo installations only."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("slugs", nargs="*", help="Slugs to purge.")
        parser.add_argument(
            "--from-file",
            help="A file of slugs, one per line — the register the browser suite writes.",
        )
        parser.add_argument(
            "--force", action="store_true", help="Allow running outside local/demo environments."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        settings = get_settings()
        if not (is_local(settings) or settings.demo_mode) and not options["force"]:
            raise CommandError(
                f"Refusing to purge use cases in environment '{settings.environment}' "
                "(set AIRA_DEMO_MODE, or pass --force if you really mean it)."
            )

        wanted = list(options["slugs"])
        source: str | None = options.get("from_file")
        if source:
            path = Path(source)
            if not path.is_file():
                # A teardown pointed at a missing register has cleaned nothing; say so.
                raise CommandError(f"No such file: {path}")
            wanted += [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]

        names = sorted({slug for slug in wanted if slug})
        if not names:
            self.stdout.write("[purge] nothing named; nothing done")
            return

        purged, live, absent = 0, [], 0
        for slug in names:
            usecase = UseCase.objects.filter(slug=slug).first()
            if usecase is None:
                absent += 1
                continue
            if usecase.deleted_at is None:
                # Named but live: left alone and reported — the suite failed to retire it, or the
                # name collides with something a person made.
                live.append(slug)
                continue
            with transaction.atomic():
                usecase.delete()
                # As the purge endpoint sends: the gateway drops the last row it kept.
                emit("usecase.purged", {"slug": slug})
            purged += 1

        self.stdout.write(f"[purge] {purged} purged, {absent} already gone, {len(live)} still live")
        if live:
            self.stdout.write(
                self.style.WARNING("[purge] not retired, so not purged: " + ", ".join(live))
            )
