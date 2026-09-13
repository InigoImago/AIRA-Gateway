"""Who can reach a use case: members named one by one, and grants to Keycloak groups (`FRD-209`).

Reading either list is open to anybody who may see the use case — who can reach it is no secret
from its own members. Changing it needs `manage_members`.
"""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCaseGroupGrant, UseCaseMembership
from aira_management.apps.usecases.serializers import (
    AddMemberSerializer,
    GrantGroupSerializer,
    MembershipSerializer,
    UseCaseGroupGrantSerializer,
)
from aira_management.apps.usecases.views.base import UseCaseViewBase
from aira_management.apps.usecases.views.grants import (
    _grant,
    _resolve_or_invite,
    _resolve_user,
    _revoke,
    _revoke_keys_without_access,
)
from aira_management.rbac import django_group_name


class MembersMixin(UseCaseViewBase):
    @action(detail=True, methods=["get", "post"])
    def members(self, request: Request, slug: str | None = None) -> Response:
        """List members, or add one — inviting somebody who has not signed in yet (FR-4)."""
        usecase = self.get_object()
        if request.method == "GET":
            memberships = usecase.memberships.select_related("user").all()
            return Response(MembershipSerializer(memberships, many=True).data)

        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot manage members of this use case.")
        payload = AddMemberSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = _resolve_or_invite(
            payload.validated_data["username"], invited_by=request.user.get_username()
        )
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

    @action(detail=True, methods=["delete"], url_path="members/(?P<username>[^/]+)")
    def remove_member(
        self, request: Request, slug: str | None = None, username: str | None = None
    ) -> Response:
        """Remove a member, and revoke every key that rested only on that access.

        `[^/]+` rather than the router's default `[^/.]+`, which cannot address a username
        containing a dot (`first.last`); percent-encoding does not help, because the path is
        decoded before it is matched.
        """
        usecase = self.get_object()
        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot manage members of this use case.")
        user = _resolve_user(username or "")
        with transaction.atomic():
            UseCaseMembership.objects.filter(use_case=usecase, user=user).delete()
            _revoke(user, usecase)
            # **The name as it is stored, not as it was typed**: the gateway keys
            # `use_case_members` on this string, and one character off keeps the membership there.
            emit("membership.removed", {"slug": usecase.slug, "username": user.get_username()})
            revoked = _revoke_keys_without_access(usecase)
        return Response({"revoked_keys": revoked}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="groups")
    def group_grants(self, request: Request, slug: str | None = None) -> Response:
        """List or grant access to a **Keycloak group**."""
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
            # Revoked first, so lowering a grant from admin to user actually lowers it.
            _revoke(group, usecase)
            _grant(group, usecase, role)
            emit("use_case_group.granted", {"slug": usecase.slug, "group": path, "role": role})
        return Response(UseCaseGroupGrantSerializer(grant).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path="groups/revoke")
    def revoke_group_grant(self, request: Request, slug: str | None = None) -> Response:
        """Revoke a group grant.

        The path arrives in the **query string**: a group path contains slashes, and one encoded
        into a path segment works until somebody has a group two levels deep.
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
                # Only the group's permissions: a direct grant is another route in and stays
                # (`FRD-209` FR-5).
                _revoke(group, usecase)
            emit("use_case_group.revoked", {"slug": usecase.slug, "group": path})
            revoked = _revoke_keys_without_access(usecase)
        return Response({"revoked_keys": revoked}, status=status.HTTP_200_OK)
