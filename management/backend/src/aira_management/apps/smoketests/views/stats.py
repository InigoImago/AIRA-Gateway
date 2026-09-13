"""How each use case stands against the catalogue (`FRD-504`, `ADR-0020`)."""

from __future__ import annotations

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.smoketests.models import TestCase, TestRun
from aira_management.apps.smoketests.serializers import verdict_counts
from aira_management.apps.usecases.access import may_run_tests_queryset
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import MayRunTests


class TestStatsViewSet(viewsets.ViewSet):
    """**The latest run per use case**, deliberately not a total across runs.

    A standardised catalogue compares things against the same questions, so the standing is the
    most recent run: a sum would let an old, worse result drag a corrected one down forever. Each
    row names the model that run entered at, because two runs whose start model changed in between
    are not comparable. Only use cases the caller may run are listed.
    """

    permission_classes = [IsAuthenticated, MayRunTests]

    def list(self, request: Request) -> Response:
        visible = set(
            may_run_tests_queryset(request.user, UseCase.objects.all()).values_list(
                "slug", flat=True
            )
        )
        latest: dict[str, TestRun] = {}
        for run in TestRun.objects.order_by("use_case", "-started_at"):
            if run.use_case in visible:
                latest.setdefault(run.use_case, run)

        rows = []
        asked = TestCase.objects.filter(retired=False).count()
        for use_case, run in sorted(latest.items()):
            results = list(run.results.all())
            rows.append(
                {
                    "use_case": use_case,
                    "model": run.model,
                    "run": run.id,
                    "started_at": run.started_at.isoformat(),
                    "requested_by": getattr(run.requested_by, "username", "") or "",
                    # The catalogue's size *today*: a run made before questions were added answered
                    # fewer, and "40 of 100" must not read as "40".
                    "catalogue": asked,
                    **verdict_counts(results),
                    "errored": sum(1 for result in results if result.error),
                }
            )
        return Response(rows)
