"""The anomaly rules of one use case (`FRD-500`): members read, admins define.

Global rules are not listed here: they live on `/api/v1/anomaly-rules/` and are not this use
case's to change, so mixing them in would offer an edit the server refuses.
"""

from __future__ import annotations

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.anomalies.serializers import AnomalyRuleSerializer
from aira_management.apps.anomalies.views import upsert_use_case_rule
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.views.base import UseCaseViewBase


class AnomalyRulesMixin(UseCaseViewBase):
    @action(detail=True, methods=["get", "post"], url_path="anomaly-rules")
    def anomaly_rules(self, request: Request, slug: str | None = None) -> Response:
        """List the use case's rules, or upsert one."""
        usecase = self.get_object()
        if request.method == "GET":
            rules = AnomalyRule.objects.filter(use_case=usecase)
            return Response(AnomalyRuleSerializer(rules, many=True).data)
        # A DRF body may be a list; a rule is one object, and saying so beats a 500.
        if not isinstance(request.data, dict):
            raise ValidationError({"anomaly_rule": ["Send one rule, not a list."]})
        return upsert_use_case_rule(request.user, usecase, request.data)

    @action(detail=True, methods=["delete"], url_path="anomaly-rules/(?P<rule_id>[0-9]+)")
    def delete_anomaly_rule(
        self, request: Request, slug: str | None = None, rule_id: str | None = None
    ) -> Response:
        usecase, rule = self._child_to_delete(
            AnomalyRule,
            rule_id,
            key="anomaly_rule",
            noun="rule",
            refusal="You cannot edit the anomaly rules of this use case.",
        )
        with transaction.atomic():
            rule_pk = rule.pk
            rule.delete()
            emit("anomaly_rule.deleted", {"id": rule_pk, "use_case": usecase.slug})
        return Response(status=status.HTTP_204_NO_CONTENT)
