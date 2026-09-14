"""Searching for whoever should get access (`FRD-209` §3).

One endpoint returning **groups and users together**, because the question is "who should get
this"; the kind is in the answer. It **never writes** to the identity provider. Without an admin
client it answers from what Management already knows — people who have signed in, group paths
already granted — and the response's `source` says which answer the reader got.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from aira_common.access import SubjectKind
from aira_common.directory import SEARCH_LIMIT, DirectoryEntry, DirectoryUnavailable
from aira_common.logging import get_logger
from aira_management.apps.directory.service import build_directory as _build_directory
from aira_management.apps.usecases.models import UseCaseGroupGrant
from aira_management.rbac import MaySearchDirectory

_log = get_logger("aira_management.directory")

#: The shortest query answered; a picker that dumps the directory on focus is one nobody reads.
MIN_QUERY_LENGTH = 2


def _known_locally(query: str) -> list[DirectoryEntry]:
    """What Management can answer on its own: it can re-offer what exists, not invent groups."""
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

    permission_classes = [IsAuthenticated, MaySearchDirectory]

    def get(self, request: Request) -> Response:
        query = str(request.query_params.get("q", "")).strip()
        if len(query) < MIN_QUERY_LENGTH:
            return Response({"results": [], "source": "none", "hint": "Type at least two letters."})

        # A search reads other people's names and addresses, and the people who grant access must
        # be able to run it — so every search is logged instead.
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
                # Fall back to the local answer — a real subset — rather than fail.
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
