"""The questions themselves (`FRD-504`): read by anyone who may run them, written by IT Security."""

from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Max
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from aira_common.permissions import Permission
from aira_management.apps.smoketests.models import TestCase
from aira_management.apps.smoketests.serializers import TestCaseSerializer
from aira_management.rbac import MayRunTests, requires

#: How often an append is tried again when a concurrent one took the same position. Two at once is
#: already rare; losing three times in a row is answered as a refusal, never as a 500.
APPEND_ATTEMPTS = 3


class TestCaseViewSet(viewsets.ModelViewSet[TestCase]):
    """The catalogue. **Retired questions are not part of it** — they are history, kept because
    somebody judged answers against their wording."""

    queryset = TestCase.objects.filter(retired=False)
    serializer_class = TestCaseSerializer

    def get_permissions(self) -> list[Any]:
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsAuthenticated(), MayRunTests()]
        return [IsAuthenticated(), requires(Permission.SMOKETEST_AUTHOR)()]

    def perform_create(self, serializer: Any) -> None:
        """Add a question — **appended** when no position is sent.

        A position is unique among live questions, and retiring one leaves a gap, so any number a
        client derives from what it can see (`count + 1`) can land on one still in use, every time
        it retries. Appending goes after every position ever used, live or retired: a retired
        question keeps its position as history. A position that *is* sent is still the one used.
        """
        placed = "position" in serializer.validated_data
        for _attempt in range(1 if placed else APPEND_ATTEMPTS):
            try:
                with transaction.atomic():
                    if placed:
                        serializer.save()
                    else:
                        serializer.save(position=_next_position())
                return
            except IntegrityError:
                # Another question took the position between the check and the insert.
                continue
        raise ValidationError(
            {
                "position": [
                    "Another question took this position at the same moment. Send it again — "
                    "without a position, it is appended."
                ]
            }
        )

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


def _next_position() -> int:
    """One after the highest position any question has held, live or retired."""
    highest = TestCase.objects.aggregate(highest=Max("position"))["highest"]
    return int(highest or 0) + 1
