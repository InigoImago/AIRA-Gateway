"""Searching for whoever should get access (`FRD-209` §3).

One endpoint returning **groups and users together**, because the question is "who should get
this"; the kind is in the answer. It **never writes** to the identity provider. Without an admin
client it answers from what Management already knows — people who have signed in, group paths
already granted — and the response's `source` says which answer the reader got.

**The term is somebody's name, so it is written down nowhere** (`FRD-631` FR-12). It travels in the
request's body — in a URL every proxy and access log on the way keeps it — and the line each search
writes says who searched, how long the term was and how much was found, never what was searched
for: the person looked up knows nothing of it, and a log store has no role check.
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
    """``POST /api/v1/directory/search`` with ``{"query": …}`` — groups and users a grant could
    name."""

    permission_classes = [IsAuthenticated, MaySearchDirectory]

    def post(self, request: Request) -> Response:
        data = request.data if isinstance(request.data, dict) else {}
        query = str(data.get("query", "")).strip()
        if len(query) < MIN_QUERY_LENGTH:
            return Response({"results": [], "source": "none", "hint": "Type at least two letters."})

        source = "local"
        entries: list[DirectoryEntry] | None = None
        directory = _build_directory()
        if directory is not None:
            try:
                entries = directory.search(query)
                source = "keycloak"
            except DirectoryUnavailable as exc:
                # Fall back to the local answer — a real subset — rather than fail. Said where an
                # operator looks: in the console a directory that cannot be asked is only a
                # shorter list. Where it failed and with what status, never the account.
                _log.warning(
                    "directory.unavailable",
                    step=exc.step,
                    realm=exc.realm,
                    status=exc.status,
                    why=str(exc),
                )
        if entries is None:
            entries = _known_locally(query)

        # A search reads other people's names and addresses, and the people who grant access must
        # be able to run it — so every search is a line: who, how long a term, how much was found.
        # Never the term, which is the name of somebody who does not know they were looked up.
        _log.info(
            "directory.search",
            category="data_access",
            actor=request.user.get_username(),
            query_length=len(query),
            results=len(entries),
            source=source,
        )
        return Response({"results": [_as_dict(entry) for entry in entries], "source": source})


def _as_dict(entry: DirectoryEntry) -> dict[str, Any]:
    return {
        "kind": str(entry.kind),
        "id": entry.id,
        "label": entry.label,
        "detail": entry.detail,
    }
