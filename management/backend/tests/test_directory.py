"""The directory search endpoint (`FRD-209` §3).

What the *endpoint* owns, as distinct from the Keycloak client it may or may not have: who may
ask, what an empty query does, and — the property this feature is careful about — that a degraded
answer says it is degraded rather than looking like "nothing matched".
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from aira_management.apps.directory import views as directory_views
from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from aira_common.access import SubjectKind
from aira_common.directory import DirectoryEntry, DirectoryUnavailable

from .conftest import role_claims

pytestmark = pytest.mark.django_db

URL = "/api/v1/directory/"


def _user(username: str, *roles: str) -> Any:
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    return user


def _client(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ---- who may ask ---------------------------------------------------------------------------


def test_it_needs_a_credential() -> None:
    assert APIClient().get(f"{URL}?q=ada").status_code == 401


def test_a_role_that_administers_nothing_may_not_search_the_directory() -> None:
    """A directory search lists people and departments. It is offered to whoever might grant
    access, not to everyone who can sign in."""
    assert _client(_user("nobody")).get(f"{URL}?q=ada").status_code == 403


def test_a_global_administrator_may() -> None:
    """`directory.search`, held through the role."""
    assert _client(_user("a", "global-admin")).get(f"{URL}?q=ada").status_code == 200


def test_the_administrator_of_one_use_case_may() -> None:
    """No role at all — an object grant on one use case, which is what the people who add members
    hold. The branch `MaySearchDirectory` exists for, and a Global Administrator never reaches."""
    from aira_management.apps.usecases.models import UseCaseMembership
    from aira_management.apps.usecases.views import _grant

    admin = _user("uc-admin")
    _grant(admin, UseCase.objects.create(slug="uc-a", name="A"), UseCaseMembership.ADMIN)

    assert _client(admin).get(f"{URL}?q=ada").status_code == 200


def test_administering_only_a_retired_use_case_opens_no_directory() -> None:
    """Retiring a use case ends every access it granted (`FRD-607`), the picker its
    administration opened included."""
    from aira_management.apps.usecases.models import UseCaseMembership
    from aira_management.apps.usecases.views import _grant
    from django.utils import timezone

    admin = _user("uc-admin")
    retired = UseCase.objects.create(slug="uc-a", name="A", deleted_at=timezone.now())
    _grant(admin, retired, UseCaseMembership.ADMIN)

    assert _client(admin).get(f"{URL}?q=ada").status_code == 403


# ---- what an empty query does ---------------------------------------------------------------


def test_one_letter_returns_nothing_and_says_why() -> None:
    """A picker that dumps the whole directory the moment it is focused is a picker nobody reads,
    and on a real realm it is thousands of rows."""
    body = _client(_user("a", "global-admin")).get(f"{URL}?q=a").data

    assert body["results"] == []
    assert body["source"] == "none"
    assert "two letters" in body["hint"]


def test_no_query_at_all_is_the_same() -> None:
    assert _client(_user("a", "global-admin")).get(URL).data["results"] == []


# ---- the local fallback ---------------------------------------------------------------------


def test_without_an_admin_client_it_answers_from_what_management_knows() -> None:
    """Enough to run the demo and to re-grant an existing group — and it cannot invent one."""
    admin = _user("a", "global-admin")
    _user("ada")
    usecase = UseCase.objects.create(slug="uc-a", name="uc-a")
    UseCaseGroupGrant.objects.create(use_case=usecase, group_path="/ai/kundenservice")

    body = _client(admin).get(f"{URL}?q=ada").data

    assert body["source"] == "local"
    assert any(row["id"] == "ada" and row["kind"] == "user" for row in body["results"])


def test_the_local_fallback_offers_a_group_already_granted_somewhere() -> None:
    admin = _user("a", "global-admin")
    usecase = UseCase.objects.create(slug="uc-a", name="uc-a")
    UseCaseGroupGrant.objects.create(use_case=usecase, group_path="/ai/kundenservice")

    body = _client(admin).get(f"{URL}?q=kunden").data

    assert body["results"][0]["kind"] == "group"
    assert body["results"][0]["id"] == "/ai/kundenservice"


def test_the_local_fallback_cannot_invent_a_group_nobody_has_used() -> None:
    admin = _user("a", "global-admin")

    assert _client(admin).get(f"{URL}?q=vertrieb").data["results"] == []


def test_a_person_is_findable_by_name_and_by_address_not_only_by_username() -> None:
    admin = _user("a", "global-admin")
    get_user_model().objects.create(
        username="al", first_name="Ada", last_name="Lovelace", email="ada@example.org"
    )

    for query in ("Ada", "Lovelace", "example.org"):
        found = _client(admin).get(f"{URL}?q={query}").data["results"]
        assert [row["id"] for row in found] == ["al"], query


def test_it_never_returns_a_credential() -> None:
    """A directory entry is a name and a way to tell two of them apart. Nothing else."""
    admin = _user("a", "global-admin")
    user = get_user_model().objects.create(username="ada", email="ada@example.org")
    user.set_password("hunter2")
    user.save()

    body = _client(admin).get(f"{URL}?q=ada").data

    assert set(body["results"][0]) == {"kind", "id", "label", "detail"}
    assert "hunter2" not in str(body)
    assert "pbkdf2" not in str(body)


# ---- when Keycloak is configured --------------------------------------------------------------


def test_a_configured_directory_is_used_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    class Fake:
        def search(self, query: str) -> list[DirectoryEntry]:
            return [DirectoryEntry(SubjectKind.GROUP, "/ai/kundenservice", "kundenservice", "/ai")]

    monkeypatch.setattr("aira_management.apps.directory.views._build_directory", lambda: Fake())
    body = _client(_user("a", "global-admin")).get(f"{URL}?q=kunden").data

    assert body["source"] == "keycloak"
    assert body["results"][0]["id"] == "/ai/kundenservice"


def test_an_unreachable_provider_falls_back_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A console that cannot search is a console where nobody can grant access. The local answer
    is a real subset, and the reader is told which one they are looking at."""

    class Broken:
        def search(self, query: str) -> list[DirectoryEntry]:
            raise DirectoryUnavailable("down")

    monkeypatch.setattr("aira_management.apps.directory.views._build_directory", lambda: Broken())
    usecase = UseCase.objects.create(slug="uc-a", name="uc-a")
    UseCaseGroupGrant.objects.create(use_case=usecase, group_path="/ai/kundenservice")

    body = _client(_user("a", "global-admin")).get(f"{URL}?q=kunden").data

    assert body["source"] == "local"
    assert body["results"][0]["id"] == "/ai/kundenservice"


