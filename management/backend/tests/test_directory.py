"""The directory search endpoint (`FRD-209` §3).

What the *endpoint* owns, as distinct from the Keycloak client it may or may not have: who may
ask, what an empty query does, and — the property this feature is careful about — that a degraded
answer says it is degraded rather than looking like "nothing matched".
"""

from __future__ import annotations

from typing import Any

import pytest
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


def test_a_use_case_admin_may() -> None:
    assert _client(_user("a", "global-admin")).get(f"{URL}?q=ada").status_code == 200


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
