"""What every use-case resource mixin shares: the object-level predicates and child-row lookup.

The predicates themselves live in `access.py`, because the console asks the same questions to
decide what to put on screen (`FRD-206`).
"""

from __future__ import annotations

from django.db.models import Model
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError

from aira_management.apps.usecases.access import is_member, may_admin, may_manage
from aira_management.apps.usecases.models import UseCase


class UseCaseViewBase(viewsets.GenericViewSet[UseCase]):
    """The base of every resource mixin, so each can resolve the use case and ask about it."""

    def _may_admin(self, usecase: UseCase) -> bool:
        return may_admin(self.request.user, usecase)

    def _may_manage(self, usecase: UseCase) -> bool:
        return may_manage(self.request.user, usecase)

    def _is_member(self, usecase: UseCase) -> bool:
        return is_member(self.request.user, usecase)

    def _child_to_delete[M: Model](
        self, model: type[M], child_id: str | None, *, key: str, noun: str, refusal: str
    ) -> tuple[UseCase, M]:
        """The use case and its ``model`` row ``child_id``, for a delete route.

        Every per-use-case record (budget, rate limit, anomaly rule) is deleted only by somebody
        who manages the use case, and a missing id is a 400 naming it. The caller deletes and emits
        itself, so each event stays a literal `emit(...)` (`test_outbox_routing.py`).
        """
        usecase = self.get_object()
        if not self._may_manage(usecase):
            raise PermissionDenied(refusal)
        assert child_id is not None  # the URL route guarantees a numeric id
        row = model._default_manager.filter(use_case=usecase, pk=int(child_id)).first()
        if row is None:
            raise ValidationError({key: [f"No {noun} '{child_id}' for this use case."]})
        return usecase, row
