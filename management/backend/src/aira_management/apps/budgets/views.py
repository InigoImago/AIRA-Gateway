"""The installation's own budget (`FRD-610`): the spend that belongs to no use case.

**Its own route, not a use case's.** `/use-cases/<slug>/budgets/` resolves its object from a slug;
this budget has none by definition, and bending that route would give *"which use case is this
for"* a special answer at every layer that asks it.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_common.permissions import Permission
from aira_management.apps.budgets.models import Budget
from aira_management.apps.budgets.serializers import BudgetSerializer, InstallationBudgetSerializer
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.views.payloads import _budget_payload
from aira_management.rbac import may, requires


class InstallationBudgetViewSet(viewsets.ViewSet):
    """Read for anybody who oversees the installation; write for a Global Administrator.

    `ADR-0007`: IT Steuerung oversees and acts in nothing, so it **reads** this figure, and a
    Global Administrator sets it.
    """

    def get_permissions(self) -> list[Any]:
        if self.action == "list":
            return [IsAuthenticated()]
        return [IsAuthenticated(), requires(Permission.BUDGET_INSTALLATION_WRITE)()]

    def list(self, request: Request) -> Response:
        rows = Budget.objects.filter(use_case__isnull=True).order_by("period")
        # Whoever may set it sees what they set: an empty answer would read as "none set", and the
        # next save would silently replace the budget it could not see.
        if may(request.user, Permission.BUDGET_INSTALLATION_WRITE):
            return Response(BudgetSerializer(rows, many=True).data)
        if not may(request.user, Permission.REPORT_READ_ALL):
            return Response([], status=status.HTTP_200_OK)
        return Response(BudgetSerializer(rows, many=True).data)

    def create(self, request: Request) -> Response:
        """Upsert on the period, which is the only thing that distinguishes two of these.

        A use-case budget upserts on `(scope, subject, period)`; here `scope` is always
        `installation` and `subject` is always empty, so the key is the period alone — and the
        partial unique constraint in the model says the same thing to the database.
        """
        body = request.data if isinstance(request.data, dict) else {}
        serializer = InstallationBudgetSerializer(data={**body, "scope": Budget.INSTALLATION})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        defaults: dict[str, Any] = {
            "limit_cost": data.get("limit_cost"),
            "limit_tokens": data.get("limit_tokens"),
            "limit_requests": data.get("limit_requests"),
        }
        # Only when it was said: an upsert that does not mention `enabled` must not switch a
        # deliberately disabled budget back on.
        if "enabled" in data:
            defaults["enabled"] = data["enabled"]
        with transaction.atomic():
            budget, _created = Budget.objects.update_or_create(
                use_case=None,
                scope=Budget.INSTALLATION,
                subject="",
                period=data["period"],
                defaults=defaults,
            )
            # Every budget's shape; an **empty** `use_case` is what selects the installation scope.
            emit("budget.upserted", _budget_payload(budget, ""))
        return Response(BudgetSerializer(budget).data, status=status.HTTP_201_CREATED)

    def destroy(self, request: Request, pk: str | None = None) -> Response:
        # The id in the path is the caller's own value and must never become a 500: the router
        # passes any word, and Django raises while *building* the query. What
        # `generics.get_object_or_404` does for a `ModelViewSet`, spelled out to keep this 404 body.
        budget = None
        if pk is not None:
            try:
                budget = Budget.objects.filter(pk=pk, use_case__isnull=True).first()
            except TypeError, ValueError:
                budget = None
        if budget is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        with transaction.atomic():
            budget_id = budget.pk
            period = budget.period
            budget.delete()
            # Announced, or the gateway keeps enforcing a limit nobody can see (`FRD-205`). It
            # drops the row, **not the counters**: consumption is keyed by `(scope, period)`, so a
            # budget recreated within the same period gets no fresh allowance. The gateway resolves
            # the row by `id`; `use_case` and `period` are for a reader of the topic.
            emit("budget.deleted", {"id": budget_id, "use_case": "", "period": period})
        return Response(status=status.HTTP_204_NO_CONTENT)
