"""The smoke-test API (`FRD-504`).

Bounded by **role, not by use case**: a battery is a statement about a model, and a model is the
installation's, not one team's. IT Security and Global Administrators — the same `INCIDENT_ROLES`
that may stop traffic and author a global rule, because this is the same job.

The run itself is driven by the **console**, which sends each prompt through the gateway with the
signed-in person's own credentials and posts the answer back here. That is deliberate and it is
`FRD-504` §5: a smoke test must travel the ordinary request path, or it measures a path nobody
uses. It is priced, budgeted, rate-limited and audited exactly like any other traffic — which also
means an installation can see what its own testing costs.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from django.db.models import Prefetch, QuerySet
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.rbac import IsITSecurity, MayTestModels

from .models import TestBattery, TestCase, TestResult, TestRun, Verdict
from .serializers import (
    TestBatterySerializer,
    TestCaseSerializer,
    TestResultSerializer,
    TestRunSerializer,
)


class TestBatteryViewSet(viewsets.ModelViewSet[TestBattery]):
    """**Reading is not authoring.** Anybody who may run a battery must be able to choose one, or
    the picker is empty and the Run button is disabled for a reason nothing on screen explains —
    which is exactly what happened the first time. Writing one stays with IT Security: a battery is
    a statement about what this installation considers acceptable."""

    queryset = TestBattery.objects.prefetch_related(
        # A retired question is history, not part of the standard: it is not listed and not asked.
        Prefetch("cases", queryset=TestCase.objects.filter(retired=False))
    ).all()
    serializer_class = TestBatterySerializer

    def get_permissions(self) -> list[Any]:
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsAuthenticated(), MayTestModels()]
        return [IsAuthenticated(), IsITSecurity()]


class TestCaseViewSet(viewsets.ModelViewSet[TestCase]):
    queryset = TestCase.objects.select_related("battery").all()
    serializer_class = TestCaseSerializer

    def get_permissions(self) -> list[Any]:
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsAuthenticated(), MayTestModels()]
        return [IsAuthenticated(), IsITSecurity()]


class TestRunViewSet(viewsets.ModelViewSet[TestRun]):
    """Running a battery is **making requests**, so whoever may call a model may test one.

    Narrowing this to the incident roles was the first design and it did not survive contact:
    running a run needs an incident role *and* membership of a use case to attribute the traffic
    to — and IT Security is deliberately a member of nothing (`ADR-0007`). No seeded user could do
    both, which is the clearest possible sign that the two requirements were not the same
    requirement. Authoring a **battery** stays with IT Security; running one is ordinary work.
    """

    serializer_class = TestRunSerializer
    permission_classes = [IsAuthenticated, MayTestModels]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self) -> QuerySet[TestRun]:
        runs = TestRun.objects.select_related("battery").prefetch_related("results")
        model = self.request.query_params.get("model", "")
        return runs.filter(model=model) if model else runs

    def perform_create(self, serializer: Any) -> None:
        """Start a run: one result row per case, all unrated and unanswered.

        The rows exist **before** the first prompt is sent, so a run that is interrupted halfway
        shows what it did not get to rather than looking complete and short.
        """
        run = serializer.save(requested_by=self.request.user)
        TestResult.objects.bulk_create(
            TestResult(run=run, case=case) for case in run.battery.cases.filter(retired=False)
        )

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
        """The evaluation as CSV, for the spreadsheet somebody will inevitably want.

        The same conventions `FRD-602` had to get right once already, and for the same reasons: a
        **BOM** so Excel reads UTF-8 rather than guessing, **CRLF** because that is what RFC 4180
        says, and every field quoted — a topic containing a comma would otherwise shift every
        column after it one to the left, silently, in a file somebody then forwards.
        """
        run = self.get_object()
        buffer = io.StringIO()
        writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        writer.writerow(
            [
                "topic",
                "prompt",
                "expectation",
                "response",
                "error",
                "latency_ms",
                "verdict",
                "note",
                "rated_by",
                "rated_at",
            ]
        )
        for row in run.results.select_related("case", "rated_by").all():
            writer.writerow(
                [
                    row.case.topic,
                    row.case.prompt,
                    row.case.expectation,
                    row.response,
                    row.error,
                    row.latency_ms if row.latency_ms is not None else "",
                    row.verdict,
                    row.note,
                    getattr(row.rated_by, "username", "") or "",
                    row.rated_at.isoformat() if row.rated_at else "",
                ]
            )
        name = f"aira-smoketest-{run.model.replace('/', '_')}-{run.started_at.date()}.csv"
        # A plain `HttpResponse`, not DRF's: a `Response` goes through content negotiation and a
        # renderer, which re-encodes the body and eats the BOM — the one byte sequence the whole
        # point of which is to survive being handed to Excel.
        response = HttpResponse(
            ("\ufeff" + buffer.getvalue()).encode("utf-8"),
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response


class TestResultViewSet(viewsets.ModelViewSet[TestResult]):
    queryset = TestResult.objects.select_related("case", "run", "rated_by").all()
    serializer_class = TestResultSerializer
    permission_classes = [IsAuthenticated, MayTestModels]
    http_method_names = ["get", "patch", "head", "options"]

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


class TestStatsViewSet(viewsets.ViewSet):
    """**The latest run per model**, and deliberately not a total across all of them.

    The point of a standardised catalogue is comparing models against the *same* questions — so the
    figure that answers "how does this model do" is its **most recent** run, not an average over
    every run it has ever had. Summing them makes an old, worse result drag a corrected one down
    forever, and makes the number move when somebody re-runs an unrelated battery. That was the
    first version and it was wrong: it answered a question nobody asked.

    A model that has been run against two different batteries appears once per battery, because
    those are two different standards and averaging them would compare nothing to nothing.
    """

    permission_classes = [IsAuthenticated, MayTestModels]

    def list(self, request: Request) -> Response:
        runs = TestRun.objects.select_related("battery").order_by(
            "model", "battery_id", "-started_at"
        )
        wanted = request.query_params.get("battery", "")
        if wanted:
            # Parsed, not passed through. `?battery=abc` reaching the ORM as a string is a 500
            # where the honest answer is "that is not a battery id" — the routing-handler finding
            # of 2026-08-06, one layer in. mypy caught this before a caller did.
            if not wanted.isdigit():
                return Response(
                    {"error": {"code": "INVALID_ARGUMENT", "message": "battery must be an id"}},
                    status=400,
                )
            runs = runs.filter(battery_id=int(wanted))

        latest: dict[tuple[str, int], TestRun] = {}
        for run in runs:
            latest.setdefault((run.model, run.battery_id), run)

        rows = []
        for (model, _battery_id), run in sorted(latest.items()):
            counts = {"total": 0, "unrated": 0, "pass": 0, "fail": 0, "unclear": 0, "errored": 0}
            for result in run.results.all():
                counts["total"] += 1
                counts[result.verdict] = counts.get(result.verdict, 0) + 1
                if result.error:
                    counts["errored"] += 1
            rows.append(
                {
                    "model": model,
                    "battery": run.battery_id,
                    "battery_name": run.battery.name,
                    "run": run.id,
                    "started_at": run.started_at.isoformat(),
                    "requested_by": getattr(run.requested_by, "username", "") or "",
                    **counts,
                }
            )
        return Response(rows)
