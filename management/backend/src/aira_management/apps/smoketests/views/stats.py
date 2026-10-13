"""How each use case stands against the catalogue (`FRD-504`, `ADR-0020`)."""

from __future__ import annotations

from django.db.models import OuterRef, Subquery
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
    """**The latest finished run per use case**, deliberately not a total across runs.

    A standardised catalogue compares things against the same questions, so the standing is the
    most recent run: a sum would let an old, worse result drag a corrected one down forever. Each
    row names the model that run entered at, because two runs whose start model changed in between
    are not comparable. Only use cases the caller may run are listed.

    **Finished**, because a run creates every result before the first question is sent and fills
    them as it goes: one that stopped halfway, or is still going, is not a standing, and its
    never-asked questions would read as answers nobody has rated yet.

    One query for the runs and one for their answers, however many use cases there are: the page
    is opened by everybody who runs the catalogue, and the run history only grows.
    """

    permission_classes = [IsAuthenticated, MayRunTests]

    def list(self, request: Request) -> Response:
        visible = may_run_tests_queryset(request.user, UseCase.objects.all()).values_list(
            "slug", flat=True
        )
        finished = TestRun.objects.filter(finished_at__isnull=False)
        latest = finished.filter(use_case=OuterRef("use_case")).order_by("-started_at", "-id")
        runs = (
            finished.filter(use_case__in=visible, pk=Subquery(latest.values("pk")[:1]))
            .select_related("requested_by")
            .prefetch_related("results")
            .order_by("use_case")
        )

        rows = []
        asked = TestCase.objects.filter(retired=False).count()
        for run in runs:
            results = list(run.results.all())
            rows.append(
                {
                    "use_case": run.use_case,
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
