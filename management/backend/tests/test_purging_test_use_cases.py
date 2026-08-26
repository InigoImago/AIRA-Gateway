"""The clean-up command that deletes rows, and the three things that stop it.

`purge_test_use_cases` exists so a browser run leaves nothing behind (`e2e/teardown.ts`), and it
**deletes**. `FRD-607` put thirty days between retiring and purging because *"the party who might
want the record gone is not the party who may remove it"* — so a command that skips that wait needs
its own rails, and rails nobody breaks on purpose are decoration.

Each test here removes one rail and checks the command refuses.
"""

from __future__ import annotations

from io import StringIO

import pytest
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.apps.usecases.management.commands import purge_test_use_cases as cmd
from aira_management.apps.usecases.models import UseCase
from aira_management.config.app_settings import ManagementSettings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _retired(slug: str) -> UseCase:
    return UseCase.objects.create(slug=slug, name=slug, deleted_at=timezone.now())


def _live(slug: str) -> UseCase:
    return UseCase.objects.create(slug=slug, name=slug)


def test_it_refuses_outside_a_demo_installation(monkeypatch) -> None:
    """The same guard `seed_demo` has, for the same reason: this deletes rows that are evidence."""
    monkeypatch.setattr(cmd, "get_settings", lambda: ManagementSettings(environment="production"))
    _retired("gone-1")

    with pytest.raises(CommandError, match="Refusing to purge"):
        call_command("purge_test_use_cases", "gone-1")

    assert UseCase.objects.filter(slug="gone-1").exists(), "it deleted before refusing"


def test_force_is_the_way_past_that_guard_and_it_is_explicit(monkeypatch) -> None:
    monkeypatch.setattr(cmd, "get_settings", lambda: ManagementSettings(environment="production"))
    _retired("gone-2")

    call_command("purge_test_use_cases", "gone-2", "--force", stdout=StringIO())

    assert not UseCase.objects.filter(slug="gone-2").exists()


def test_a_live_use_case_is_left_alone_and_named(monkeypatch) -> None:
    """**The rail that matters most.** Purging in one step would rebuild the hole `FRD-607` closes
    behind a management command instead of a longer URL — and a suite whose slug happens to collide
    with something a person made must not take it."""
    monkeypatch.setattr(cmd, "get_settings", lambda: ManagementSettings(demo_mode=True))
    _live("somebodys-work")
    out = StringIO()

    call_command("purge_test_use_cases", "somebodys-work", stdout=out)

    assert UseCase.objects.filter(slug="somebodys-work").exists()
    assert "somebodys-work" in out.getvalue()
    assert "not retired" in out.getvalue()


def test_a_retired_use_case_is_purged_and_announced(monkeypatch) -> None:
    """The gateway keeps the last row for a retired use case — its retention period is read from
    it — so a purge that does not announce itself leaves the read-model answering for a record
    Management no longer has."""
    monkeypatch.setattr(cmd, "get_settings", lambda: ManagementSettings(demo_mode=True))
    _retired("gone-3")
    OutboxEvent.objects.all().delete()

    call_command("purge_test_use_cases", "gone-3", stdout=StringIO())

    assert not UseCase.objects.filter(slug="gone-3").exists()
    announced = OutboxEvent.objects.filter(event_type="usecase.purged").values_list(
        "key", flat=True
    )
    assert list(announced) == ["gone-3"]


def test_a_slug_that_is_already_gone_is_not_an_error(monkeypatch) -> None:
    """A run that failed before creating one, or a use case a test removed itself. Tidying must not
    fail again, or the failure a reader sees is the clean-up rather than the defect."""
    monkeypatch.setattr(cmd, "get_settings", lambda: ManagementSettings(demo_mode=True))
    out = StringIO()

    call_command("purge_test_use_cases", "never-existed", stdout=out)

    assert "1 already gone" in out.getvalue()


def test_naming_nothing_does_nothing(monkeypatch) -> None:
    """Said out loud rather than exiting quietly: a clean-up that reports nothing is
    indistinguishable from one that is not running."""
    monkeypatch.setattr(cmd, "get_settings", lambda: ManagementSettings(demo_mode=True))
    _retired("gone-4")
    out = StringIO()

    call_command("purge_test_use_cases", stdout=out)

    assert "nothing named" in out.getvalue()
    assert UseCase.objects.filter(slug="gone-4").exists()


def test_a_register_that_is_not_there_is_an_error_not_a_silence(monkeypatch, tmp_path) -> None:
    """A teardown pointed at a register that does not exist has cleaned nothing, and reporting
    success would be the "guard that cannot fail" shape one layer out."""
    monkeypatch.setattr(cmd, "get_settings", lambda: ManagementSettings(demo_mode=True))

    with pytest.raises(CommandError, match="No such file"):
        call_command("purge_test_use_cases", "--from-file", str(tmp_path / "absent.txt"))


def test_the_register_file_is_read_and_blank_lines_ignored(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cmd, "get_settings", lambda: ManagementSettings(demo_mode=True))
    _retired("from-file-1")
    _retired("from-file-2")
    register = tmp_path / "use-cases.txt"
    register.write_text("from-file-1\n\n  from-file-2  \n", encoding="utf-8")

    call_command("purge_test_use_cases", "--from-file", str(register), stdout=StringIO())

    assert not UseCase.objects.filter(slug__startswith="from-file").exists()
