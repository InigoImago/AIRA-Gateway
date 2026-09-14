"""The questions themselves (`FRD-504`): read by anyone who may run them, written by IT Security."""

from __future__ import annotations

from typing import Any

from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from aira_common.permissions import Permission
from aira_management.apps.smoketests.models import TestCase
from aira_management.apps.smoketests.serializers import TestCaseSerializer
from aira_management.rbac import MayRunTests, requires


class TestCaseViewSet(viewsets.ModelViewSet[TestCase]):
    """The catalogue. **Retired questions are not part of it** — they are history, kept because
    somebody judged answers against their wording."""

    queryset = TestCase.objects.filter(retired=False)
    serializer_class = TestCaseSerializer

    def get_permissions(self) -> list[Any]:
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsAuthenticated(), MayRunTests()]
        return [IsAuthenticated(), requires(Permission.SMOKETEST_AUTHOR)()]

    def perform_destroy(self, instance: TestCase) -> None:
        """Refuse to delete a question somebody has already answered, and say what to do instead.

        `TestResult.case` is `PROTECT`: a verdict was formed against this wording, and the
        `ProtectedError` would otherwise surface as a 500. Refused by name rather than silently
        turned into a retirement — a DELETE that becomes a soft delete is a different verb.
        """
        if instance.results.exists():
            raise ValidationError(
                {
                    "detail": [
                        f"'{instance.topic}' has already been answered, and those verdicts were "
                        "formed against this wording. Retire it instead — it leaves the catalogue "
                        "and its answers keep their meaning."
                    ]
                }
            )
        super().perform_destroy(instance)
