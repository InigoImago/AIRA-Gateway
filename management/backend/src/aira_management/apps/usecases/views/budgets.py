"""Usage budgets of one use case (`FRD-400`, `FRD-407`): members read, admins define."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.budgets.models import Budget
from aira_management.apps.budgets.serializers import BudgetSerializer
from aira_management.apps.usecases.access import may_call
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase
from aira_management.apps.usecases.views.base import UseCaseViewBase
from aira_management.apps.usecases.views.grants import _refuse_a_name_the_gateway_reads_differently
from aira_management.apps.usecases.views.payloads import _budget_payload


def _person_named(usecase: UseCase, typed: str) -> str:
    """The username a person's own budget is stored under: **the account's own spelling**, the
    key the gateway counts that person's allowance by (`scopes.person`).

    Three refusals, each with the fix. A name no account has would be a budget binding nobody; a
    name the gateway reads as somebody else would bind the wrong person (`FRD-613`); and somebody
    who cannot call the use case has no requests for the budget to bound — the gateway's own rule,
    asked through `may_call`, so a Global Administrator's blanket is no access here.
    """
    person = get_user_model().objects.filter(username=typed).first()
    if person is None:
        raise ValidationError(
            {
                "subject": [
                    f"There is no user '{typed}'. A budget is set for somebody who has an account "
                    "here — they have signed in to the console once, or were added as a member."
                ]
            }
        )
    try:
        _refuse_a_name_the_gateway_reads_differently(person)
    except ValidationError as refused:
        detail: Any = refused.detail
        raise ValidationError({"subject": detail.get("username", detail)}) from None
    if not may_call(person, usecase):
        raise ValidationError(
            {
                "subject": [
                    f"'{typed}' cannot call this use case, so a budget for them would bind nobody. "
                    "Give them access first — as a member, or through a group."
                ]
            }
        )
    return str(person.get_username())


class BudgetsMixin(UseCaseViewBase):
    @action(detail=True, methods=["get", "post"], url_path="budgets")
    def budgets(self, request: Request, slug: str | None = None) -> Response:
        """List budgets, or upsert one on (scope, subject, period) and publish `budget.upserted`."""
        usecase = self.get_object()
        context = {"viewer": request.user}
        if request.method == "GET":
            budgets = Budget.objects.filter(use_case=usecase).select_related("use_case")
            return Response(BudgetSerializer(budgets, many=True, context=context).data)

        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot edit budgets of this use case.")
        serializer = BudgetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        subject = data["subject"]
        if data["scope"] == Budget.MEMBER:
            subject = _person_named(usecase, subject)
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
                subject=subject,
                period=data["period"],
                defaults=defaults,
            )
            emit("budget.upserted", _budget_payload(budget, usecase.slug))
        return Response(
            BudgetSerializer(budget, context=context).data, status=status.HTTP_201_CREATED
        )

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
