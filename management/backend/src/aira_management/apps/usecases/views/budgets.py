"""Usage budgets of one use case (`FRD-400`): members read, admins define."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.budgets.models import Budget
from aira_management.apps.budgets.serializers import BudgetSerializer
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.views.base import UseCaseViewBase
from aira_management.apps.usecases.views.payloads import _budget_payload


class BudgetsMixin(UseCaseViewBase):
    @action(detail=True, methods=["get", "post"], url_path="budgets")
    def budgets(self, request: Request, slug: str | None = None) -> Response:
        """List budgets, or upsert one on (scope, subject, period) and publish `budget.upserted`."""
        usecase = self.get_object()
        if request.method == "GET":
            budgets = Budget.objects.filter(use_case=usecase)
            return Response(BudgetSerializer(budgets, many=True).data)

        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot edit budgets of this use case.")
        serializer = BudgetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        defaults: dict[str, Any] = {
            "limit_cost": data.get("limit_cost"),
            "limit_tokens": data.get("limit_tokens"),
            "limit_requests": data.get("limit_requests"),
        }
        # `enabled` only when it was said: the console's save never mentions it, and an upsert
        # that switched a deliberately lifted budget back on would reverse a decision silently.
        # Absent on a create still means the model's default, which is on.
        if "enabled" in data:
            defaults["enabled"] = data["enabled"]
        with transaction.atomic():
            budget, _created = Budget.objects.update_or_create(
                use_case=usecase,
                scope=data["scope"],
                subject=data["subject"],
                period=data["period"],
                defaults=defaults,
            )
            emit("budget.upserted", _budget_payload(budget, usecase.slug))
        return Response(BudgetSerializer(budget).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path="budgets/(?P<budget_id>[0-9]+)")
    def delete_budget(
        self, request: Request, slug: str | None = None, budget_id: str | None = None
    ) -> Response:
        usecase, budget = self._child_to_delete(
            Budget,
            budget_id,
            key="budget",
            noun="budget",
            refusal="You cannot edit budgets of this use case.",
        )
        with transaction.atomic():
            budget_pk = budget.pk
            budget.delete()
            emit("budget.deleted", {"id": budget_pk, "use_case": usecase.slug})
        return Response(status=status.HTTP_204_NO_CONTENT)
