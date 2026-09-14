"""The configured directory, and the one question the rest of Management asks it.

Shared by the directory search and by granting access to somebody who has not signed in yet
(`FRD-209` FR-4), so "which settings make a directory client" is written once.
"""

from __future__ import annotations

from django.conf import settings

from aira_common.directory import DirectoryEntry, DirectoryUnavailable, KeycloakDirectory


def build_directory() -> KeycloakDirectory | None:
    """The configured Keycloak directory, or ``None`` when there is no admin client."""
    # Its own address where one is set: the issuer names the server as browsers reach it.
    base = (
        getattr(settings, "AIRA_DIRECTORY_URL", "")
        or getattr(settings, "AIRA_OIDC_ISSUER_BASE", "")
        or ""
    )
    realm = getattr(settings, "AIRA_OIDC_REALM", "") or ""
    client_id = getattr(settings, "AIRA_DIRECTORY_CLIENT_ID", "") or ""
    secret = getattr(settings, "AIRA_DIRECTORY_CLIENT_SECRET", "") or ""
    if not (base and realm and client_id and secret):
        return None
    return KeycloakDirectory(base, realm, client_id, secret)


def known_person(username: str) -> DirectoryEntry | None:
    """The directory's record of ``username``, or ``None`` if it has none.

    Raises :class:`DirectoryUnavailable` when there is no directory to ask or asking failed: "no
    such person" is a fact about the name, "nobody could be asked" a fact about this installation,
    and only the first is the typist's to fix.
    """
    directory = build_directory()
    if directory is None:
        raise DirectoryUnavailable("no directory client is configured")
    return directory.find_user(username)
