"""The question-catalogue API (`FRD-504`, `ADR-0020`).

**Two different questions, asked separately, and conflating them is this file's oldest defect.**

*Writing* the catalogue is bounded by role: it states what this installation considers an
acceptable answer, which is the installation's statement and not one team's — Global Administrators
and IT Security, the same people who author a global anomaly rule, because it is the same job.

*Running* it is bounded per use case, by `access.may_run_tests_queryset`: the gateway would accept
this caller for that use case **and** they administer it, or they hold one of the two installation
roles. A normal use-case user may not (owner's rule, 2026-08-16) — a run spends the use case's
budget a hundred prompts at a time and reads prompts §8 calls sensitive, which is a decision about
the use case rather than work inside it.

The run itself is driven by the **console**, which sends each prompt through the gateway with the
signed-in person's own credentials and posts the answer back here. That is deliberate and it is
`FRD-504` §5: a run must travel the ordinary request path, or it measures a path nobody uses. It is
priced, budgeted, rate-limited and audited exactly like any other traffic, against the use case it
is about — which also means an installation can see what its own testing costs, and see it in the
place the cost belongs.
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
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.usecases.access import may_run_tests_queryset
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import IsITSecurity, MayRunTests

from .models import TestCase, TestResult, TestRun, Verdict
from .serializers import (
    TestCaseSerializer,
    TestResultSerializer,
    TestRunSerializer,
)

#: Why a use case cannot be run, in the words the console shows. One place, because the same three
#: sentences are the API's refusal and the screen's explanation, and a second copy is a second
#: wording for one rule.
NO_PIPELINE = (
    "This use case has no pipeline, so there is nothing to put the questions to. Build one on the "
    "use case's Pipeline tab and give it a start model."
)
NO_START_MODEL = (
    "This use case's pipeline has no start model, so a run has nowhere to begin. A request "
    "normally names its own model; the catalogue does not, so the pipeline has to say where it "
    "enters. Set it on the use case's Pipeline tab."
)


def _runnable(use_case: UseCase) -> tuple[str, str]:
    """``(start_model, why_not)`` for one use case — exactly one of the two is set.

    Asked here rather than in the console, for the reason `FRD-206` keeps arriving at: a screen
    that decides for itself offers a button the server refuses. The console shows `why_not` where
    it would otherwise show Run.
    """
    config = getattr(use_case, "pipeline", None)
    if config is None:
        return "", NO_PIPELINE
    start = str(config.start_model or "").strip()
    return (start, "") if start else ("", NO_START_MODEL)


class TestAttributionViewSet(viewsets.ViewSet):
    """**Where the catalogue can be run**, and where it cannot and why (`ADR-0020`).

    This used to answer one question about one seeded use case, because there was one place every
    run was attributed to. A run now names the use case whose pipeline it exercises, so the
    question became "which of my use cases can I put this to" — and the three facts the console
    needs per use case are all the server's:

    - may this caller **run** it (`may_run_tests_queryset`): would the gateway accept them for it
      — not Management's visibility, a distinction that cost every question of a run a
      `Not a member of use case …` on 2026-08-09 — *and* do they administer it or answer for the
      installation;
    - does it have a pipeline with a **start model**, which is where a run begins;
    - if not, **why not**, in a sentence naming what to do about it.

    A console that worked any of these out for itself would offer a Run button the server refuses,
    which is `FRD-206`'s defect and the reason this endpoint exists at all.
    """

    permission_classes = [IsAuthenticated, MayRunTests]

    def list(self, request: Request) -> Response:
        callable_ones = may_run_tests_queryset(request.user, UseCase.objects.all()).select_related(
            "pipeline"
        )
        rows = []
        for use_case in callable_ones.order_by("name", "slug"):
            start, why_not = _runnable(use_case)
            rows.append(
                {
                    "use_case": use_case.slug,
                    "name": use_case.name,
                    "start_model": start,
                    "may_run": not why_not,
                    "why_not": why_not,
                }
            )
        return Response(rows)


class TestCaseViewSet(viewsets.ModelViewSet[TestCase]):
    """The catalogue. **Retired questions are not part of it** — they are history, kept because
    somebody judged answers against their wording, and listing them would promise a longer run than
    is performed."""

    queryset = TestCase.objects.filter(retired=False)
    serializer_class = TestCaseSerializer

    def get_permissions(self) -> list[Any]:
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsAuthenticated(), MayRunTests()]
        return [IsAuthenticated(), IsITSecurity()]


class TestRunViewSet(viewsets.ModelViewSet[TestRun]):
    """Running the catalogue is **making requests**, in a use case one administers.

    Narrowing this to the incident roles *alone* was the first design and it did not survive
    contact: a run needs a use case to attribute its traffic to, and IT Security is deliberately a
    member of nothing (`ADR-0007`), so no seeded user could do both. Widening it to every member
    was the correction and it went one step too far — it handed a hundred prompts and the budget
    they spend to anybody who could send one. The rule that holds both facts is
    `may_run_tests_queryset`: the gateway's acceptance **and** administration of that use case, or
    an installation role.

    **A run is somebody's traffic, so it is only readable by somebody who could have started it**
    (`ADR-0020`). It used to be one shared use case and the list was unscoped, which was defensible
    while every run was about a model the whole installation had approved. A run now carries a use
    case's own answers — what its filter caught, what its redactor rewrote — and that is that use
    case's business.
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
        # `?model=` still narrows, and still means the model a run **entered** at. Kept because the
        # question "how has this model behaved over time" is a real one and the answer is the runs
        # of every use case whose pipeline started there.
        model = self.request.query_params.get("model", "")
        return runs.filter(model=model) if model else runs

    def perform_create(self, serializer: Any) -> None:
        """Start a run: one result row per case, all unrated and unanswered.

        The rows exist **before** the first prompt is sent, so a run that is interrupted halfway
        shows what it did not get to rather than looking complete and short.
        """
        use_case = self._use_case_the_caller_may_run(serializer.validated_data.get("use_case", ""))
        start, why_not = _runnable(use_case)
        if why_not:
            raise ValidationError({"use_case": [why_not]})
        # **The pipeline's start model, never the caller's.** A run enters where the pipeline says
        # it enters; asking the caller would be asking them to predict a decision the pipeline
        # makes, and a model the use case has not been released is refused at dispatch anyway
        # (`FRD-308`). Recorded on the row because a start model can change between two runs and
        # the older one is still evidence about the configuration it actually met.
        run = serializer.save(requested_by=self.request.user, model=start)
        TestResult.objects.bulk_create(
            TestResult(run=run, case=case) for case in TestCase.objects.filter(retired=False)
        )

    def _use_case_the_caller_may_run(self, slug: str) -> UseCase:
        """The use case this run is about, or a refusal.

        Asked **per object**, not once at the door: `MayRunTests` answers "is there any use case
        this person could run", which is the right question for showing a screen and the wrong one
        for starting a run. A caller who administers one use case would otherwise pass the class
        permission and then name somebody else's slug.

        The rule includes the gateway's own — not Management's visibility — because the run's
        traffic is sent with this caller's credentials. An oversight role sees every use case and
        may call none (`ADR-0007`), and offering them a run that fails on its first question is the
        `FRD-206` defect this endpoint's sibling exists to prevent.
        """
        if not slug:
            raise ValidationError(
                {"use_case": ["Name the use case whose pipeline to put the catalogue to."]}
            )
        use_case = may_run_tests_queryset(
            self.request.user, UseCase.objects.filter(slug=slug)
        ).first()
        if use_case is None:
            # One answer for "no such use case" and "not yours to call". Telling them apart would
            # confirm a use case exists to somebody who may not reach it.
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
    """One answer, and the verdict somebody gave it.

    Scoped to the runs this caller could have started, for the reason `TestRunViewSet` gives: a
    result now carries a use case's own answers, including whatever its redactor rewrote — and the
    question it answers, which §8 calls sensitive because it states what we test for.
    """

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


