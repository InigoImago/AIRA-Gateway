"""Remove the use cases a test suite created, by name, in a demo installation.

## Why this exists beside the API's own purge

`FRD-607` splits *retire* from *purge* and puts thirty days between them, because the party who
might want a record gone is not the party who may remove it, and *"a decision that can be taken in
the same minute as the deletion is not a second decision"*. **That rule is untouched here** — this
command is not reachable over HTTP, and the endpoint still refuses for thirty days.

What it answers is a different question. The browser suite creates a use case per test and had no
way to remove one: after a few sessions the demo held **1734 use cases in Management and 1946 in
the gateway's read-model**, four of them the demo's own. Its teardown now retires what it made,
which is the product's own path and takes them off every screen that lists live use cases — but a
tombstone is kept on purpose, and a suite that leaves one behind per test still fills the register
that shows retired ones.

So the last step is here, where it can be guarded the way `seed_demo` is: demo installations only.
The precedent is that command's `--fresh`, which already deletes **every** use case that is not one
of the demo's own.

## What it will not do

- **Anything outside a demo or local installation.** Same guard as `seed_demo`, same reason.
- **Anything that is not already retired.** Purging in one step would rebuild the hole `FRD-607`
  closes, behind a management command instead of a longer URL.
- **Anything it was not given by name.** No pattern, no prefix, no "everything that looks
  generated". A person may call a use case whatever they like — this demo holds two that a person
  made — and a sweep by shape is how a clean-up takes somebody's work with it.
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
                # Not silence. A teardown pointed at a register that is not there has cleaned
                # nothing, and reporting success would be the "guard that cannot fail" shape.
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
                # Named but still live. Left alone and **reported**, because the caller believes
                # this slug is theirs and it is not — either the suite failed to retire it, or the
                # name collides with something a person made.
                live.append(slug)
                continue
            with transaction.atomic():
                usecase.delete()
                # The second event, as the endpoint sends: `usecase.deleted` ended access and kept
                # the tombstone; this says the record itself is gone, so the gateway drops the last
                # row it kept.
                emit("usecase.purged", {"slug": slug})
            purged += 1

        self.stdout.write(f"[purge] {purged} purged, {absent} already gone, {len(live)} still live")
        if live:
            self.stdout.write(
                self.style.WARNING("[purge] not retired, so not purged: " + ", ".join(live))
            )
