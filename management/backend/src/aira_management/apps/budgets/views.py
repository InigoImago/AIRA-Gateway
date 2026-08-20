"""The installation's own budget (`FRD-610`).

**Its own route, not a use case's.** `/use-cases/<slug>/budgets/` reads a slug out of the path and
resolves the object from it; this budget has no slug by definition, and bending that route to
accept an absent one would make *"which use case is this for"* a question with a special answer at
every layer that asks it.

What it bounds is the spend that belongs to nobody: the console's model checks, break-glass keys,
demo traffic. Measured on a running installation before this existed — 59 audit rows carrying no
use case, and no allowance that could ever see them.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.budgets.models import Budget
from aira_management.apps.budgets.serializers import BudgetSerializer
from aira_management.apps.usecases.events import emit
from aira_management.rbac import IsGlobalAdmin, has_oversight_role


def payload(budget: Budget) -> dict[str, Any]:
    """The event the gateway applies. `use_case` is **empty**, which is what selects the scope."""
    return {
        "id": budget.pk,
        "use_case": "",
        "scope": budget.scope,
        "subject": budget.subject,
        "period": budget.period,
        # Decimal as a string: JSON numbers are floats, and money must not round-trip through one.
        "limit_cost": str(budget.limit_cost) if budget.limit_cost is not None else None,
        "limit_tokens": budget.limit_tokens,
        "limit_requests": budget.limit_requests,
        "enabled": budget.enabled,
    }


class InstallationBudgetViewSet(viewsets.ViewSet):
    """Read for anybody who oversees the installation; write for a Global Administrator.

    The split follows `ADR-0007` and the owner's open question about it: `IT Steuerung` oversees
    and acts in nothing, so it **reads** this figure — the installation's own spend is exactly what
    a governance role is there to see — and a Global Administrator sets it. If that should change,
    it changes here and nowhere else.
    """

    def get_permissions(self) -> list[Any]:
        if self.action == "list":
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsGlobalAdmin()]

    def list(self, request: Request) -> Response:
        if not has_oversight_role(request.user):
            return Response([], status=status.HTTP_200_OK)
        rows = Budget.objects.filter(use_case__isnull=True).order_by("period")
        return Response(BudgetSerializer(rows, many=True).data)

    def create(self, request: Request) -> Response:
        """Upsert on the period, which is the only thing that distinguishes two of these.

        A use-case budget upserts on `(scope, subject, period)`; here `scope` is always
        `installation` and `subject` is always empty, so the key is the period alone — and the
        partial unique constraint in the model says the same thing to the database.
        """
        body = request.data if isinstance(request.data, dict) else {}
        serializer = BudgetSerializer(data={**body, "scope": Budget.INSTALLATION})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        defaults: dict[str, Any] = {
            "limit_cost": data.get("limit_cost"),
            "limit_tokens": data.get("limit_tokens"),
            "limit_requests": data.get("limit_requests"),
        }
        # Only when it was said — the same rule the use-case route learned the hard way: an upsert
        # that does not mention `enabled` must not switch a deliberately disabled budget back on.
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
            emit("budget.upserted", payload(budget))
        return Response(BudgetSerializer(budget).data, status=status.HTTP_201_CREATED)

    def destroy(self, request: Request, pk: str | None = None) -> Response:
        # **A caller's own value must never become a server error**, and the id in the path is one.
        # The router's default lookup is `[^/.]+`, so `pk` reaches here as any word — and Django
        # raises `ValueError: Field 'id' expected a number` while *building* the query, which DRF
        # renders as a **500** for a route that has one honest answer: there is no such budget.
        # `pk or 0` guarded the empty string and nothing else.
        #
        # This is what `rest_framework.generics.get_object_or_404` does and why it exists; every
        # `ModelViewSet` in this project is covered by it, and this hand-written one was the single
        # route that resolves an id itself. Spelled out rather than borrowed so the 404 keeps the
        # body it already had.
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
            # Deleting the budget without saying so would leave the gateway enforcing a limit
            # nobody can see — the shape `FRD-205` found once with API keys. So the event goes out
            # and the gateway drops the row.
            #
            # **It drops the row, not the counters**, and this comment claimed otherwise until
            # 2026-08-20. `consumer.apply._delete_budget` deletes the `BudgetRead` and touches
            # neither `budget_usage` nor the shared counter — which is right and is worth stating
            # rather than mis-stating: consumption is keyed by `(scope, period)` and not by a
            # budget id, so it is a fact about what was spent rather than about the rule that was
            # in force. The consequence a reader needs: recreating this budget inside the same
            # period does **not** hand it a fresh allowance, because the spend it would be
            # measuring against really happened. `use_case` and `period` ride along for a reader of
            # the topic; the gateway resolves the row by `id`.
            emit("budget.deleted", {"id": budget_id, "use_case": "", "period": period})
        return Response(status=status.HTTP_204_NO_CONTENT)
