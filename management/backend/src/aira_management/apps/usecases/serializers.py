"""Serializers for use-cases (FRD-202)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_management.apps.usecases.access import is_member, may_admin, may_manage
from aira_management.apps.usecases.models import UseCase, UseCaseMembership


class UseCaseSerializer(serializers.ModelSerializer[UseCase]):
    #: What *this* caller may do here, answered by the same predicates that enforce it.
    #:
    #: Object-level permission is not derivable from a token: it lives in guardian rows, so the
    #: console cannot work it out from `/me` and had been guessing — showing "Add member" and
    #: "Remove" to a use-case user who then got a 403 from the screen that had just invited the
    #: click. An action nobody can carry out is worse than an absent one: it reads as a broken
    #: system rather than as a boundary.
    permissions = serializers.SerializerMethodField()

    def get_permissions(self, obj: UseCase) -> dict[str, bool]:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return {"can_admin": False, "can_manage": False, "is_member": False}
        return {
            "can_admin": may_admin(user, obj),
            "can_manage": may_manage(user, obj),
            "is_member": is_member(user, obj),
        }

    class Meta:
        model = UseCase
        fields = [
            "permissions",
            "slug",
            "name",
            "description",
            "processing_notes",
            "store_payloads",
            "retention_days",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["permissions", "created_at", "updated_at"]


class MembershipSerializer(serializers.ModelSerializer[UseCaseMembership]):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = UseCaseMembership
        fields = ["username", "role", "created_at"]


class AddMemberSerializer(serializers.Serializer[Any]):
    username = serializers.CharField()
    role = serializers.ChoiceField(
        choices=[UseCaseMembership.ADMIN, UseCaseMembership.USER],
        default=UseCaseMembership.USER,
    )
