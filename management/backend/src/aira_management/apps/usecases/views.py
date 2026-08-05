"""Use-case CRUD + membership management (FRD-202).

Visibility and mutation follow the RBAC mechanics from FRD-201: list results are scoped
(governance sees all, others see use cases they may view); editing/deleting requires
change permission on the object (or global-admin); membership management requires the
``manage_members`` object permission (or global-admin). Object permissions are granted via
``django-guardian`` when a member is added.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
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
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.apikeys.serializers import ApiKeySerializer, IssueApiKeySerializer
from aira_management.apps.budgets.models import Budget
from aira_management.apps.budgets.serializers import BudgetSerializer
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.pipelines.serializers import PipelineConfigSerializer
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.apps.usecases.serializers import (
    AddMemberSerializer,
    MembershipSerializer,
    UseCaseSerializer,
)
from aira_management.rbac import IsUseCaseAdmin, has_role, scope_queryset
from aira_management.roles import Role

_VIEW = "usecases.view_usecase"
_CHANGE = "usecases.change_usecase"
_MANAGE = "usecases.manage_members"


def _grant(user: Any, usecase: UseCase, role: str) -> None:
    assign_perm(_VIEW, user, usecase)
    if role == UseCaseMembership.ADMIN:
        assign_perm(_CHANGE, user, usecase)
        assign_perm(_MANAGE, user, usecase)


def _revoke(user: Any, usecase: UseCase) -> None:
    for perm in (_VIEW, _CHANGE, _MANAGE):
        remove_perm(perm, user, usecase)


def _snapshot(usecase: UseCase) -> dict[str, Any]:
    return {
        "slug": usecase.slug,
        "name": usecase.name,
        "description": usecase.description,
        "processing_notes": usecase.processing_notes,
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
        "limit_tokens": budget.limit_tokens,
        "limit_requests": budget.limit_requests,
        "enabled": budget.enabled,
    }


class UseCaseViewSet(viewsets.ModelViewSet[UseCase]):
    serializer_class = UseCaseSerializer
    lookup_field = "slug"

    def get_queryset(self) -> QuerySet[UseCase]:
        return scope_queryset(self.request.user, _VIEW, UseCase.objects.all())

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
        user = self.request.user
        return has_role(user, Role.GLOBAL_ADMIN) or user.has_perm(_CHANGE, usecase)

    def _may_manage(self, usecase: UseCase) -> bool:
        user = self.request.user
        return has_role(user, Role.GLOBAL_ADMIN) or user.has_perm(_MANAGE, usecase)

    def _is_member(self, usecase: UseCase) -> bool:
        """True if the caller is an actual member of the use case (or a global admin).

        Deliberately *not* the same as "may see it": the governance roles (global-admin,
        it-steuerung) get organisation-wide read visibility through ``scope_queryset``, and
        read visibility must never imply the right to act inside a use case (ADR-0007).
        """
        user: Any = self.request.user
        if has_role(user, Role.GLOBAL_ADMIN):
            return True
        return UseCaseMembership.objects.filter(use_case=usecase, user=user).exists()

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
        user: Any = request.user
        full, prefix, key_hash = generate_api_key()
        with transaction.atomic():
            ApiKey.objects.create(
                use_case=usecase, owner=user, prefix=prefix, key_hash=key_hash, label=label
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
                },
            )
        # The one and only time the plaintext leaves Management.
        return Response(
            {"api_key": full, "prefix": prefix, "label": label, "use_case": usecase.slug},
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
                    "limit_tokens": data.get("limit_tokens"),
                    "limit_requests": data.get("limit_requests"),
                    "enabled": data.get("enabled", True),
                },
            )
            emit("budget.upserted", _budget_payload(budget, usecase.slug))
        return Response(BudgetSerializer(budget).data, status=status.HTTP_201_CREATED)

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
