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
from rest_framework.request import Request
from rest_framework.response import Response

from aira_common.permissions import Permission
from aira_management.apps.usecases.access import VIEW as _VIEW
from aira_management.apps.usecases.access import may_call_queryset, prefetch_object_permissions
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.apps.usecases.serializers import UseCaseSerializer
from aira_management.apps.usecases.views.anomaly_rules import AnomalyRulesMixin
from aira_management.apps.usecases.views.api_keys import ApiKeysMixin
from aira_management.apps.usecases.views.budgets import BudgetsMixin
from aira_management.apps.usecases.views.grants import _admit
from aira_management.apps.usecases.views.members import MembersMixin
from aira_management.apps.usecases.views.payloads import _snapshot
from aira_management.apps.usecases.views.pipeline import PipelineMixin
from aira_management.apps.usecases.views.rate_limits import RateLimitsMixin
from aira_management.apps.usecases.views.retirement import RetirementMixin
from aira_management.pagination import ConsolePagination, apply_search
from aira_management.rbac import requires, scope_queryset


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
            scoped = may_call_queryset(self.request.user, UseCase.objects.live())
        else:
            scoped = scope_queryset(self.request.user, _VIEW, UseCase.objects.live())
        # Ordered, because paging an unordered queryset is undefined; searched here, so the rows a
        # reader is not looking at never have their per-row permissions computed. The releases
        # every row lists are read for the whole page at once.
        scoped = scoped.order_by("name", "slug").prefetch_related("allowed_models")
        return apply_search(scoped, self.request, "name", "slug")

    def paginate_queryset(self, queryset: Any) -> list[Any] | None:
        """The page, with the caller's object permissions on all of it read in one go — each row
        states what this caller may do there (`UseCaseSerializer.permissions`)."""
        page = super().paginate_queryset(queryset)
        if page is not None:
            prefetch_object_permissions(self.request.user, page)
        return page

    def get_permissions(self) -> list[Any]:
        if self.action == "create":
            # A Global Administrator creates a use case and names the group administering it
            # (`ADR-0017`); administering one use case is no licence to create another.
            return [IsAuthenticated(), requires(Permission.USECASE_CREATE)()]
        return [IsAuthenticated()]

    def perform_create(self, serializer: Any) -> None:
        with transaction.atomic():
            usecase = serializer.save()
            emit("usecase.upserted", _snapshot(usecase))
            # The creator's membership travels like any other, or the gateway refuses the person
            # this use case was just given to administer — after the use case it belongs to.
            _admit(usecase, self.request.user, UseCaseMembership.ADMIN)

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Asked who is asking **before** the body is read: somebody who may only read a use case
        is told so, not told to correct field values they may not set anyway. `partial_update`
        arrives here too."""
        if not self._may_admin(self.get_object()):
            raise PermissionDenied("You are not an admin of this use case.")
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer: Any) -> None:
        with transaction.atomic():
            usecase = serializer.save()
            emit("usecase.upserted", _snapshot(usecase))
