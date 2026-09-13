"""The use-case viewset: CRUD over live use cases, composed from one mixin per resource.

Visibility follows `FRD-201`: the list is scoped (oversight roles see all, others what guardian
lets them view); editing needs `change_usecase` on the object, and managing what is inside it
`manage_members`, either of them or a Global Administrator.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from aira_management.apps.usecases.access import VIEW as _VIEW
from aira_management.apps.usecases.access import may_call_queryset
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.apps.usecases.serializers import UseCaseSerializer
from aira_management.apps.usecases.views.anomaly_rules import AnomalyRulesMixin
from aira_management.apps.usecases.views.api_keys import ApiKeysMixin
from aira_management.apps.usecases.views.budgets import BudgetsMixin
from aira_management.apps.usecases.views.grants import _grant
from aira_management.apps.usecases.views.members import MembersMixin
from aira_management.apps.usecases.views.payloads import _snapshot
from aira_management.apps.usecases.views.pipeline import PipelineMixin
from aira_management.apps.usecases.views.rate_limits import RateLimitsMixin
from aira_management.apps.usecases.views.retirement import RetirementMixin
from aira_management.pagination import ConsolePagination, apply_search
from aira_management.rbac import IsGlobalAdmin, scope_queryset


class UseCaseViewSet(
    RetirementMixin,
    MembersMixin,
    ApiKeysMixin,
    PipelineMixin,
    BudgetsMixin,
    RateLimitsMixin,
    AnomalyRulesMixin,
    viewsets.ModelViewSet[UseCase],
):
    serializer_class = UseCaseSerializer
    lookup_field = "slug"
    #: Only the list is paged; every other action addresses one use case by slug.
    pagination_class = ConsolePagination

    def _wants_may_call(self) -> bool:
        """Whether **the list** was asked for the gateway's answer (`?may_call=true`).

        Not *what may I see* (`scope_queryset`) but *what will the gateway accept from my token*
        (`aira_common.access.resolve`) — a wider set, since the `/use-cases/<slug>` convention
        (`FRD-102`) grants calling without a guardian permission. Bounded to the list: DRF resolves
        every detail route and `@action(detail=True)` through `get_queryset()`, so answering it
        there would disclose the panels of use cases the caller may not see.
        """
        if getattr(self, "action", None) != "list":
            return False
        return str(self.request.query_params.get("may_call", "")).lower() in ("1", "true", "yes")

    def get_queryset(self) -> QuerySet[UseCase]:
        if self._wants_may_call():
            # Against every live use case, not the visible ones: the gateway's answer does not
            # depend on Management visibility, and nothing is disclosed — these are exactly the use
            # cases this caller may already name in a request.
            scoped = may_call_queryset(self.request.user, self._live())
        else:
            scoped = scope_queryset(self.request.user, _VIEW, self._live())
        # Ordered, because paging an unordered queryset is undefined; searched here, so the rows a
        # reader is not looking at never have their per-row permissions computed.
        scoped = scoped.order_by("name", "slug")
        return apply_search(scoped, self.request, "name", "slug")

    def get_permissions(self) -> list[Any]:
        if self.action == "create":
            # A Global Administrator creates a use case and names the group administering it
            # (`ADR-0017`); administering one use case is no licence to create another.
            return [IsAuthenticated(), IsGlobalAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer: Any) -> None:
        with transaction.atomic():
            usecase = serializer.save()
            user: Any = self.request.user
            _grant(user, usecase, UseCaseMembership.ADMIN)
            UseCaseMembership.objects.create(
                use_case=usecase, user=user, role=UseCaseMembership.ADMIN
            )
            emit("usecase.upserted", _snapshot(usecase))
            # The creator's membership travels like any other, or the gateway refuses the person
            # this use case was just given to administer.
            emit(
                "membership.upserted",
                {
                    "slug": usecase.slug,
                    "username": user.get_username(),
                    "role": UseCaseMembership.ADMIN,
                },
            )

    def perform_update(self, serializer: Any) -> None:
        if not self._may_admin(serializer.instance):
            raise PermissionDenied("You are not an admin of this use case.")
        with transaction.atomic():
            usecase = serializer.save()
            emit("usecase.upserted", _snapshot(usecase))

    def _live(self) -> QuerySet[UseCase]:
        """Every use case that has not been retired — the one place that filter is written.

        A soft delete some queries honour and others do not shows a retired use case on exactly the
        screens nobody audited. Purged rows are gone from the table and need no filter.
        """
        return UseCase.objects.filter(deleted_at__isnull=True)
