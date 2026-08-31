"""Every value a directory or a caller can put where an identity goes (`FRD-613`).

**A sweep over the door, not a test per field.** `LESSONS.md` §1 records what found four defects in
one round: *"None of these was found by asking about a field. They were found by sweeping every
endpoint with the same short list of wrong values."* This is that sweep, aimed at the one input
nobody validates because it arrives already verified — the **claims of a token**.

A signed token is trustworthy about *who issued it*. It is not a promise that `preferred_username`
is a string, that it fits a column, that `groups` is a list, or that any of it is what this system
would have chosen. Everything here is a value a realm can legitimately emit, and each one used to
reach a database column, a comparison or a log line unexamined.
"""

from __future__ import annotations

import pytest

from aira_common.access import GrantRole, resolve, strongest, usecases_from_group_paths
from aira_common.roles import (
    Role,
    RoleMappingError,
    is_governance,
    parse_role_groups,
    roles_from_groups,
)
from aira_gateway.auth.attribution import USE_CASE_HEADER, is_valid_use_case, resolve_use_case
from aira_gateway.auth.credentials import extract_token
from aira_gateway.auth.oidc import OidcValidator


class _Request:
    """The three things the credential and selector readers touch."""

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
        scope: dict[str, object] | None = None,
    ) -> None:
        self.headers = headers or {}
        self.query_params = query or {}
        self.scope = scope or {}


# ═══ 1. what the caller presented ═══════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("headers", "query", "expected"),
    [
        ({"authorization": "Bearer abc"}, {}, "abc"),
        ({"authorization": "bearer abc"}, {}, "abc"),
        ({"authorization": "BEARER abc"}, {}, "abc"),
        ({"authorization": "Bearer   abc  "}, {}, "abc"),
        ({"authorization": "Bearer "}, {}, None),
        ({"authorization": "Bearer"}, {}, None),
        ({"authorization": "Basic abc"}, {}, None),
        ({"authorization": ""}, {}, None),
        ({"x-goog-api-key": " k "}, {}, "k"),
        ({"x-goog-api-key": "  "}, {}, None),
        ({}, {"key": "q"}, "q"),
        ({}, {"key": ""}, None),
        ({}, {}, None),
        ({"authorization": "Bearer a", "x-goog-api-key": "b"}, {"key": "c"}, "a"),
        ({"x-goog-api-key": "b"}, {"key": "c"}, "b"),
    ],
    ids=[
        "bearer",
        "lowercase-scheme",
        "uppercase-scheme",
        "padded",
        "bearer-with-nothing",
        "scheme-only",
        "another-scheme",
        "empty-header",
        "goog-header",
        "blank-goog-header",
        "query",
        "blank-query",
        "nothing",
        "authorization-wins",
        "header-beats-query",
    ],
)
def test_the_credential_a_request_presents(
    headers: dict[str, str], query: dict[str, str], expected: str | None
) -> None:
    """Precedence is a security property, not a convenience: a client that sends two must get a
    defined answer, or which credential authenticated depends on the server's mood."""
    assert extract_token(_Request(headers, query)) == expected  # type: ignore[arg-type]


# ═══ 2. what the caller selected ════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("slug", "valid"),
    [
        ("kundenservice", True),
        ("uc-1", True),
        ("a", True),
        ("a" * 64, True),
        ("a" * 65, False),
        ("", False),
        ("Kundenservice", False),
        ("kunden_service", False),
        ("kunden service", False),
        ("../etc/passwd", False),
        ("uc-a\n", False),
        ("uc-a%00", False),
        ("üc-a", False),
    ],
    ids=[
        "ordinary",
        "digits",
        "one-character",
        "at-the-limit",
        "past-the-limit",
        "empty",
        "capitals",
        "underscore",
        "space",
        "traversal",
        "newline",
        "encoded-nul",
        "non-ascii",
    ],
)
def test_a_selector_must_look_like_a_slug(slug: str, valid: bool) -> None:
    """Rejecting anything else keeps unvalidated client input out of the audit log, the read-model
    lookups and the trace attributes (`ADR-0007`) — the length bound is the one that matters most,
    because the column it reaches is 64 characters wide and SQLite would take anything."""
    assert is_valid_use_case(slug) is valid


