"""Anomaly-rule API (FRD-500).

Two surfaces on one model, because there are two genuinely different questions.

A rule scoped to a use case is that use case's business, and its administrator authors it. A
**global** rule — "any credential used from an address it has never been used from" — crosses
every boundary the console otherwise enforces, and its effects land on use cases its author may
not be able to see. That is IT Security's job description (PRD §154), so it is IT Security's to
author, and the *API* says so rather than the UI (`FRD-206`: the console asks, the server decides).
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import Q, QuerySet
from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.anomalies.serializers import AnomalyRuleSerializer
from aira_management.apps.usecases.access import VIEW, may_manage
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import has_role, scope_queryset
from aira_management.roles import Role


def rule_payload(rule: AnomalyRule) -> dict[str, Any]:
    """What travels to the gateway.

    Everything the engine needs, because the gateway never calls Management on the request path
    (`FRD-500` FR-7, the same rule as `FRD-114` FR-8). A `use_case` of ``None`` is the wire form
    of "everywhere" — deliberately not an empty string, which would be a use case named "".
    """
    return {
        "id": rule.pk,
        "use_case": rule.use_case.slug if rule.use_case is not None else None,
        "name": rule.name,
        "kind": rule.kind,
        "window_minutes": rule.window_minutes,
        "threshold": rule.threshold,
        "min_sample": rule.min_sample,
        "action": rule.action,
        "target": rule.target,
        "action_minutes": rule.action_minutes,
        "enabled": rule.enabled,
    }


def may_author_global(user: Any) -> bool:
    """Who may write a rule that acts everywhere."""
    return has_role(user, Role.GLOBAL_ADMIN, Role.IT_SECURITY)


class AnomalyRuleViewSet(viewsets.ModelViewSet[AnomalyRule]):
    """Global rules, plus a read across everything the caller can see."""

    serializer_class = AnomalyRuleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self) -> QuerySet[AnomalyRule]:
        # Global rules are visible to everybody: a rule that can block your traffic is a rule you
        # are entitled to know about, whoever wrote it.
        visible = scope_queryset(self.request.user, VIEW, UseCase.objects.all())
        return AnomalyRule.objects.filter(
            Q(use_case__isnull=True) | Q(use_case__in=visible)
        ).select_related("use_case")

    def perform_create(self, serializer: Any) -> None:
        if not may_author_global(self.request.user):
            raise PermissionDenied(
                "Only IT Security or a Global Administrator may author a global rule. "
                "A rule for one use case is created on that use case."
            )
        with transaction.atomic():
            rule = serializer.save(use_case=None)
            emit("anomaly_rule.upserted", rule_payload(rule))

    def perform_update(self, serializer: Any) -> None:
        self._guard(serializer.instance)
        with transaction.atomic():
            rule = serializer.save()
            emit("anomaly_rule.upserted", rule_payload(rule))

    def perform_destroy(self, instance: AnomalyRule) -> None:
        self._guard(instance)
        with transaction.atomic():
            rule_id = instance.pk
            instance.delete()
            emit("anomaly_rule.deleted", {"id": rule_id})

    def _guard(self, rule: AnomalyRule) -> None:
        """Editing follows the scope, not the endpoint: a use-case rule reachable through this
        list is still that use case's to change."""
        if rule.use_case is None:
            if not may_author_global(self.request.user):
                raise PermissionDenied(
                    "Only IT Security or a Global Administrator may change a global rule."
                )
        elif not may_manage(self.request.user, rule.use_case):
            raise PermissionDenied("You cannot change the anomaly rules of this use case.")


def upsert_use_case_rule(user: Any, usecase: UseCase, data: dict[str, Any]) -> Response:
    """Create or replace a rule on one use case. Shared by the use-case viewset's action."""
    if not may_manage(user, usecase):
        raise PermissionDenied("You cannot edit the anomaly rules of this use case.")
    serializer = AnomalyRuleSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    values = dict(serializer.validated_data)
    name = values.pop("name")
    with transaction.atomic():
        rule, _created = AnomalyRule.objects.update_or_create(
            use_case=usecase, name=name, defaults=values
        )
        emit("anomaly_rule.upserted", rule_payload(rule))
    return Response(AnomalyRuleSerializer(rule).data, status=status.HTTP_201_CREATED)
