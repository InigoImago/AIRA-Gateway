"""The configured directory, and the one question the rest of Management asks it.

`_build_directory` lived inside the search view, where it was the only caller. It has a second one
now — granting access to somebody who has **not signed in yet** (`FRD-209` FR-4) — and a second
copy of "which four settings make a directory client" is the drift this project keeps paying for.
So it lives here, and the view imports it.

The second caller is the reason `known_person` exists rather than the view calling `find_user`
itself: whether an account may be created for a typed name is a decision, not a lookup, and it has
exactly three outcomes a caller has to tell apart — **there is such a person**, **there is not**,
and **nobody could be asked**. A function returning `DirectoryEntry | None` collapses the last two,
and collapsing them is how "no such colleague" comes to be reported for a directory that is down.
"""

from __future__ import annotations

from django.conf import settings

from aira_common.directory import DirectoryEntry, DirectoryUnavailable, KeycloakDirectory


def build_directory() -> KeycloakDirectory | None:
    """The configured Keycloak directory, or ``None`` when there is no admin client."""
    base = getattr(settings, "AIRA_OIDC_ISSUER_BASE", "") or ""
    realm = getattr(settings, "AIRA_OIDC_REALM", "") or ""
    client_id = getattr(settings, "AIRA_DIRECTORY_CLIENT_ID", "") or ""
    secret = getattr(settings, "AIRA_DIRECTORY_CLIENT_SECRET", "") or ""
    if not (base and realm and client_id and secret):
        return None
    return KeycloakDirectory(base, realm, client_id, secret)


def known_person(username: str) -> DirectoryEntry | None:
    """The directory's record of ``username``, or ``None`` if it has none.

    Raises :class:`DirectoryUnavailable` when there is no directory to ask, or when asking failed.
    That is the distinction the caller owes its reader: "the directory says there is no such
    person" is a fact about the name, and "nobody could be asked" is a fact about this
    installation, and only one of the two is the typist's to fix.
    """
    directory = build_directory()
    if directory is None:
        raise DirectoryUnavailable("no directory client is configured")
    return directory.find_user(username)