class TestStatsViewSet(viewsets.ViewSet):
    """**The latest run per use case**, and deliberately not a total across all of them.

    The point of a standardised catalogue is comparing things against the *same* questions — so the
    figure that answers "how does this stand" is the **most recent** run, not an average over every
    run there has ever been. Summing them makes an old, worse result drag a corrected one down
    forever, and makes the number move when somebody re-runs something unrelated. That was the
    first version and it was wrong: it answered a question nobody asked.

    **Per use case since `ADR-0020`**, because that is what a run is now about: the standing of a
    pipeline, not of a model. One row per use case, with the **start model that run entered at**
    named on it — a pipeline's start model can be changed between two runs, and a row that hid it
    would compare two configurations as though they were one.

    A model's standing is then the standing of a use case whose pipeline starts there, which is
    what IT Security's evaluation use case is for. That is a real loss of directness and it buys
    the thing that was missing: a use-case administrator can ask the same question about their own
    pipeline, and the two answers are comparable because the questions are the same.
    """

    permission_classes = [IsAuthenticated, MayRunTests]

    def list(self, request: Request) -> Response:
        # Only what this caller may actually run. A standing is about traffic somebody may send;
        # showing a use case they cannot run would be an entry in a table with no action behind it,
        # which is the shape `FRD-206` names.
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
            counts = {"total": 0, "unrated": 0, "pass": 0, "fail": 0, "unclear": 0, "errored": 0}
            for result in run.results.all():
                counts["total"] += 1
                counts[result.verdict] = counts.get(result.verdict, 0) + 1
                if result.error:
                    counts["errored"] += 1
            rows.append(
                {
                    "use_case": use_case,
                    # What that run entered the pipeline at. Named rather than implied: two runs of
                    # one use case whose start model changed in between are not comparable, and the
                    # row has to let a reader see that rather than average over it.
                    "model": run.model,
                    "run": run.id,
                    "started_at": run.started_at.isoformat(),
                    "requested_by": getattr(run.requested_by, "username", "") or "",
                    # How many questions the catalogue asks *today*. A run made before questions
                    # were added answered fewer, and saying so is the difference between "this
                    # scored 40" and "this scored 40 out of a catalogue that has since grown to
                    # 100".
                    "catalogue": asked,
                    **counts,
                }
            )
        return Response(rows)
