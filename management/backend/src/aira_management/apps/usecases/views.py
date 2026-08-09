"""Use-case CRUD + membership management (FRD-202).

Visibility and mutation follow the RBAC mechanics from FRD-201: list results are scoped
(governance sees all, others see use cases they may view); editing/deleting requires
change permission on the object (or global-admin); membership management requires the
``manage_members`` object permission (or global-admin). Object permissions are granted via
``django-guardian`` when a member is added.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from guardian.shortcuts import assign_perm, remove_perm
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_common.apikeys import generate_api_key
from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.anomalies.serializers import AnomalyRuleSerializer
from aira_management.apps.anomalies.views import upsert_use_case_rule
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.apikeys.serializers import ApiKeySerializer, IssueApiKeySerializer
from aira_management.apps.budgets.models import Budget
from aira_management.apps.budgets.serializers import BudgetSerializer
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.pipelines.serializers import PipelineConfigSerializer
from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.ratelimits.serializers import RateLimitSerializer
from aira_management.apps.usecases.access import (
    CHANGE as _CHANGE_PERM,
)
from aira_management.apps.usecases.access import (
    MANAGE as _MANAGE_PERM,
)
from aira_management.apps.usecases.access import (
    VIEW as _VIEW_PERM,
)
from aira_management.apps.usecases.access import (
    is_member,
    may_admin,
    may_manage,
    member_queryset,
)
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant, UseCaseMembership
from aira_management.apps.usecases.serializers import (
    AddMemberSerializer,
    GrantGroupSerializer,
    MembershipSerializer,
    UseCaseGroupGrantSerializer,
    UseCaseSerializer,
)
from aira_management.pagination import ConsolePagination, apply_search
from aira_management.rbac import IsUseCaseAdmin, django_group_name, scope_queryset

# One definition, in `access.py`, because the console asks the same questions to decide what to
# put on screen — see the module docstring there.
_VIEW = _VIEW_PERM
_CHANGE = _CHANGE_PERM
_MANAGE = _MANAGE_PERM


def _grant(holder: Any, usecase: UseCase, role: str) -> None:
    """Assign the object permissions for ``role``.

    ``holder`` is a user **or a Django group** — guardian takes either, and that is the whole
    mechanism behind group grants (`FRD-209` §2.2). Writing a second permission path for groups
    would be a second chance to forget one.
    """
    assign_perm(_VIEW, holder, usecase)
    if role == UseCaseMembership.ADMIN:
        assign_perm(_CHANGE, holder, usecase)
        assign_perm(_MANAGE, holder, usecase)


def _revoke(holder: Any, usecase: UseCase) -> None:
    for perm in (_VIEW, _CHANGE, _MANAGE):
        remove_perm(perm, holder, usecase)


def _snapshot(usecase: UseCase) -> dict[str, Any]:
    return {
        "slug": usecase.slug,
        "name": usecase.name,
        "description": usecase.description,
        "processing_notes": usecase.processing_notes,
        "store_payloads": usecase.store_payloads,
        "restrict_members_to_own_requests": usecase.restrict_members_to_own_requests,
        "tools_enabled": usecase.tools_enabled,
        "retention_days": usecase.retention_days,
    }


def _resolve_user(username: str) -> Any:
    user = get_user_model().objects.filter(username=username).first()
    if user is None:
        raise ValidationError({"username": [f"Unknown user '{username}'."]})
    return user


def _budget_payload(budget: Budget, slug: str) -> dict[str, Any]:
    return {
        "id": budget.pk,
        "use_case": slug,
        "scope": budget.scope,
        "subject": budget.subject,
        "period": budget.period,
        # Decimal as a string: JSON numbers are floats, and money must not round-trip through one.
        "limit_cost": str(budget.limit_cost) if budget.limit_cost is not None else None,
        "limit_tokens": budget.limit_tokens,
        "limit_requests": budget.limit_requests,
        "enabled": budget.enabled,
    }


def _rate_limit_payload(limit: RateLimit, slug: str) -> dict[str, Any]:
    return {
        "id": limit.pk,
        "use_case": slug,
        "scope": limit.scope,
        "subject": limit.subject,
        "limit_rpm": limit.limit_rpm,
        "burst": limit.burst,
        "enabled": limit.enabled,
    }


class UseCaseViewSet(viewsets.ModelViewSet[UseCase]):
    serializer_class = UseCaseSerializer
    lookup_field = "slug"
    #: Only the list is paged. Every other action here addresses one use case by slug, and a
    #: paginated single object is not a thing.
    pagination_class = ConsolePagination

    def get_queryset(self) -> QuerySet[UseCase]:
        scoped = scope_queryset(self.request.user, _VIEW, UseCase.objects.all())
        # Ordered explicitly: paging an unordered queryset is undefined, and Postgres is entitled
        # to hand back the same row on two pages and no row for a third. By name, because that is
        # what the list is read by.
        scoped = scoped.order_by("name", "slug")
        # `?mine=true` narrows to the use cases this caller may actually **act** in, which is a
        # different question from what they may see (`ADR-0007`). A screen that needs somewhere to
        # attribute traffic to needs this set and not the visible one — an oversight role sees
        # every use case and may call none of them, so offering the visible list there is a control
        # that fails the moment it is used.
        if str(self.request.query_params.get("mine", "")).lower() in ("1", "true", "yes"):
            scoped = member_queryset(self.request.user, scoped)
        # The search runs here, so the rows a reader is not looking at are never built. That is the
        # whole reason this moved off the browser: the serializer computes object-level permissions
        # per row (`access.py`), and client-side paging left every one of them happening.
        return apply_search(scoped, self.request, "name", "slug")

    def get_permissions(self) -> list[Any]:
        if self.action == "create":
            return [IsAuthenticated(), IsUseCaseAdmin()]
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

    def perform_update(self, serializer: Any) -> None:
        if not self._may_admin(serializer.instance):
            raise PermissionDenied("You are not an admin of this use case.")
        with transaction.atomic():
            usecase = serializer.save()
            emit("usecase.upserted", _snapshot(usecase))

    def perform_destroy(self, instance: UseCase) -> None:
        if not self._may_admin(instance):
            raise PermissionDenied("You are not an admin of this use case.")
        with transaction.atomic():
            slug = instance.slug
            instance.delete()
            emit("usecase.deleted", {"slug": slug})

    def _may_admin(self, usecase: UseCase) -> bool:
        return may_admin(self.request.user, usecase)

    def _may_manage(self, usecase: UseCase) -> bool:
        return may_manage(self.request.user, usecase)

    def _is_member(self, usecase: UseCase) -> bool:
        return is_member(self.request.user, usecase)

    @action(detail=True, methods=["get", "post"])
    def members(self, request: Request, slug: str | None = None) -> Response:
        usecase = self.get_object()
        if request.method == "GET":
            memberships = usecase.memberships.select_related("user").all()
            return Response(MembershipSerializer(memberships, many=True).data)

        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot manage members of this use case.")
        payload = AddMemberSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = _resolve_user(payload.validated_data["username"])
        role = payload.validated_data["role"]

        with transaction.atomic():
            membership, _created = UseCaseMembership.objects.update_or_create(
                use_case=usecase, user=user, defaults={"role": role}
            )
            _revoke(user, usecase)
            _grant(user, usecase, role)
            emit(
                "membership.upserted",
                {"slug": usecase.slug, "username": user.get_username(), "role": role},
            )
        return Response(MembershipSerializer(membership).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="groups")
    def group_grants(self, request: Request, slug: str | None = None) -> Response:
        """List or grant access to a **Keycloak group** (`FRD-209`).

        Reading is open to anybody who may see the use case: who can reach it is not a secret from
        its own members, and hiding it makes "why can that person call this" unanswerable without
        a database.
        """
        usecase = self.get_object()
        if request.method == "GET":
            grants = usecase.group_grants.all().order_by("group_path")
            return Response(UseCaseGroupGrantSerializer(grants, many=True).data)

        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot manage access to this use case.")
        payload = GrantGroupSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        path = payload.validated_data["group_path"]
        role = payload.validated_data["role"]

        with transaction.atomic():
            grant, _created = UseCaseGroupGrant.objects.update_or_create(
                use_case=usecase,
                group_path=path,
                defaults={"role": role, "granted_by": request.user.get_username()},
            )
            group, _made = Group.objects.get_or_create(name=django_group_name(path))
            # Revoked first, so lowering a grant from admin to user actually lowers it. An
            # `assign_perm` on top of the old set would leave the stronger permissions in place —
            # a demotion that demotes nothing is worse than none, because it reads as done.
            _revoke(group, usecase)
            _grant(group, usecase, role)
            emit("use_case_group.granted", {"slug": usecase.slug, "group": path, "role": role})
        return Response(UseCaseGroupGrantSerializer(grant).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path="groups/revoke")
    def revoke_group_grant(self, request: Request, slug: str | None = None) -> Response:
        """Revoke a group grant.

        The path arrives in the **query string**, not the URL path: a Keycloak group path contains
        slashes, and encoding it into a path segment produces a route that works until somebody
        has a group two levels deep.
        """
        usecase = self.get_object()
        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot manage access to this use case.")
        path = str(request.query_params.get("group_path", "")).strip()
        grant = UseCaseGroupGrant.objects.filter(use_case=usecase, group_path=path).first()
        if grant is None:
            raise ValidationError({"group_path": [f"'{path}' is not granted on this use case."]})

        with transaction.atomic():
            grant.delete()
            group = Group.objects.filter(name=django_group_name(path)).first()
            if group is not None:
                # Only the group's permissions. Somebody who also holds a direct grant keeps it —
                # revoking one route must not silently close another (`FRD-209` FR-5).
                _revoke(group, usecase)
            emit("use_case_group.revoked", {"slug": usecase.slug, "group": path})
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["delete"], url_path="members/(?P<username>[^/.]+)")
    def remove_member(
        self, request: Request, slug: str | None = None, username: str | None = None
    ) -> Response:
        usecase = self.get_object()
        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot manage members of this use case.")
        user = _resolve_user(username or "")
        with transaction.atomic():
            UseCaseMembership.objects.filter(use_case=usecase, user=user).delete()
            _revoke(user, usecase)
            emit("membership.removed", {"slug": usecase.slug, "username": username})
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"], url_path="api-keys")
    def api_keys(self, request: Request, slug: str | None = None) -> Response:
        """List keys, or issue a new one bound to this use case (FRD-205).

        A **member** of the use case may issue a key (bound to it, owned by them); the
        plaintext is returned **once** and only its hash is stored and distributed. Being able
        to *see* a use case is not enough — an API key is data-plane access, so the oversight
        roles, which see every use case, must not be able to mint one (ADR-0007).
        """
        usecase = self.get_object()
        if request.method == "GET":
            keys = ApiKey.objects.filter(use_case=usecase).select_related("owner")
            return Response(ApiKeySerializer(keys, many=True).data)

        if not self._is_member(usecase):
            raise PermissionDenied("Only members of this use case may issue API keys.")
        payload = IssueApiKeySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        label = payload.validated_data["label"]
        # Always a date: the serializer fills in the configured default when none was asked for,
        # and refuses anything past the configured maximum. There is no branch here for "never".
        days = payload.validated_data["expires_in_days"]
        expires_at = timezone.now() + timedelta(days=days)
        user: Any = request.user
        full, prefix, key_hash = generate_api_key()
        with transaction.atomic():
            ApiKey.objects.create(
                use_case=usecase,
                owner=user,
                prefix=prefix,
                key_hash=key_hash,
                label=label,
                expires_at=expires_at,
            )
            emit(
                "api_key.created",
                {
                    "prefix": prefix,
                    "key_hash": key_hash,
                    "subject": user.get_username(),
                    "use_case": usecase.slug,
                    "label": label,
                    "status": "active",
                    # The gateway enforces it; Management only decides it. Absent stays absent
                    # rather than becoming a far-future date, so "never" survives the wire.
                    "expires_at": expires_at.isoformat(),
                },
            )
        # The one and only time the plaintext leaves Management.
        return Response(
            {
                "api_key": full,
                "prefix": prefix,
                "label": label,
                "use_case": usecase.slug,
                "expires_at": expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["delete"], url_path="api-keys/(?P<prefix>[^/.]+)")
    def revoke_api_key(
        self, request: Request, slug: str | None = None, prefix: str | None = None
    ) -> Response:
        """Revoke a key by prefix (use-case admins only); publishes the revocation."""
        usecase = self.get_object()
        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot manage keys of this use case.")
        key = ApiKey.objects.filter(use_case=usecase, prefix=prefix, is_active=True).first()
        if key is None:
            raise ValidationError({"prefix": [f"No active key '{prefix}' for this use case."]})
        with transaction.atomic():
            key.is_active = False
            key.revoked_at = timezone.now()
            key.save(update_fields=["is_active", "revoked_at"])
            emit(
                "api_key.revoked", {"prefix": prefix, "use_case": usecase.slug, "status": "revoked"}
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "put"], url_path="pipeline")
    def pipeline(self, request: Request, slug: str | None = None) -> Response:
        """Read or replace the use case's pre-dispatch pipeline (FRD-300/303).

        Any member who can view the use case reads it; admins edit it. A saved config is
        published to the gateway via `pipeline.upserted`.
        """
        usecase = self.get_object()
        config = PipelineConfig.objects.filter(use_case=usecase).first()
        if request.method == "GET":
            if config is None:
                return Response({"steps": [], "fallback_models": []})
            return Response(PipelineConfigSerializer(config).data)

        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot edit the pipeline of this use case.")
        if config is None:
            config = PipelineConfig(use_case=usecase)
        serializer = PipelineConfigSerializer(config, data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            config = serializer.save()
            emit(
                "pipeline.upserted",
                {
                    "use_case": usecase.slug,
                    "steps": config.steps,
                    "fallback_models": config.fallback_models,
                },
            )
        return Response(PipelineConfigSerializer(config).data)

    @action(detail=True, methods=["get", "post"], url_path="budgets")
    def budgets(self, request: Request, slug: str | None = None) -> Response:
        """List or upsert usage budgets for this use case (FRD-400).

        Members read; admins define. POST upserts on (scope, subject, period) and publishes
        `budget.upserted` to the gateway.
        """
        usecase = self.get_object()
        if request.method == "GET":
            budgets = Budget.objects.filter(use_case=usecase)
            return Response(BudgetSerializer(budgets, many=True).data)

        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot edit budgets of this use case.")
        serializer = BudgetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            budget, _created = Budget.objects.update_or_create(
                use_case=usecase,
                scope=data["scope"],
                subject=data["subject"],
                period=data["period"],
                defaults={
                    "limit_cost": data.get("limit_cost"),
                    "limit_tokens": data.get("limit_tokens"),
                    "limit_requests": data.get("limit_requests"),
                    "enabled": data.get("enabled", True),
                },
            )
            emit("budget.upserted", _budget_payload(budget, usecase.slug))
        return Response(BudgetSerializer(budget).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="rate-limits")
    def rate_limits(self, request: Request, slug: str | None = None) -> Response:
        """List or upsert request-rate limits for this use case (FRD-405).

        Members read; admins define. POST upserts on (scope, subject) and publishes
        `ratelimit.upserted` to the gateway.
        """
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
                defaults={
                    "limit_rpm": data["limit_rpm"],
                    "burst": data.get("burst", 0),
                    "enabled": data.get("enabled", True),
                },
            )
            emit("ratelimit.upserted", _rate_limit_payload(limit, usecase.slug))
        return Response(RateLimitSerializer(limit).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="anomaly-rules")
    def anomaly_rules(self, request: Request, slug: str | None = None) -> Response:
        """List or upsert the anomaly rules of this use case (FRD-500).

        Members read; admins define. Global rules are **not** listed here — they are visible on
        `/api/v1/anomaly-rules/` and are not this use case's to change, and mixing them in would
        offer an edit that the server refuses.
        """
        usecase = self.get_object()
        if request.method == "GET":
            rules = AnomalyRule.objects.filter(use_case=usecase)
            return Response(AnomalyRuleSerializer(rules, many=True).data)
        # `request.data` is typed as "a dict or a list" because a DRF body can be either. A rule
        # is one object; a list here is a malformed request, and saying so beats a 500.
        if not isinstance(request.data, dict):
            raise ValidationError({"anomaly_rule": ["Send one rule, not a list."]})
        return upsert_use_case_rule(request.user, usecase, request.data)

    @action(detail=True, methods=["delete"], url_path="anomaly-rules/(?P<rule_id>[0-9]+)")
    def delete_anomaly_rule(
        self, request: Request, slug: str | None = None, rule_id: str | None = None
    ) -> Response:
        usecase = self.get_object()
        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot edit the anomaly rules of this use case.")
        assert rule_id is not None  # the URL route guarantees a numeric id
        rule = AnomalyRule.objects.filter(use_case=usecase, pk=int(rule_id)).first()
        if rule is None:
            raise ValidationError({"anomaly_rule": [f"No rule '{rule_id}' for this use case."]})
        with transaction.atomic():
            rule_pk = rule.pk
            rule.delete()
            emit("anomaly_rule.deleted", {"id": rule_pk, "use_case": usecase.slug})
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["delete"], url_path="rate-limits/(?P<limit_id>[0-9]+)")
    def delete_rate_limit(
        self, request: Request, slug: str | None = None, limit_id: str | None = None
    ) -> Response:
        usecase = self.get_object()
        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot edit rate limits of this use case.")
        assert limit_id is not None  # the URL route guarantees a numeric id
        limit = RateLimit.objects.filter(use_case=usecase, pk=int(limit_id)).first()
        if limit is None:
            raise ValidationError(
                {"rate_limit": [f"No rate limit '{limit_id}' for this use case."]}
            )
        with transaction.atomic():
            limit_pk = limit.pk
            limit.delete()
            emit("ratelimit.deleted", {"id": limit_pk, "use_case": usecase.slug})
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["delete"], url_path="budgets/(?P<budget_id>[0-9]+)")
    def delete_budget(
        self, request: Request, slug: str | None = None, budget_id: str | None = None
    ) -> Response:
        usecase = self.get_object()
        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot edit budgets of this use case.")
        assert budget_id is not None  # the URL route guarantees a numeric id
        budget = Budget.objects.filter(use_case=usecase, pk=int(budget_id)).first()
        if budget is None:
            raise ValidationError({"budget": [f"No budget '{budget_id}' for this use case."]})
        with transaction.atomic():
            budget_pk = budget.pk
            budget.delete()
            emit("budget.deleted", {"id": budget_pk, "use_case": usecase.slug})
        return Response(status=status.HTTP_204_NO_CONTENT)
