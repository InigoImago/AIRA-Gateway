"""Retiring and purging a use case (`FRD-607`).

The threat: somebody misuses a use case, compromises it, and deletes it. Its administrator is best
placed to do that, so deleting only **retires** — access ends at once, the row stays — and removing
the record is a separate, later decision by a different role. The party who might want the record
gone is not the party who can remove it.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from aira_common.permissions import Permission
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import PURGE_AFTER_DAYS, UseCase
from aira_management.apps.usecases.serializers import RetiredUseCaseSerializer
from aira_management.apps.usecases.views.base import UseCaseViewBase
from aira_management.rbac import may


class RetirementMixin(UseCaseViewBase):
    def perform_destroy(self, instance: UseCase) -> None:
        """**Retire, never remove**: tombstone the row and emit `usecase.deleted`.

        The gateway reacts as to a deletion — keys, memberships, grants, budgets, limits, rules and
        pipeline go and traffic stops — but the row stays: the gateway's audit rows name the use
        case only by slug, and this row is the context that makes them evidence.
        """
        if not self._may_admin(instance):
            raise PermissionDenied("You are not an admin of this use case.")
        if instance.deleted_at is not None:
            # Already retired: no second event, which would re-run the gateway's cascade and
            # overwrite who retired it.
            return
        with transaction.atomic():
            instance.deleted_at = timezone.now()
            instance.deleted_by = str(getattr(self.request.user, "username", "") or "")
            instance.save(update_fields=["deleted_at", "deleted_by", "updated_at"])
            emit("usecase.deleted", {"slug": instance.slug})

    @action(detail=False, methods=["get"], url_path="retired")
    def retired(self, request: Request) -> Response:
        """The tombstones, for governance — a route of their own because they are records now.

        Not for the use-case administrator, including the one who retired it: the design assumes
        that person may be the reason the record matters.
        """
        if not may(request.user, Permission.USECASE_READ_RETIRED):
            raise PermissionDenied(
                "Retired use cases need the permission to see them (usecase.read_retired)."
            )
        rows = UseCase.objects.filter(deleted_at__isnull=False).order_by("-deleted_at", "slug")
        return Response(RetiredUseCaseSerializer(rows, many=True).data)

    @action(detail=True, methods=["delete"], url_path="purge")
    def purge(self, request: Request, slug: str | None = None) -> Response:
        """Remove a retired use case for good. Three conditions, each a separate defence:

        1. `usecase.purge` — a Global Administrator by default, not the administrator who retired
           it, and not `IT Steuerung`, which oversees and does not act;
        2. it must **already be retired**, or purging would rebuild the hole in one step;
        3. it must have been retired for **`PURGE_AFTER_DAYS`**, so erasing a record means waiting
           while its tombstone is visible in the retired list.
        """
        if not may(request.user, Permission.USECASE_PURGE):
            raise PermissionDenied(
                "Purging a retired use case needs the permission to purge (usecase.purge)."
            )
        # Read around `get_queryset()`, which excludes retired rows, and without a retired filter,
        # so the one check below enforces condition 2 — a second copy would leave neither
        # load-bearing.
        usecase = UseCase.objects.filter(slug=slug).first()
        if usecase is None or usecase.deleted_at is None:
            # Live and unknown answer alike, so this does not reveal whether a slug exists.
            raise Http404("No retired use case with this id.")

        waited = timezone.now() - usecase.deleted_at
        if waited < timedelta(days=PURGE_AFTER_DAYS):
            remaining = timedelta(days=PURGE_AFTER_DAYS) - waited
            raise ValidationError(
                {
                    "detail": [
                        f"This use case was retired {waited.days} day(s) ago and may be purged "
                        f"after {PURGE_AFTER_DAYS}. Try again in "
                        f"{max(1, -(-remaining.total_seconds() // 86400)):.0f} day(s)."
                    ]
                }
            )

        with transaction.atomic():
            purged = usecase.slug
            usecase.delete()
            # A second event, not a repeat of `usecase.deleted`: that one ended access and kept the
            # tombstone; this one lets the gateway drop the last row it kept for the use case.
            emit("usecase.purged", {"slug": purged})
        return Response(status=status.HTTP_204_NO_CONTENT)
