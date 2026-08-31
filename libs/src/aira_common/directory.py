"""Looking up groups and people in Keycloak, to grant them access (`FRD-209` §3).

**Read-only, always.** AIRA never creates a group, never puts anybody in one, never deletes one.
The identity provider is the source of truth about who works where; a console that edited it would
be a second place to change that, and the two would disagree within a week.

Why a lookup exists at all: a grant names a group *path*, and typing one from memory is how a grant
comes to name a group that does not exist — silently, because a path matching nobody simply never
applies. Nothing fails, nobody gets access, and there is nothing on screen to notice.

The client is deliberately small. It answers two questions ("which groups look like this", "which
people look like this"), holds one token, and knows nothing about use cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from aira_common.access import SubjectKind

#: How many of each kind one search returns. A directory search is a picker, not a report: a
#: hundred results is a list nobody reads, and the answer to "too many" is a better search term.
SEARCH_LIMIT = 25

#: Long enough for a slow identity provider, short enough that a console search does not appear to
#: hang. A directory that is down must say so quickly.
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

    Distinct from "nothing matched" on purpose: a console that showed an empty list for both would
    have somebody conclude a group does not exist when in fact nobody could look.
    """


class KeycloakDirectory:
    """Search groups and users in one realm through the Admin API.

    Credentials are a **client-credentials** service account with `view-users` and `query-groups`
    on the realm — the least it can be given. It is never handed a user's token: a directory search
    is the console asking on the reader's behalf, and forwarding their token would make the results
    depend on what that individual happens to be allowed to see in Keycloak, which is a different
    question from "who could be granted access here".
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
        # Injected in tests so the whole class is exercised against a transport rather than a
        # stand-in for itself — a double that is more permissive than the thing it replaces is a
        # trap this project has already fallen into.
        self._http = client or httpx.Client(timeout=TIMEOUT_SECONDS)

    def find_user(self, username: str) -> DirectoryEntry | None:
        """The one person with exactly this username, or ``None``.

        Separate from :meth:`search` because the two answer different questions and only one of
        them may be approximate. A search populates a picker and a substring match is a help; this
        decides whether an **account** is created for a name somebody typed, and a substring match
        there would attach a credential and a membership to the wrong person. Keycloak's
        `exact=true` is what makes it the same question the grant is about.
        """
        wanted = username.strip()
        if not wanted:
            return None
        rows = self._get(self._token(), "/users", {"username": wanted, "exact": "true", "max": 2})
        for row in rows:
            found = row.get("username")
            if isinstance(found, str) and found == wanted:
                parts = (row.get("firstName"), row.get("lastName"))
                name = " ".join(part for part in parts if isinstance(part, str)).strip()
                return DirectoryEntry(
                    kind=SubjectKind.USER,
                    id=found,
                    label=name or found,
                    detail=str(row.get("email") or ""),
                )
        return None

    def search(self, query: str) -> list[DirectoryEntry]:
        """Groups and users matching ``query``, groups first.

        Groups first because granting to one is the point of the feature, and a list that opens
        with twelve people who happen to share a substring buries it.
        """
        token = self._token()
        return [*self._groups(token, query), *self._users(token, query)]

    # ---- the Admin API ---------------------------------------------------------------------

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
            # The reason is not carried outward: it may name the client, and the console shows this
            # to whoever is granting access. The *fact* is what they need.
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
        # Flattened, because Keycloak returns a tree and a grant names a leaf as readily as a
        # parent. Both are grantable and only the caller knows which they mean.
        self._flatten(rows, found)
        return found[:SEARCH_LIMIT]

    def _flatten(self, rows: list[dict[str, Any]], into: list[DirectoryEntry]) -> None:
        for row in rows:
            path = row.get("path")
            if isinstance(path, str) and path:
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
                self._flatten(children, into)

    def _users(self, token: str, query: str) -> list[DirectoryEntry]:
        rows = self._get(token, "/users", {"search": query, "max": SEARCH_LIMIT})
        found: list[DirectoryEntry] = []
        for row in rows:
            username = row.get("username")
            if not isinstance(username, str) or not username:
                continue
            parts = (row.get("firstName"), row.get("lastName"))
            name = " ".join(part for part in parts if isinstance(part, str)).strip()
            found.append(
                DirectoryEntry(
                    kind=SubjectKind.USER,
                    id=username,
                    label=name or username,
                    # The address distinguishes two people with the same name, which is the whole
                    # reason a picker shows a second line. It is not a credential.
                    detail=str(row.get("email") or ""),
                )
            )
        return found
