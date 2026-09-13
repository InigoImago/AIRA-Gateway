"""Where the catalogue can be run, and at which models (`ADR-0020`).

Decided here rather than in the console, so a screen never offers a Run button the server refuses
(`FRD-206`).
"""

from __future__ import annotations

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.usecases.access import may_run_tests_queryset
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import MayRunTests

#: Why a use case cannot be run — one wording for the API's refusal and the console's explanation.
#: There is no "has no pipeline" case: every use case has one, and no steps is a configuration.
NOTHING_RELEASED = (
    "No model is released to this use case, so there is nothing to put the questions to. An "
    "administrator of the use case releases models on its Models tab; the run is then entered at "
    "whichever of them you choose."
)


def entry_models(use_case: UseCase) -> list[str]:
    """The models a run may be entered at: exactly what this use case has been released.

    A property of the *run*, not a field on the pipeline (owner's decision): two runs of one use
    case may enter at different models, which is the comparison someone evaluating a model wants.
    Sorted, because it feeds a picker.
    """
    return sorted(use_case.allowed_models.values_list("name", flat=True))


def runnable(use_case: UseCase) -> tuple[list[str], str]:
    """``(models, why_not)`` for one use case — exactly one of the two is set."""
    models = entry_models(use_case)
    return (models, "") if models else ([], NOTHING_RELEASED)


class TestAttributionViewSet(viewsets.ViewSet):
    """Per use case the caller may run: the models a run may enter at, or why it cannot run.

    "May run" is `may_run_tests_queryset` — the gateway's acceptance of this caller for the use
    case, not Management's visibility, plus administration or an installation role.
    """

    permission_classes = [IsAuthenticated, MayRunTests]

    def list(self, request: Request) -> Response:
        callable_ones = may_run_tests_queryset(
            request.user, UseCase.objects.all()
        ).prefetch_related("allowed_models")
        rows = []
        for use_case in callable_ones.order_by("name", "slug"):
            models, why_not = runnable(use_case)
            rows.append(
                {
                    "use_case": use_case.slug,
                    "name": use_case.name,
                    "models": models,
                    "may_run": not why_not,
                    "why_not": why_not,
                }
            )
        return Response(rows)