@pytest.mark.parametrize(
    ("header", "path", "expected"),
    [
        ("uc-a", None, "uc-a"),
        ("  uc-a  ", None, "uc-a"),
        ("   ", "uc-b", "uc-b"),
        ("", "uc-b", "uc-b"),
        (None, "uc-b", "uc-b"),
        ("uc-a", "uc-b", "uc-a"),
        (None, None, None),
        (None, 7, None),
    ],
    ids=[
        "header",
        "padded-header",
        "blank-header-falls-through",
        "empty-header-falls-through",
        "path",
        "header-wins",
        "neither",
        "path-of-the-wrong-type",
    ],
)
def test_where_a_use_case_is_named(header: str | None, path: object, expected: str | None) -> None:
    request = _Request(
        headers={USE_CASE_HEADER: header} if header is not None else {},
        scope={"aira_use_case_path": path} if path is not None else {},
    )
    assert resolve_use_case(request) == expected  # type: ignore[arg-type]


# ═══ 3. what the token claimed ══════════════════════════════════════════════════════════════════


class _Verifier:
    """Verifies nothing and returns the claims it was given — the *claims* are what is under test
    here, and a real signature would only make the fixtures longer."""

    def __init__(self, claims: dict[str, object] | None) -> None:
        self._claims = claims

    def verify(self, token: str) -> dict[str, object] | None:  # noqa: ARG002
        return self._claims


def _principal(claims: dict[str, object] | None, **kwargs: object):
    validator = OidcValidator("iss", "aud", jwks=None, **kwargs)  # type: ignore[arg-type]
    validator._verifiers = (("iss", _Verifier(claims)),)  # type: ignore[attr-defined]
    return validator.validate("t")


def test_a_token_with_no_subject_authenticates_nobody() -> None:
    """`sub` is what every audit row, membership decision and budget booking is attributed to.
    Absence of information is not permission."""
    assert _principal({"preferred_username": "ada", "groups": ["/aira/global-admins"]}) is None


@pytest.mark.parametrize("subject", ["", None, 0], ids=["empty", "absent", "falsy"])
def test_a_falsy_subject_is_no_subject(subject: object) -> None:
    assert _principal({"sub": subject}) is None


def test_a_numeric_subject_is_carried_as_a_string() -> None:
    """A realm may mint one. The column is a string and every comparison is a string comparison, so
    the coercion happens once, here, rather than at the four readers."""
    principal = _principal({"sub": 12345})
    assert principal is not None
    assert principal.subject == "12345"


@pytest.mark.parametrize(
    ("claimed", "expected"),
    [
        ("ada", "ada"),
        ("  ", None),
        ("", None),
        (None, None),
        (12345, None),
        (["ada"], None),
        ("a" * 300, "a" * 150),
    ],
    ids=["name", "blank", "empty", "absent", "number", "list", "too-long"],
)
def test_the_name_a_token_carries(claimed: object, expected: str | None) -> None:
    """Bounded and typed at the door. The name reaches a stored column, a budget key and a
    membership comparison — three places where a 300-character value is a `DataError` on Postgres
    and silently fine on SQLite."""
    principal = _principal({"sub": "s", "preferred_username": claimed})
    assert principal is not None
    assert principal.username == expected


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"azp": "console"}, "console"),
        ({"client_id": "batch"}, "batch"),
        ({"azp": "console", "client_id": "batch"}, "console"),
        ({}, None),
        ({"azp": ""}, None),
        ({"azp": "c" * 100}, "c" * 64),
    ],
    ids=["azp", "client-id", "azp-wins", "neither", "empty", "too-long"],
)
def test_which_system_the_token_was_issued_to(
    claims: dict[str, object], expected: str | None
) -> None:
    principal = _principal({"sub": "s", **claims})
    assert principal is not None
    assert principal.credential == expected


@pytest.mark.parametrize(
    ("groups", "expected"),
    [
        (["/use-cases/uc-a"], ("uc-a",)),
        (["/use-cases/uc-a/"], ("uc-a",)),
        (["/use-cases/dept/uc-a"], ("uc-a",)),
        (["/use-cases/uc-a", "/use-cases/uc-a"], ("uc-a",)),
        (["/use-cases/"], ()),
        (["/use-cases"], ()),
        (["/other/uc-a"], ()),
        ([], ()),
        ("not-a-list", ()),
        ([None, 7, "/use-cases/uc-a"], ("uc-a",)),
    ],
    ids=[
        "convention",
        "trailing-slash",
        "nested",
        "repeated",
        "prefix-only",
        "prefix-without-slash",
        "another-tree",
        "none",
        "not-a-list",
        "mixed-types",
    ],
)
def test_which_use_cases_a_group_claim_names(groups: object, expected: tuple[str, ...]) -> None:
    """A malformed claim confers nothing rather than raising: a realm misconfiguration must not
    stop authentication, it must stop *authority*."""
    principal = _principal({"sub": "s", "groups": groups})
    assert principal is not None
    assert principal.use_cases == expected


