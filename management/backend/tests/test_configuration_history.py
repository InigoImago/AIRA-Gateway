"""Who changed which configuration, when, and from what to what (`FRD-632`).

Adding somebody to a use case left an access line and `published 1 events` — neither in the log
store, and neither saying who added whom with which role. Every change the gateway enforces is
announced on one hook, from inside its transaction; the history subscribes there:

- a row per change, with the actor and the person in plain text and the state before and after,
  readable only by the roles that may see people;
- a `config.changed` line once the transaction commits, naming the kind, the id, the action and the
  fields — never a value, and the people as the pipeline writes them;
- nothing for a change that rolled back.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
import structlog
from aira_management.apps.history import recorder
from aira_management.apps.history.models import ConfigurationChange
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APIClient

from .conftest import role_claims, setting

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"
HISTORY = "/api/v1/configuration-changes/"


def _user(username: str, *roles: str) -> Any:
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    return get_user_model().objects.get(pk=user.pk)


def _client(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def admin() -> Any:
    return _user("boss", "global-admin")


@pytest.fixture
def usecase() -> UseCase:
    return UseCase.objects.create(slug="test-otel-uc", name="OTel")


def _lines(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == "config.changed"]


def test_adding_a_member_is_a_row_naming_who_added_whom_and_a_line_naming_no_value(
    admin: Any,
    usecase: UseCase,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks,
) -> None:
    _user("ada")
    monkeypatch.setattr(recorder, "_log", structlog.get_logger("aira_management.history"))

    with structlog.testing.capture_logs() as logs, django_capture_on_commit_callbacks(execute=True):
        response = _client(admin).post(
            f"{BASE}{usecase.slug}/members/", {"username": "ada", "role": "user"}, format="json"
        )
    assert response.status_code in (200, 201), response.content

    (row,) = ConfigurationChange.objects.filter(kind="membership")
    assert (row.actor, row.person, row.use_case, row.action) == (
        "boss",
        "ada",
        "test-otel-uc",
        "created",
    )
    assert row.before is None and row.after["role"] == "user"
    assert row.object_key == "test-otel-uc|ada"

    (line,) = [line for line in _lines(logs) if line["object"] == "membership"]
    assert (line["category"], line["action"], line["object_id"]) == (
        "configuration",
        "created",
        "test-otel-uc",
    )
    assert (line["actor"], line["person"], line["granted_role"]) == ("boss", "ada", "user")
    assert line["change_id"] == row.pk
    assert "after" not in line and "before" not in line


def test_a_second_change_records_what_was_there_before_and_which_fields_moved(
    admin: Any, usecase: UseCase
) -> None:
    _user("ada")
    client = _client(admin)
    client.post(
        f"{BASE}{usecase.slug}/members/", {"username": "ada", "role": "user"}, format="json"
    )
    client.post(
        f"{BASE}{usecase.slug}/members/", {"username": "ada", "role": "admin"}, format="json"
    )

    latest = ConfigurationChange.objects.filter(kind="membership").first()
    assert latest is not None
    assert latest.action == "updated"
    assert (latest.before["role"], latest.after["role"]) == ("user", "admin")
    assert latest.fields_changed == ["role"]


def test_removing_a_member_is_recorded_as_removed_with_what_it_ended(
    admin: Any, usecase: UseCase
) -> None:
    _user("ada")
    client = _client(admin)
    client.post(
        f"{BASE}{usecase.slug}/members/", {"username": "ada", "role": "user"}, format="json"
    )
    client.delete(f"{BASE}{usecase.slug}/members/ada/")

    removal = ConfigurationChange.objects.filter(event_type="membership.removed").first()
    assert removal is not None and removal.action == "removed"
    assert removal.before is not None and removal.before["role"] == "user"


def test_a_change_that_rolled_back_leaves_no_row_and_no_line(
    monkeypatch: pytest.MonkeyPatch, django_capture_on_commit_callbacks
) -> None:
    monkeypatch.setattr(recorder, "_log", structlog.get_logger("aira_management.history"))
    with (
        structlog.testing.capture_logs() as logs,
        django_capture_on_commit_callbacks(execute=True),
        pytest.raises(RuntimeError),
        transaction.atomic(),
    ):
        events.emit("budget.upserted", {"id": 7, "use_case": "uc", "period": "monthly"})
        raise RuntimeError("the view failed after its emit")

    assert not ConfigurationChange.objects.exists()
    assert _lines(logs) == []


def test_what_verifies_a_key_is_never_kept() -> None:
    events.emit(
        "api_key.created",
        {"prefix": "aira_ab12", "key_hash": "sha256$secret", "subject": "ada", "use_case": "uc"},
    )

    (row,) = ConfigurationChange.objects.all()
    assert "key_hash" not in row.after and row.person == "ada"


def test_a_change_no_person_made_names_the_process_that_made_it() -> None:
    from aira_common.logging import process_named

    process_named("seed")
    events.emit("model.upserted", {"name": "mock-1", "approved": True})

    (row,) = ConfigurationChange.objects.all()
    assert (row.actor, row.process, row.action) == ("", "seed", "saved")


@pytest.mark.parametrize(
    ("roles", "status"),
    [(("global-admin",), 200), (("it-security",), 200), (("it-steuerung",), 403), ((), 403)],
)
def test_only_the_roles_that_may_see_people_read_the_history(
    roles: tuple[str, ...], status: int
) -> None:
    """Every row names who changed something, and many whom it was about."""
    events.emit("membership.upserted", {"slug": "uc", "username": "ada", "role": "user"})

    response = _client(_user("reader", *roles)).get(HISTORY)

    assert response.status_code == status
    if status == 200:
        (row,) = response.data["results"]
        assert (row["kind"], row["person"], row["after"]["role"]) == ("membership", "ada", "user")


def test_the_history_is_filtered_by_kind_and_use_case(admin: Any) -> None:
    events.emit("membership.upserted", {"slug": "uc-a", "username": "ada", "role": "user"})
    events.emit("membership.upserted", {"slug": "uc-b", "username": "bob", "role": "user"})
    events.emit("model.upserted", {"name": "mock-1"})

    by_case = _client(admin).get(HISTORY, {"use_case": "uc-a"}).data["results"]
    by_kind = _client(admin).get(HISTORY, {"kind": "model"}).data["results"]

    assert [row["person"] for row in by_case] == ["ada"]
    assert [row["object_key"] for row in by_kind] == ["mock-1"]


def test_the_relay_deletes_what_is_older_than_the_installation_keeps(admin: Any) -> None:
    from aira_management.apps.outbox.management.commands.relay import sweep_history

    events.emit("model.upserted", {"name": "old"})
    events.emit("model.upserted", {"name": "new"})
    ConfigurationChange.objects.filter(object_key="old").update(
        changed_at=timezone.now() - timedelta(days=731)
    )

    with setting(config_history_days=730):
        assert sweep_history(timezone.now()) == 1

    assert [row.object_key for row in ConfigurationChange.objects.all()] == ["new"]


def test_the_relay_is_what_keeps_the_history_to_its_period(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deletion runs on the relay's every run, whether or not anybody opens the console."""
    from io import StringIO

    import aira_management.apps.outbox.management.commands.relay as relay
    from django.core.management import call_command

    from aira_common.kafka import InMemoryProducer

    events.emit("model.upserted", {"name": "old"})
    ConfigurationChange.objects.update(changed_at=timezone.now() - timedelta(days=731))
    monkeypatch.setattr(relay, "build_producer", InMemoryProducer)

    call_command("relay", stdout=StringIO())

    assert not ConfigurationChange.objects.exists()
