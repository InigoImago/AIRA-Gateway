"""Request-rate limits of one use case (`FRD-405`): members read, admins define."""

from __future__ import annotations

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.ratelimits.serializers import RateLimitSerializer
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.views.base import UseCaseViewBase
from aira_management.apps.usecases.views.payloads import _rate_limit_payload


class RateLimitsMixin(UseCaseViewBase):
    @action(detail=True, methods=["get", "post"], url_path="rate-limits")
    def rate_limits(self, request: Request, slug: str | None = None) -> Response:
        """List limits, or upsert one on (scope, subject) and publish `ratelimit.upserted`."""
        usecase = self.get_object()
        if request.method == "GET":
            limits = RateLimit.objects.filter(use_case=usecase)
            return Response(RateLimitSerializer(limits, many=True).data)

        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot edit rate limits of this use case.")
        serializer = RateLimitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            limit, _created = RateLimit.objects.update_or_create(
                use_case=usecase,
                scope=data["scope"],
                subject=data["subject"],
                # As for budgets: an upsert that says nothing about `enabled` leaves it alone.
                defaults={
                    "limit_rpm": data["limit_rpm"],
                    "burst": data.get("burst", 0),
                    **({"enabled": data["enabled"]} if "enabled" in data else {}),
                },
            )
            emit("ratelimit.upserted", _rate_limit_payload(limit, usecase.slug))
        return Response(RateLimitSerializer(limit).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path="rate-limits/(?P<limit_id>[0-9]+)")
    def delete_rate_limit(
        self, request: Request, slug: str | None = None, limit_id: str | None = None
    ) -> Response:
        usecase, limit = self._child_to_delete(
            RateLimit,
            limit_id,
            key="rate_limit",
            noun="rate limit",
            refusal="You cannot edit rate limits of this use case.",
        )
        with transaction.atomic():
            limit_pk = limit.pk
            limit.delete()
            emit("ratelimit.deleted", {"id": limit_pk, "use_case": usecase.slug})
        return Response(status=status.HTTP_204_NO_CONTENT)