def test_only_string_group_paths_survive_onto_the_principal() -> None:
    principal = _principal({"sub": "s", "groups": ["/a", 7, None, "/b"]})
    assert principal is not None
    assert principal.groups == ("/a", "/b")


# ═══ 4. what a group confers ════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("held", "expected"),
    [
        (["/aira/global-admins"], ("global-admin",)),
        (["/aira/global-admins-readonly"], ()),
        (["/aira/global-admins/deputies"], ()),
        (["/AIRA/GLOBAL-ADMINS"], ()),
        (["/aira/it-security", "/aira/global-admins"], ("global-admin", "it-security")),
        ([], ()),
    ],
    ids=[
        "exact",
        "longer-name",
        "child-group",
        "different-case",
        "two-roles",
        "none",
    ],
)
def test_a_role_comes_from_an_exact_group_path(held: list[str], expected: tuple[str, ...]) -> None:
    """Exact match, never a prefix. `/aira/global-admins-readonly` starting with
    `/aira/global-admins` must confer nothing, and a sub-group is a different group."""
    mapping = parse_role_groups("global-admin=/aira/global-admins;it-security=/aira/it-security")
    assert set(roles_from_groups(held, mapping)) == set(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "use-case-admin=/x",
        "use-case-user=/x",
        "not-a-role=/x",
        "global-admin",
        "global-admin=relative/path",
        "global-admin=/",
        "global-admin=",
    ],
    ids=[
        "abolished-admin",
        "abolished-user",
        "unknown-role",
        "no-equals",
        "relative-path",
        "realm-root",
        "names-no-group",
    ],
)
def test_a_role_mapping_refuses_rather_than_ignores(raw: str) -> None:
    """A typo here grants nothing and would do so **silently** — the failure this project has
    recorded four times."""
    with pytest.raises(RoleMappingError):
        parse_role_groups(raw)


def test_two_entries_for_one_role_are_merged() -> None:
    """An installation that lists a group twice meant both."""
    mapping = parse_role_groups("global-admin=/a;global-admin=/b")
    assert mapping[Role.GLOBAL_ADMIN] == ("/a", "/b")


def test_an_absent_mapping_grants_no_role() -> None:
    assert roles_from_groups(["/aira/global-admins"], {}) == ()


def test_a_realm_role_claim_confers_nothing() -> None:
    """`ADR-0017`: assigning a realm role directly grants nothing, and that inertness is the
    guarantee rather than an oversight."""
    principal = _principal({"sub": "s", "realm_access": {"roles": ["global-admin"]}})
    assert principal is not None
    assert principal.roles == ()
    assert not is_governance(principal.roles)


# ═══ 5. what a grant resolves to ════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        ([], "user"),
        (["user"], "user"),
        (["admin"], "admin"),
        (["user", "admin"], "admin"),
        (["admin", "user"], "admin"),
        (["owner"], "user"),
        (["owner", "admin"], "admin"),
        ([""], "user"),
        ([None], "user"),
    ],
    ids=[
        "nothing",
        "user",
        "admin",
        "weak-then-strong",
        "strong-then-weak",
        "unknown-alone",
        "unknown-beside-known",
        "empty",
        "none",
    ],
)
def test_the_strongest_role_wins_and_an_unknown_one_is_the_weakest(
    roles: list[object], expected: str
) -> None:
    """A role this version has never heard of must not be assumed to be powerful — *absence of
    information is not permission* — and an access decision must not depend on which row was read
    first."""
    assert strongest(roles) == expected  # type: ignore[arg-type]


def test_the_union_of_every_route_in() -> None:
    """Convention, group grant and personal grant, with the strongest winning per use case."""
    answer = resolve(
        ["/use-cases/uc-a", "/ai/kundenservice"],
        [("/ai/kundenservice", "uc-b", "admin"), ("/nobody", "uc-c", "admin")],
        [("uc-a", "admin"), ("uc-d", "user")],
    )
    assert answer == {"uc-a": "admin", "uc-b": "admin", "uc-d": "user"}


def test_the_convention_grants_only_ordinary_membership() -> None:
    """A group path cannot express a role, which is exactly why explicit grants exist."""
    assert resolve(["/use-cases/uc-a"], []) == {"uc-a": str(GrantRole.USER)}


def test_a_grant_naming_a_group_nobody_is_in_reaches_nobody() -> None:
    assert resolve([], [("/ai/kundenservice", "uc-b", "admin")]) == {}


def test_the_prefix_is_read_from_one_definition() -> None:
    """The rule was written out a second time in `auth/attribution.py`, character for character.
    Nothing had drifted, which was the only reason it was still true."""
    assert usecases_from_group_paths(["/use-cases/uc-a"]) == ("uc-a",)
