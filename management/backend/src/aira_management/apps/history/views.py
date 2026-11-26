"""``GET /api/v1/configuration-changes/`` — the history, for the roles that may see people."""

from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from aira_common.permissions import Permission
from aira_management.apps.history.models import ConfigurationChange
from aira_management.pagination import ConsolePagination
from aira_management.rbac import requires

#: The filters a reader may narrow the history by, each a column compared for equality.
FILTERS = ("kind", "use_case", "actor", "person")


class ConfigurationChangesView(APIView):
    """Newest first, paged, filtered by kind, use case, actor or person.

    **Only for whoever may see people** (`incident.investigate`: Global Administrator and IT
    Security under strict data mode), because every row names the person who changed something
    and many name the person it was about — the same rule as every other record of a person.
    """

    permission_classes = [IsAuthenticated, requires(Permission.INCIDENT_INVESTIGATE)]

    def get(self, request: Request) -> Response:
        rows = ConfigurationChange.objects.all()
        for name in FILTERS:
            value = str(request.query_params.get(name, "")).strip()
            if value:
                rows = rows.filter(**{name: value})
        paginator = ConsolePagination()
        page = paginator.paginate_queryset(rows, request, view=self) or []
        return paginator.get_paginated_response(
            [
                {
                    "id": row.pk,
                    "changed_at": row.changed_at.isoformat(),
                    "actor": row.actor,
                    "process": row.process,
                    "event_type": row.event_type,
                    "kind": row.kind,
                    "action": row.action,
                    "object_key": row.object_key,
                    "use_case": row.use_case,
                    "person": row.person,
                    "before": row.before,
                    "after": row.after,
                    "fields_changed": row.fields_changed,
                    "trace_id": row.trace_id,
                }
                for row in page
            ]
        )
