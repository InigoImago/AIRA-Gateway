"""Runs of the catalogue against one use case's pipeline, and the answers in them (`ADR-0020`).

**A run is somebody's traffic**, and it carries a use case's own answers — what its filter caught,
what its redactor rewrote. So runs and results are readable only by somebody who could have
started them: `may_run_tests_queryset`, the same rule that decides who may run.
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.smoketests.models import TestCase, TestResult, TestRun, Verdict
from aira_management.apps.smoketests.serializers import TestResultSerializer, TestRunSerializer
from aira_management.apps.smoketests.views.export import run_as_csv
from aira_management.apps.smoketests.views.runnable import runnable
from aira_management.apps.usecases.access import may_run_tests_queryset
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import MayRunTests


class TestRunViewSet(viewsets.ModelViewSet[TestRun]):
    """Running the catalogue is **making requests**, in a use case one may run it in.

    IT Security is a member of nothing (`ADR-0007`) and every member may send a request, so neither
    "incident roles" nor "members" is the rule; `may_run_tests_queryset` holds both facts.
    """

    serializer_class = TestRunSerializer
    permission_classes = [IsAuthenticated, MayRunTests]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self) -> QuerySet[TestRun]:
        runs = TestRun.objects.prefetch_related("results").filter(
            use_case__in=may_run_tests_queryset(
                self.request.user, UseCase.objects.all()
            ).values_list("slug", flat=True)
        )
        use_case = self.request.query_params.get("use_case", "")
        if use_case:
            runs = runs.filter(use_case=use_case)
        # `?model=` means the model a run **entered** at: "how has this model behaved over time"
        # is answered by the runs of every use case whose pipeline started there.
        model = self.request.query_params.get("model", "")
        return runs.filter(model=model) if model else runs

    def perform_create(self, serializer: Any) -> None:
        """Start a run: one result row per case, all unrated and unanswered.

        The rows exist **before** the first prompt is sent, so an interrupted run shows what it did
        not get to rather than looking complete and short.
        """
        use_case = self._use_case_the_caller_may_run(serializer.validated_data.get("use_case", ""))
        released, why_not = runnable(use_case)
        if why_not:
            raise ValidationError({"use_case": [why_not]})
        run = serializer.save(
            requested_by=self.request.user,
            model=self._entry_model(serializer.validated_data.get("model", ""), use_case, released),
        )
        TestResult.objects.bulk_create(
            TestResult(run=run, case=case) for case in TestCase.objects.filter(retired=False)
        )

    def _entry_model(self, wanted: str, use_case: UseCase, released: list[str]) -> str:
        """Which model this run enters the pipeline at — **the caller's choice, bounded**.

        Bounded by what is *released* to the use case (`FRD-504` §5.8), and refused by name
        otherwise: the gateway would refuse it at dispatch, producing a run full of 403s that says
        nothing. Empty means "whichever" and takes the first released model — a run must record
        *some* entry point or its results compare with nothing.
        """
        chosen = str(wanted or "").strip()
        if not chosen:
            return released[0]
        if chosen not in released:
            raise ValidationError(
                {
                    "model": [
                        f"'{chosen}' is not released to '{use_case.slug}'. A run may only be "
                        f"entered at a model the use case may call: {', '.join(released)}."
                    ]
                }
            )
        return chosen

    def _use_case_the_caller_may_run(self, slug: str) -> UseCase:
        """The use case this run is about, or a refusal.

        Asked **per object**: `MayRunTests` only answers "is there any use case this person could
        run", and a caller who administers one must not then name somebody else's slug.
        """
        if not slug:
            raise ValidationError(
                {"use_case": ["Name the use case whose pipeline to put the catalogue to."]}
            )
        use_case = may_run_tests_queryset(
            self.request.user, UseCase.objects.filter(slug=slug)
        ).first()
        if use_case is None:
            # One answer for "no such use case" and "not yours to call", so the refusal does not
            # confirm that a use case exists.
            raise ValidationError(
                {
                    "use_case": [
                        f"'{slug}' is not a use case you may run the catalogue in. Running it "
                        "needs administration of the use case, not membership of it."
                    ]
                }
            )
        return use_case

    @action(detail=True, methods=["get"])
    def results(self, request: Request, pk: str | None = None) -> Response:
        run = self.get_object()
        rows = run.results.select_related("case", "rated_by").all()
        return Response(TestResultSerializer(rows, many=True).data)

    @action(detail=True, methods=["post"])
    def finish(self, request: Request, pk: str | None = None) -> Response:
        run = self.get_object()
        run.finished_at = timezone.now()
        run.save(update_fields=["finished_at"])
        return Response(TestRunSerializer(run).data)

    @action(detail=True, methods=["get"])
    def export(self, request: Request, pk: str | None = None) -> HttpResponse:
        return run_as_csv(self.get_object())


class TestResultViewSet(viewsets.ModelViewSet[TestResult]):
    """One answer, and the verdict somebody gave it — scoped like the runs."""

    serializer_class = TestResultSerializer
    permission_classes = [IsAuthenticated, MayRunTests]
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self) -> QuerySet[TestResult]:
        return TestResult.objects.select_related("case", "run", "rated_by").filter(
            run__use_case__in=may_run_tests_queryset(
                self.request.user, UseCase.objects.all()
            ).values_list("slug", flat=True)
        )

    def perform_update(self, serializer: Any) -> None:
        """Store an answer, or a verdict, or both.

        The rating stamps its author here rather than accepting one from the caller: a judgement
        that names somebody who did not make it is worse than an anonymous one.
        """
        verdict = serializer.validated_data.get("verdict")
        if verdict and verdict != Verdict.UNRATED:
            serializer.save(rated_by=self.request.user, rated_at=timezone.now())
        else:
            serializer.save()