def test_the_directory_is_asked_at_its_own_address_before_the_issuers(settings) -> None:
    """The issuer is the address a browser uses; inside a container it is rarely Keycloak."""
    from aira_management.apps.directory.service import build_directory

    settings.AIRA_OIDC_ISSUER_BASE = "http://localhost:8080"
    settings.AIRA_OIDC_REALM = "aira"
    settings.AIRA_DIRECTORY_CLIENT_ID = "aira-directory"
    settings.AIRA_DIRECTORY_CLIENT_SECRET = "s3cret"

    settings.AIRA_DIRECTORY_URL = "http://keycloak:8080"
    assert build_directory()._base == "http://keycloak:8080"  # type: ignore[union-attr]

    settings.AIRA_DIRECTORY_URL = ""
    assert build_directory()._base == "http://localhost:8080"  # type: ignore[union-attr]


# ---- an account that lives in another realm (`AIRA_DIRECTORY_TOKEN_REALM`) -----------------------
#
# The installation this was found on keeps its admin service accounts in `master`, with
# `view-users` and `query-groups` on its realm `preprod` through `master`'s `preprod-realm` client.
# Keycloak issues that account's token in `master` and accepts it on `/admin/realms/preprod/…`;
# `preprod` itself refuses the client as `invalid_client`. Its Keycloak is served under `/auth`, as
# Keycloak's older distributions are, so every path below carries it.

#: Where that Keycloak is served, and the context path every request carries.
KEYCLOAK = "https://idprovider.example/auth"
CONTEXT = "/auth"


def _keycloak_with_the_account_in(
    monkeypatch: pytest.MonkeyPatch, account_in: str
) -> list[tuple[str, str]]:
    """Every client the directory opens answers as that Keycloak, recording `(method, path)`."""
    asked: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append((request.method, path))
        if path.endswith("/protocol/openid-connect/token"):
            if path == f"{CONTEXT}/realms/{account_in}/protocol/openid-connect/token":
                return httpx.Response(200, json={"access_token": "master-token"})
            return httpx.Response(401, json={"error": "invalid_client"})
        if path == f"{CONTEXT}/admin/realms/preprod/groups":
            names = ("aira-t-compliance", "aira-t-global-admin", "aira-t-it-security")
            return httpx.Response(200, json=[{"name": n, "path": f"/{n}"} for n in names])
        if path == f"{CONTEXT}/admin/realms/preprod/users":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr(
        "aira_common.directory.httpx.Client",
        lambda **kwargs: real(transport=httpx.MockTransport(handler)),
    )
    return asked


