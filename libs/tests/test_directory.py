"""Searching Keycloak for groups and people (`FRD-209` §3).

Driven through a `MockTransport` rather than against a stand-in for the class: a double that is
more permissive than the thing it replaces is a trap this project has already fallen into, and the
whole value of this client is what it does with *Keycloak's* shapes — a group tree, a user list,
and a token endpoint that can fail in several ways.
"""

from __future__ import annotations

import httpx
import pytest

from aira_common.access import SubjectKind
from aira_common.directory import (
    SEARCH_LIMIT,
    DirectoryUnavailable,
    KeycloakDirectory,
)

TOKEN_PATH = "/realms/aira/protocol/openid-connect/token"
GROUPS_PATH = "/admin/realms/aira/groups"
USERS_PATH = "/admin/realms/aira/users"


def _directory(handler) -> KeycloakDirectory:
    transport = httpx.MockTransport(handler)
    return KeycloakDirectory(
        "https://keycloak.example",
        "aira",
        "aira-directory",
        "s3cret",
        client=httpx.Client(transport=transport),
    )


def _ok(groups: list[dict] | None = None, users: list[dict] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, json={"access_token": "admin-token"})
        if request.url.path == GROUPS_PATH:
            return httpx.Response(200, json=groups or [])
        if request.url.path == USERS_PATH:
            return httpx.Response(200, json=users or [])
        return httpx.Response(404)

    return handler


# ---- groups ------------------------------------------------------------------------------


def test_a_group_is_returned_by_its_path_because_that_is_what_a_grant_stores() -> None:
    entries = _directory(
        _ok(groups=[{"name": "kundenservice", "path": "/ai/kundenservice"}])
    ).search("kunden")

    assert entries[0].kind is SubjectKind.GROUP
    assert entries[0].id == "/ai/kundenservice"
    assert entries[0].label == "kundenservice"
    # Where it sits, so two groups of the same name in different branches are distinguishable.
    assert entries[0].detail == "/ai"


def test_a_nested_group_is_offered_as_well_as_its_parent() -> None:
    """Keycloak returns a tree; a grant names a leaf as readily as a parent, and only the caller
    knows which they mean."""
    entries = _directory(
        _ok(
            groups=[
                {
                    "name": "ai",
                    "path": "/ai",
                    "subGroups": [{"name": "kundenservice", "path": "/ai/kundenservice"}],
                }
            ]
        )
    ).search("a")

    assert [entry.id for entry in entries] == ["/ai", "/ai/kundenservice"]


def test_a_top_level_group_reports_the_root_as_its_parent() -> None:
    entries = _directory(_ok(groups=[{"name": "ai", "path": "/ai"}])).search("ai")
    assert entries[0].detail == "/"


def test_a_group_row_with_no_path_is_skipped_rather_than_offered_as_a_broken_grant() -> None:
    entries = _directory(_ok(groups=[{"name": "odd"}, {"name": "ok", "path": "/ok"}])).search("o")
    assert [entry.id for entry in entries] == ["/ok"]


def test_the_result_is_bounded() -> None:
    # A directory search is a picker, not a report: the answer to "too many" is a better term.
    many = [{"name": f"g{i}", "path": f"/g{i}"} for i in range(SEARCH_LIMIT * 3)]
    entries = _directory(_ok(groups=many)).search("g")
    assert len([e for e in entries if e.kind is SubjectKind.GROUP]) == SEARCH_LIMIT


# ---- users -------------------------------------------------------------------------------


def test_a_person_is_returned_by_username_because_that_is_what_a_grant_stores() -> None:
    entries = _directory(
        _ok(users=[{"username": "ada", "firstName": "Ada", "lastName": "Lovelace"}])
    ).search("ada")

    assert entries[0].kind is SubjectKind.USER
    assert entries[0].id == "ada"
    assert entries[0].label == "Ada Lovelace"


def test_a_person_with_no_name_is_shown_by_username_rather_than_blank() -> None:
    entries = _directory(_ok(users=[{"username": "ada"}])).search("ada")
    assert entries[0].label == "ada"


def test_the_address_distinguishes_two_people_of_the_same_name() -> None:
    entries = _directory(
        _ok(users=[{"username": "ada", "firstName": "Ada", "email": "ada@example.org"}])
    ).search("ada")
    assert entries[0].detail == "ada@example.org"


def test_a_user_row_with_no_username_is_skipped() -> None:
    entries = _directory(_ok(users=[{"firstName": "Nameless"}, {"username": "ok"}])).search("n")
    assert [entry.id for entry in entries] == ["ok"]


def test_groups_come_first_because_granting_to_one_is_the_point() -> None:
    entries = _directory(
        _ok(groups=[{"name": "ada-team", "path": "/ada-team"}], users=[{"username": "ada"}])
    ).search("ada")

    assert [entry.kind for entry in entries] == [SubjectKind.GROUP, SubjectKind.USER]


# ---- what it sends -----------------------------------------------------------------------


def test_it_authenticates_as_a_service_account_not_as_the_reader() -> None:
    """A directory search asks on the reader's behalf; forwarding *their* token would make the
    results depend on what that individual can see in Keycloak, which is a different question."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            seen["grant"] = request.content.decode()
            return httpx.Response(200, json={"access_token": "admin-token"})
        seen[request.url.path] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    _directory(handler).search("kunden")

    assert "grant_type=client_credentials" in str(seen["grant"])
    assert seen[GROUPS_PATH] == "Bearer admin-token"


def test_it_never_writes() -> None:
    """AIRA does not create groups, does not add people to them, does not delete them."""
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(200, json=[])

    _directory(handler).search("anything")

    assert set(methods) <= {"POST", "GET"}
    # The one POST is the token exchange, not a write to the directory.
    assert methods.count("POST") == 1


# ---- when it cannot answer ---------------------------------------------------------------


def test_an_unreachable_provider_is_distinct_from_nothing_matching() -> None:
    """A console that showed an empty list for both would have somebody conclude a group does not
    exist when in fact nobody could look."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(DirectoryUnavailable):
        _directory(handler).search("kunden")


def test_a_refused_token_is_unavailable_rather_than_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    with pytest.raises(DirectoryUnavailable):
        _directory(handler).search("kunden")


def test_a_token_response_with_no_token_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not_a_token": True})

    with pytest.raises(DirectoryUnavailable):
        _directory(handler).search("kunden")


def test_the_failure_never_names_the_client() -> None:
    """The console shows this to whoever is granting access. The *fact* is what they need; the
    client id is not theirs to see."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="client aira-directory is misconfigured")

    with pytest.raises(DirectoryUnavailable) as caught:
        _directory(handler).search("kunden")

    assert "aira-directory" not in str(caught.value)
    assert "s3cret" not in str(caught.value)


def test_a_search_that_answers_with_the_wrong_shape_is_empty_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(200, json={"unexpected": "object"})

    assert _directory(handler).search("kunden") == []
