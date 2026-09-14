"""Looking up groups and people in Keycloak, to grant them access (`FRD-209` §3).

**Read-only, always**: the identity provider is the source of truth about who works where, and AIRA
never creates, fills or deletes a group. The lookup exists because a grant names a group *path*,
and a path typed from memory that matches nobody silently never applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from aira_common.access import SubjectKind

#: How many of each kind one search returns. A search is a picker, not a report: the answer to
#: "too many" is a better search term.
SEARCH_LIMIT = 25

#: The shortest literal a search sends. Keycloak's search is a pattern match, so what is left after
#: its wildcards are removed has to be a search on its own.
MIN_LITERAL = 2

#: Long enough for a slow identity provider, short enough that a directory that is down says so
#: quickly.
TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """One thing a grant can name."""

    kind: SubjectKind
    #: What the grant stores: a group path, or a username.
    id: str
    #: What a person reads.
    label: str
    #: Where it sits, or how to tell two of the same name apart.
    detail: str = ""


class DirectoryUnavailable(RuntimeError):
    """The identity provider could not be asked.

    Distinct from "nothing matched": an empty list for both would have somebody conclude a group
    does not exist when nobody could look.
    """


def _user_entry(row: dict[str, Any], username: str) -> DirectoryEntry:
    """A user row as a grantable entry. The address tells two people of one name apart."""
    parts = (row.get("firstName"), row.get("lastName"))
    name = " ".join(part for part in parts if isinstance(part, str)).strip()
    return DirectoryEntry(
        kind=SubjectKind.USER,
        id=username,
        label=name or username,
        detail=str(row.get("email") or ""),
    )


class KeycloakDirectory:
    """Search groups and users in one realm through the Admin API.

    Authenticates as a **client-credentials** service account with `view-users` and `query-groups`,
    never with the reader's token: results would then depend on what that individual may see in
    Keycloak, not on who could be granted access here.
    """

    def __init__(
        self,
        base_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        # Injected in tests, so the class is exercised against a transport rather than a stand-in.
        self._http = client or httpx.Client(timeout=TIMEOUT_SECONDS)

    def find_user(self, username: str) -> DirectoryEntry | None:
        """The one person with exactly this username, or ``None``.

        Unlike :meth:`search`, never approximate: this decides whether an account is created for a
        typed name, and a substring match would attach it to the wrong person (`exact=true`).
        """
        wanted = username.strip()
        if not wanted:
            return None
        rows = self._get(self._token(), "/users", {"username": wanted, "exact": "true", "max": 2})
        for row in rows:
            found = row.get("username")
            if isinstance(found, str) and found == wanted:
                return _user_entry(row, found)
        return None

    def group_exists(self, path: str) -> bool:
        """Whether ``path`` is a group in the realm — exactly this path, as a token carries it.

        Asked by path rather than by search: a search matches names and substrings, and binding a
        role needs the one group whose full path this is (`FRD-614` FR-4). A path that is not
        absolute names no group Keycloak can emit, so it is answered without asking.
        """
        wanted = path.strip()
        if not wanted.startswith("/") or wanted == "/":
            return False
        token = self._token()
        try:
            response = self._http.get(
                f"{self._base}/admin/realms/{self._realm}/group-by-path{quote(wanted, safe='/')}",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise DirectoryUnavailable("the identity provider could not be reached") from exc
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            # A refusal to *look* (403 without `query-groups`) is not a "no": nobody could check.
            raise DirectoryUnavailable("the identity provider could not be asked")
        try:
            body = response.json()
        except ValueError as exc:
            raise DirectoryUnavailable("the identity provider answered unreadably") from exc
        return isinstance(body, dict) and body.get("path") == wanted

    def search(self, query: str) -> list[DirectoryEntry]:
        """Groups and users matching ``query``, groups first — granting to a group is the point.

        The query is taken literally. Keycloak matches it as a pattern, so a `%` or `*` a caller
        types would list the whole directory; both are removed, and what is left must still be
        :data:`MIN_LITERAL` characters.
        """
        needle = query.replace("%", "").replace("*", "").strip()
        if len(needle) < MIN_LITERAL:
            return []
        token = self._token()
        return [*self._groups(token, needle), *self._users(token, needle)]

    def _token(self) -> str:
        try:
            response = self._http.post(
                f"{self._base}/realms/{self._realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            response.raise_for_status()
            token = response.json().get("access_token")
        except (httpx.HTTPError, ValueError) as exc:
            # The reason is not carried outward: it may name the client, and the console shows
            # this to whoever is granting access.
            raise DirectoryUnavailable("the identity provider could not be reached") from exc
        if not isinstance(token, str) or not token:
            raise DirectoryUnavailable("the identity provider returned no token")
        return token

    def _get(self, token: str, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            response = self._http.get(
                f"{self._base}/admin/realms/{self._realm}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DirectoryUnavailable("the identity provider could not be reached") from exc
        return body if isinstance(body, list) else []

    def _groups(self, token: str, query: str) -> list[DirectoryEntry]:
        rows = self._get(
            token,
            "/groups",
            {"search": query, "max": SEARCH_LIMIT, "briefRepresentation": "true"},
        )
        found: list[DirectoryEntry] = []
        # Flattened: Keycloak returns a tree, and a leaf is as grantable as its parent.
        self._flatten(rows, found, query.lower())
        return found[:SEARCH_LIMIT]

    def _flatten(self, rows: list[dict[str, Any]], into: list[DirectoryEntry], needle: str) -> None:
        for row in rows:
            path = row.get("path")
            # Only a group that matches itself is offered. Keycloak returns the parents of a
            # match as the tree around it, and offering `/abteilungen` for "kundendienst" puts the
            # wrong group first in the list a grant is picked from.
            if isinstance(path, str) and path and needle in path.lower():
                parent = path.rsplit("/", 1)[0] or "/"
                into.append(
                    DirectoryEntry(
                        kind=SubjectKind.GROUP,
                        id=path,
                        label=str(row.get("name") or path),
                        detail=parent,
                    )
                )
            children = row.get("subGroups")
            if isinstance(children, list):
                self._flatten(children, into, needle)

    def _users(self, token: str, query: str) -> list[DirectoryEntry]:
        rows = self._get(token, "/users", {"search": query, "max": SEARCH_LIMIT})
        return [
            _user_entry(row, username)
            for row in rows
            if isinstance(username := row.get("username"), str) and username
        ]
