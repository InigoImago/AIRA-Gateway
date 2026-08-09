"""The smoke-test API (`FRD-504`).

Bounded by **role, not by use case**: the catalogue is a statement about a model, and a model is the
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

from django.db.models import QuerySet
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.usecases.access import may_call_queryset
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import IsITSecurity, MayTestModels

from .models import SMOKE_TEST_USE_CASE, TestCase, TestResult, TestRun, Verdict
from .serializers import (
    TestCaseSerializer,
    TestResultSerializer,
    TestRunSerializer,
)


class TestAttributionViewSet(viewsets.ViewSet):
    """Where a run is booked, and whether this caller may book one.

    **One server answer, so the slug is written once.** The console needs three facts before it can
    offer a Run button — which use case, whether it exists, and whether the gateway will accept this
    caller for it — and all three are the server's to know. A console that held the slug itself
    would go silently wrong the day the seed renamed it, and a console that decided the third fact
    from a membership list would repeat the defect this endpoint exists because of: on 2026-08-09 it
    asked Management's `is_member`, which grants a global admin everything, and every question of a
    run came back `Not a member of use case 'addr-1nn4ss'`.

    `may_call` is the **gateway's** rule (`may_call_queryset` → `aira_common.access.resolve`), not
    visibility and not administration.
    """

    permission_classes = [IsAuthenticated, MayTestModels]

    def list(self, request: Request) -> Response:
        use_case = UseCase.objects.filter(slug=SMOKE_TEST_USE_CASE).first()
        may_call = (
            use_case is not None
            and may_call_queryset(request.user, UseCase.objects.filter(pk=use_case.pk)).exists()
        )
        return Response(
            {
                "use_case": SMOKE_TEST_USE_CASE,
                "name": use_case.name if use_case else "",
                "exists": use_case is not None,
                "may_call": may_call,
            }
        )


class TestCaseViewSet(viewsets.ModelViewSet[TestCase]):
    """The catalogue. **Retired questions are not part of it** — they are history, kept because
    somebody judged answers against their wording, and listing them would promise a longer run than
    is performed."""

    queryset = TestCase.objects.filter(retired=False)
    serializer_class = TestCaseSerializer

    def get_permissions(self) -> list[Any]:
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsAuthenticated(), MayTestModels()]
        return [IsAuthenticated(), IsITSecurity()]


class TestRunViewSet(viewsets.ModelViewSet[TestRun]):
    """Running the catalogue is **making requests**, so whoever may call a model may test one.

    Narrowing this to the incident roles was the first design and it did not survive contact:
    running a run needs an incident role *and* membership of a use case to attribute the traffic
    to — and IT Security is deliberately a member of nothing (`ADR-0007`). No seeded user could do
    both, which is the clearest possible sign that the two requirements were not the same
    requirement. Authoring the **catalogue** stays with IT Security; running it is ordinary work.
    """

    serializer_class = TestRunSerializer
    permission_classes = [IsAuthenticated, MayTestModels]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self) -> QuerySet[TestRun]:
        runs = TestRun.objects.prefetch_related("results")
        model = self.request.query_params.get("model", "")
        return runs.filter(model=model) if model else runs

    def perform_create(self, serializer: Any) -> None:
        """Start a run: one result row per case, all unrated and unanswered.

        The rows exist **before** the first prompt is sent, so a run that is interrupted halfway
        shows what it did not get to rather than looking complete and short.
        """
        run = serializer.save(requested_by=self.request.user)
        TestResult.objects.bulk_create(
            TestResult(run=run, case=case) for case in TestCase.objects.filter(retired=False)
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
    forever, and makes the number move when somebody re-runs something unrelated. That was the
    first version and it was wrong: it answered a question nobody asked.

    One row per model, because there is one catalogue. The first version grouped questions into
    batteries and reported a row per model *and battery*, so "how does this model do" had
    as many answers as there were groups and none of them compared to a model asked a different
    group.
    """

    permission_classes = [IsAuthenticated, MayTestModels]

    def list(self, request: Request) -> Response:
        latest: dict[str, TestRun] = {}
        for run in TestRun.objects.order_by("model", "-started_at"):
            latest.setdefault(run.model, run)

        rows = []
        asked = TestCase.objects.filter(retired=False).count()
        for model, run in sorted(latest.items()):
            counts = {"total": 0, "unrated": 0, "pass": 0, "fail": 0, "unclear": 0, "errored": 0}
            for result in run.results.all():
                counts["total"] += 1
                counts[result.verdict] = counts.get(result.verdict, 0) + 1
                if result.error:
                    counts["errored"] += 1
            rows.append(
                {
                    "model": model,
                    "run": run.id,
                    "started_at": run.started_at.isoformat(),
                    "requested_by": getattr(run.requested_by, "username", "") or "",
                    # How many questions the catalogue asks *today*. A run made before questions
                    # were added answered fewer, and saying so is the difference between "this
                    # model scored 40" and "this model scored 40 out of a catalogue that has since
                    # grown to 100".
                    "catalogue": asked,
                    **counts,
                }
            )
        return Response(rows)
