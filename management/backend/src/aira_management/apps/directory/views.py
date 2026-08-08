"""Searching for whoever should get access (`FRD-209` §3).

One endpoint returning **groups and users together**, because the question a person is asking is
"who should get this", not "am I about to name a group or a person". The kind is in the answer.

Two properties this is careful about:

**It never writes.** AIRA does not create groups, does not put people in them, does not delete
them. The identity provider is the source of truth about who works where.

**It says where the answer came from.** Without an admin client configured the search still works,
against what Management already knows — everybody who has signed in, and every group path already
granted somewhere. That is enough to run the demo and to re-grant an existing group, and it cannot
invent a group nobody has ever used. "No results" from a degraded directory and "no such group" are
different answers, so the response carries `source` and the console shows which it got.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from aira_common.access import SubjectKind
from aira_common.directory import (
    SEARCH_LIMIT,
    DirectoryEntry,
    DirectoryUnavailable,
    KeycloakDirectory,
)
from aira_common.logging import get_logger
from aira_management.apps.usecases.models import UseCaseGroupGrant
from aira_management.rbac import IsUseCaseAdmin

_log = get_logger("aira_management.directory")


def _build_directory() -> KeycloakDirectory | None:
    """The configured Keycloak directory, or ``None`` when there is no admin client."""
    base = getattr(settings, "AIRA_OIDC_ISSUER_BASE", "") or ""
    realm = getattr(settings, "AIRA_OIDC_REALM", "") or ""
    client_id = getattr(settings, "AIRA_DIRECTORY_CLIENT_ID", "") or ""
    secret = getattr(settings, "AIRA_DIRECTORY_CLIENT_SECRET", "") or ""
    if not (base and realm and client_id and secret):
        return None
    return KeycloakDirectory(base, realm, client_id, secret)


def _known_locally(query: str) -> list[DirectoryEntry]:
    """What Management can answer on its own.

    Deliberately limited to things somebody has already used: people who have signed in, and group
    paths already granted on some use case. It can re-offer what exists and cannot invent what does
    not — which is the honest shape of a degraded directory.
    """
    needle = query.strip()
    entries: list[DirectoryEntry] = []

    paths = (
        UseCaseGroupGrant.objects.filter(group_path__icontains=needle)
        .values_list("group_path", flat=True)
        .distinct()[:SEARCH_LIMIT]
    )
    for path in paths:
        entries.append(
            DirectoryEntry(
                kind=SubjectKind.GROUP,
                id=path,
                label=path.rsplit("/", 1)[-1] or path,
                detail=path.rsplit("/", 1)[0] or "/",
            )
        )

    users = get_user_model().objects.filter(
        Q(username__icontains=needle)
        | Q(first_name__icontains=needle)
        | Q(last_name__icontains=needle)
        | Q(email__icontains=needle)
    )[:SEARCH_LIMIT]
    for user in users:
        full = f"{user.first_name} {user.last_name}".strip()
        entries.append(
            DirectoryEntry(
                kind=SubjectKind.USER,
                id=user.get_username(),
                label=full or user.get_username(),
                detail=user.email or "",
            )
        )
    return entries


class DirectorySearchView(APIView):
    """``GET /api/v1/directory/?q=`` — groups and users a grant could name."""

    permission_classes = [IsAuthenticated, IsUseCaseAdmin]

    def get(self, request: Request) -> Response:
        query = str(request.query_params.get("q", "")).strip()
        # An empty query is answered with an empty list rather than with everybody: a picker that
        # dumps the whole directory the moment it is focused is a picker nobody reads, and on a
        # real realm it is thousands of rows.
        if len(query) < 2:
            return Response({"results": [], "source": "none", "hint": "Type at least two letters."})

        # A directory search reads other people's names and email addresses. Narrowing *who* may
        # do it is not available — granting access is a use-case admin's job and they have to be
        # able to find the person — so the answer is the one this project keeps arriving at: an
        # action nobody can see is not governed. Walking the alphabet is still possible and is now
        # a hundred log lines with one username on them.
        _log.info(
            "directory.search",
            actor=request.user.get_username(),
            query=query,
        )

        directory = _build_directory()
        if directory is not None:
            try:
                entries = directory.search(query)
                return Response({"results": [_as_dict(e) for e in entries], "source": "keycloak"})
            except DirectoryUnavailable:
                # Falls back rather than failing: a console that cannot search is a console where
                # nobody can grant access, and the local answer is a real subset rather than a
                # guess. The reader is told which one they are looking at.
                pass

        return Response(
            {
                "results": [_as_dict(e) for e in _known_locally(query)],
                "source": "local",
            }
        )


def _as_dict(entry: DirectoryEntry) -> dict[str, Any]:
    return {
        "kind": str(entry.kind),
        "id": entry.id,
        "label": entry.label,
        "detail": entry.detail,
    }