@pytest.fixture
def preprod(settings) -> Any:
    settings.AIRA_OIDC_ISSUER_BASE = KEYCLOAK
    settings.AIRA_OIDC_REALM = "preprod"
    settings.AIRA_DIRECTORY_URL = ""
    settings.AIRA_DIRECTORY_CLIENT_ID = "aira-directory-preprod"
    settings.AIRA_DIRECTORY_CLIENT_SECRET = "s3cret"
    return settings


def test_an_account_kept_in_master_answers_the_search_from_keycloak(
    preprod: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    preprod.AIRA_DIRECTORY_TOKEN_REALM = "master"
    asked = _keycloak_with_the_account_in(monkeypatch, "master")

    body = _client(_user("a", "global-admin")).get(f"{URL}?q=aira").data

    assert body["source"] == "keycloak"
    assert [row["id"] for row in body["results"]] == [
        "/aira-t-compliance",
        "/aira-t-global-admin",
        "/aira-t-it-security",
    ]
    assert {path for method, path in asked if method == "POST"} == {
        f"{CONTEXT}/realms/master/protocol/openid-connect/token"
    }
    assert {path for method, path in asked if method == "GET"} == {
        f"{CONTEXT}/admin/realms/preprod/groups",
        f"{CONTEXT}/admin/realms/preprod/users",
    }


def _fresh_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """A logger first used inside the capture: the module's own may already be cached with the
    processors an earlier test configured, and would log past it."""
    monkeypatch.setattr(directory_views, "_log", structlog.get_logger(directory_views.__name__))


def test_asked_of_the_wrong_realm_the_search_falls_back_and_the_log_says_where(
    preprod: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the setting the token is asked of `preprod`, which refuses the account. The console
    sees a shorter list — the operator gets one line naming the step, the realm and the status,
    and neither the account nor its secret."""
    preprod.AIRA_DIRECTORY_TOKEN_REALM = ""
    _keycloak_with_the_account_in(monkeypatch, "master")

    with structlog.testing.capture_logs() as logs:
        _fresh_log(monkeypatch)
        body = _client(_user("a", "global-admin")).get(f"{URL}?q=aira").data

    assert body["source"] == "local"
    (line,) = [entry for entry in logs if entry["event"] == "directory.unavailable"]
    assert line["log_level"] == "warning"
    assert (line["step"], line["realm"], line["status"]) == ("token", "preprod", 401)
    assert "aira-directory-preprod" not in str(line)
    assert "s3cret" not in str(line)


def test_a_search_keycloak_answers_logs_no_warning(
    preprod: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    preprod.AIRA_DIRECTORY_TOKEN_REALM = "master"
    _keycloak_with_the_account_in(monkeypatch, "master")

    with structlog.testing.capture_logs() as logs:
        _fresh_log(monkeypatch)
        _client(_user("a", "global-admin")).get(f"{URL}?q=aira")

    assert [entry["event"] for entry in logs] == ["directory.search"]


def test_the_token_realm_reaches_the_directory_from_the_environment(tmp_path: Path) -> None:
    """From the variable an operator sets to the directory Management builds — in a process of its
    own, because Django reads its settings once, at start, and this process already started. Run
    where no `.env` is, so that none can answer for the variable."""
    probe = (
        "import django; django.setup()\n"
        "from aira_management.apps.directory.service import build_directory\n"
        "directory = build_directory()\n"
        "print(directory._realm, directory._token_realm)\n"
    )
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "aira_management.config.settings",
        "AIRA_TEST_DATABASE": "1",
        "AIRA_OIDC_ISSUER": f"{KEYCLOAK}/realms/preprod",
        "AIRA_DIRECTORY_CLIENT_ID": "aira-directory-preprod",
        "AIRA_DIRECTORY_CLIENT_SECRET": "s3cret",
        "AIRA_DIRECTORY_TOKEN_REALM": "master",
    }
    result = subprocess.run(  # noqa: S603 - this interpreter, a fixed program
        [sys.executable, "-c", probe],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "preprod master"
