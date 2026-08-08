"""Serializers for use-cases (FRD-202)."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import Group
from django.db.models import Count
from rest_framework import serializers

from aira_management.apps.usecases.access import is_member, may_admin, may_manage
from aira_management.apps.usecases.models import (
    UseCase,
    UseCaseGroupGrant,
    UseCaseMembership,
)
from aira_management.rbac import django_group_name


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


class UseCaseGroupGrantSerializer(serializers.ModelSerializer[UseCaseGroupGrant]):
    """A grant to a Keycloak group, plus how many known people it currently reaches.

    `reaches` answers the question a grant to a group cannot answer on its own: a path that matches
    nobody is silently inert, and an access list that shows it identically to a working one is an
    access list nobody can audit (`FRD-209` FR-8). It counts people **Management has seen sign
    in** — not the group's true size, which only the identity provider knows — and the console says
    so rather than implying otherwise.
    """

    reaches = serializers.SerializerMethodField()

    class Meta:
        model = UseCaseGroupGrant
        fields = ["group_path", "role", "granted_by", "reaches", "created_at"]
        read_only_fields = ["granted_by", "reaches", "created_at"]

    def get_reaches(self, obj: UseCaseGroupGrant) -> int:
        return (
            Group.objects.filter(name=django_group_name(obj.group_path)).aggregate(n=Count("user"))[
                "n"
            ]
            or 0
        )


class GrantGroupSerializer(serializers.Serializer[Any]):
    #: A Keycloak group path. Validated for **shape**, not for existence: the identity provider
    #: may create the group tomorrow, and refusing a grant for a group that does not exist *yet*
    #: would make onboarding a department a two-step dance across two systems.
    #:
    #: The pattern requires at least one character after the slash, which rules out the bare `/`.
    #: A token's `groups` claim never contains it — every path Keycloak reports starts with a
    #: name — so a grant on `/` can only ever be inert, while reading to a person as "the whole
    #: realm". A grant that cannot match anything is exactly what this validation exists to catch.
    group_path = serializers.RegexField(r"^/[^\s/][^\s]{0,253}$", max_length=255)
    role = serializers.ChoiceField(
        choices=[UseCaseMembership.ADMIN, UseCaseMembership.USER],
        default=UseCaseMembership.USER,
    )


class AddMemberSerializer(serializers.Serializer[Any]):
    username = serializers.CharField()
    role = serializers.ChoiceField(
        choices=[UseCaseMembership.ADMIN, UseCaseMembership.USER],
        default=UseCaseMembership.USER,
    )
