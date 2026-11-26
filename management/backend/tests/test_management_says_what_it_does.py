"""Management says what it does in lines, as every process does (`FRD-631`).

Its access line was uvicorn's text, outside the export, with the address and the query string; a
refused sign-in was a status and nothing else; a stop was silent, because Django's ASGI application
speaks no lifespan; and a migration printed its progress as text. Each is a line through the
pipeline now:

- ``http_request`` per request, with the route Django matched — never the name in the path — the
  status, the duration and who made it;
- ``auth_refused`` for a refused sign-in or permission, bounded per source;
- ``process_stopping`` and ``process_stopped`` when uvicorn says the process ends, and what the
  export holds handed over;
- ``migrations_applied`` for a migration run, and nothing printed beside it;
- ``unhandled_error`` with the error's type and place, never its message below `DEBUG`.
"""

from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
import structlog
from aira_management.apps.api import exceptions as api_exceptions
from aira_management.config import lifespan
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from .conftest import role_claims

pytestmark = pytest.mark.django_db


def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loggers first used now: one an earlier test first used keeps the configuration of then."""
    import aira_common.logging.access as access
    import aira_common.logging.refusals as refusals

    monkeypatch.setattr(access, "_log", structlog.get_logger("aira.access"))
    monkeypatch.setattr(refusals, "_log", structlog.get_logger("aira.security"))
    monkeypatch.setattr(api_exceptions, "_log", structlog.get_logger("aira_management"))


def _user(username: str, *roles: str) -> Any:
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    return get_user_model().objects.get(pk=user.pk)


def test_a_request_is_an_access_line_with_its_route_and_its_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fresh(monkeypatch)
    client = APIClient()
    client.force_authenticate(user=_user("boss", "global-admin"))

    with structlog.testing.capture_logs() as logs:
        client.delete("/api/v1/use-cases/uc-x/members/max.mustermann/?q=johannes")
        client.get("/healthz")

    (line,) = [entry for entry in logs if entry["event"] == "http_request"]
    assert (line["method"], line["status"], line["actor"]) == ("DELETE", 404, "boss")
    assert "members" in line["route"] and "mustermann" not in json.dumps(line)
    assert "johannes" not in json.dumps(line)
    assert isinstance(line["duration_ms"], float)


def test_a_refused_sign_in_is_a_line(monkeypatch: pytest.MonkeyPatch) -> None:
    _fresh(monkeypatch)

    with structlog.testing.capture_logs() as logs:
        response = APIClient().get("/api/v1/me", HTTP_AUTHORIZATION="Bearer whatever")

    assert response.status_code == 401
    (line,) = [entry for entry in logs if entry["event"] == "auth_refused"]
    assert (line["category"], line["outcome"], line["status"]) == (
        "authentication",
        "unauthenticated",
        401,
    )
    assert line["route"] and "subject" not in line


def test_a_refused_permission_is_a_line_naming_who(monkeypatch: pytest.MonkeyPatch) -> None:
    _fresh(monkeypatch)
    client = APIClient()
    client.force_authenticate(user=_user("nobody"))

    with structlog.testing.capture_logs() as logs:
        response = client.get("/api/v1/roles/")

    assert response.status_code == 403
    (line,) = [entry for entry in logs if entry["event"] == "auth_refused"]
    assert (line["category"], line["outcome"], line["subject"]) == (
        "authorization",
        "forbidden",
        "nobody",
    )


def test_an_unexpected_error_says_its_type_and_place_and_not_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aira_management.apps.api import views

    def boom() -> None:
        raise RuntimeError("Key (username)=(max.mustermann) already exists")

    _fresh(monkeypatch)
    monkeypatch.setattr(views, "role_definitions", boom)
    client = APIClient()
    client.force_authenticate(user=_user("boom-user"))

    with structlog.testing.capture_logs() as logs:
        assert client.get("/api/v1/me").status_code == 500

    (line,) = [entry for entry in logs if entry["event"] == "unhandled_error"]
    assert isinstance(line["exc_info"], RuntimeError)
    assert line["route"] == "api/v1/me"
    assert "error" not in line and "path" not in line


def test_a_stop_is_said_and_the_export_handed_over(monkeypatch: pytest.MonkeyPatch) -> None:
    """Django's ASGI application answers no lifespan, so uvicorn stopped the process without a
    word; now the protocol is answered and the stop is two lines and a flush."""
    flushed: list[bool] = []
    monkeypatch.setattr(lifespan, "flush_telemetry", lambda: flushed.append(True) or True)
    monkeypatch.setattr(
        "aira_common.logging.lifecycle._log", structlog.get_logger("aira_common.lifecycle")
    )
    passed: list[str] = []

    async def django(scope: Any, receive: Any, send: Any) -> None:
        passed.append(scope["type"])

    application = lifespan.with_lifespan(django, service="aira-management")
    incoming = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return incoming.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    with structlog.testing.capture_logs() as logs:
        asyncio.run(application({"type": "lifespan"}, receive, send))
        asyncio.run(application({"type": "http"}, receive, send))

    assert [message["type"] for message in sent] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]
    assert [entry["event"] for entry in logs] == ["process_stopping", "process_stopped"]
    assert flushed == [True]
    assert passed == ["http"], "every other scope is Django's, unchanged"


def test_a_migration_run_is_one_line_and_prints_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aira_management.apps.health.management.commands import migrate

    monkeypatch.setattr(migrate, "_log", structlog.get_logger("aira_management.migrations"))
    out = StringIO()

    with structlog.testing.capture_logs() as logs:
        call_command("migrate", stdout=out, verbosity=1)

    (line,) = [entry for entry in logs if entry["event"] == "migrations_applied"]
    assert (line["plane"], line["count"]) == ("management", 0)
    assert out.getvalue() == ""


@pytest.mark.parametrize(
    ("argv", "process"),
    [
        (["/app/manage.py", "relay", "--loop"], "relay"),
        (["manage.py", "migrate"], "migrate"),
        (
            ["/app/.venv/lib/python3.14/site-packages/django/__main__.py", "purge_test_use_cases"],
            "purge_test_use_cases",
        ),
        (["/app/.venv/bin/django-admin", "seed_demo"], "seed_demo"),
        (["/app/.venv/bin/uvicorn", "aira_management.config.asgi:application"], "api"),
        (["manage.py", "--version"], "api"),
        (["manage.py"], "api"),
        ([], "api"),
    ],
)
def test_a_command_is_named_after_itself_however_it_was_started(
    argv: list[str], process: str
) -> None:
    """The name is every line's `process` and, for a change no person made, the history's
    answer to who made it — so a command started as `python -m django` is not the API."""
    from aira_management.config.observability import process_of

    assert process_of(argv) == process


def test_the_process_is_named_before_its_settings_are_read() -> None:
    """Reading the settings from Vault writes a process's first lines; Django runs `settings.py`
    as the process starts, so the order is read from it."""
    from aira_management.config import settings as module

    source = Path(str(module.__file__)).read_text("utf-8")

    assert source.index("process_named(process_of(sys.argv))") < source.index("= get_settings()")


def test_django_adds_no_handler_that_prints_beside_the_pipeline() -> None:
    """Django's default logging gives its loggers a console handler that prints a failed request
    as text on stderr whenever `DEBUG` is on — a second, unparsed copy of a line the pipeline
    already renders. It configures nothing here; its records reach the root logger."""
    import logging

    from django.conf import settings

    assert settings.LOGGING_CONFIG is None
    # The test runner's own capturing handlers aside, Django's loggers hold none.
    assert not [
        handler
        for name in ("django", "django.request", "django.server")
        for handler in logging.getLogger(name).handlers
        if not type(handler).__module__.startswith("_pytest")
    ]
