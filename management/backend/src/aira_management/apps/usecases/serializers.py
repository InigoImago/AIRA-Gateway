"""Serializers for use cases (`FRD-202`)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.contrib.auth.models import Group
from django.db.models import Count
from rest_framework import serializers

from aira_management.apps.catalog.models import Model as CatalogModel
from aira_management.apps.usecases.access import (
    is_member,
    may_admin,
    may_call_queryset,
    may_manage,
)
from aira_management.apps.usecases.models import (
    PURGE_AFTER_DAYS,
    UseCase,
    UseCaseGroupGrant,
    UseCaseMembership,
)
from aira_management.rbac import django_group_name

#: How many models one use case may be released: generous, and finite (`ADR-0007`).
MAX_RELEASED_MODELS = 64

#: The two roles a grant carries, for people and groups alike.
_GRANT_ROLES = [UseCaseMembership.ADMIN, UseCaseMembership.USER]


class UseCaseSerializer(serializers.ModelSerializer[UseCase]):
    #: What *this* caller may do here, answered by the predicates that enforce it (`FRD-206`).
    #: Object permissions live in guardian rows, so the console cannot derive them from `/me`.
    permissions = serializers.SerializerMethodField()

    #: The models released for this use case, by name (`FRD-308`) — the name is what a caller
    #: sends, what the audit row carries and what the gateway enforces against.
    allowed_models = serializers.SlugRelatedField(
        many=True,
        slug_field="name",
        required=False,
        queryset=CatalogModel.objects.all(),
    )

    class Meta:
        model = UseCase
        fields = [
            "permissions",
            "allowed_models",
            "slug",
            "name",
            "description",
            "processing_notes",
            "store_payloads",
            "restrict_members_to_own_requests",
            "tools_enabled",
            "include_reasoning",
            "prompt_caching_enabled",
            "prompt_cache_ttl",
            "retention_days",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["permissions", "created_at", "updated_at"]

    def get_permissions(self, obj: UseCase) -> dict[str, bool]:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return {
                "can_admin": False,
                "can_manage": False,
                "is_member": False,
                "may_call": False,
            }
        return {
            "can_admin": may_admin(user, obj),
            "can_manage": may_manage(user, obj),
            "is_member": is_member(user, obj),
            # What the gateway will accept from this person's token — a third question, not a
            # stricter `is_member` (see `access.may_call_queryset`). The screen that spends tokens
            # needs the answer of the system that spends them.
            "may_call": may_call_queryset(user, UseCase.objects.filter(pk=obj.pk)).exists(),
        }

    def validate_allowed_models(self, models: list[CatalogModel]) -> list[CatalogModel]:
        """Only an **approved** model may be released to a use case, and not unboundedly many.

        Two gates, two owners: a Global Administrator decides what this installation may use at all
        (`FRD-307`), a use-case administrator which of those this use case reaches. Unapproved
        models are refused by name.
        """
        if len(models) > MAX_RELEASED_MODELS:
            raise serializers.ValidationError(
                f"A use case can be released at most {MAX_RELEASED_MODELS} models."
            )
        unapproved = sorted(model.name for model in models if not model.approved)
        if unapproved:
            raise serializers.ValidationError(
                f"Not approved for use in this installation: {', '.join(unapproved)}. A Global "
                "Administrator releases a model into the catalog before a use case can be given it."
            )
        return models

    def validate_slug(self, slug: str) -> str:
        """The slug is the use case's **identity**, set once.

        It keys the gateway's whole read-model and audit trail, binds every API key, and completes
        the Keycloak convention `/use-cases/<slug>` (`FRD-102`). Renaming it would not rename
        anything: it would move a provisioned use case out of the control plane's sight while its
        keys keep serving, beyond retirement, revocation or purge. Refused rather than made
        `read_only`, because a `200` carrying the old slug would read as a rename (`FRD-124`).
        """
        if self.instance is not None and slug != self.instance.slug:
            raise serializers.ValidationError(
                f"A use case's slug is its identity and cannot be changed. '{self.instance.slug}' "
                "is what the gateway's read-model, its API keys, its audit rows and the Keycloak "
                "group '/use-cases/<slug>' all name. Retire this use case and create the one you "
                "want instead — the name and description are yours to edit."
            )
        return slug


class MembershipSerializer(serializers.ModelSerializer[UseCaseMembership]):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = UseCaseMembership
        fields = ["username", "role", "created_at"]


class UseCaseGroupGrantSerializer(serializers.ModelSerializer[UseCaseGroupGrant]):
    """A grant to a Keycloak group, plus how many known people it currently reaches.

    `reaches` makes a grant that matches nobody visible (`FRD-209` FR-8). It counts people
    Management has seen sign in, not the group's true size, which only the identity provider knows.
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
    #: A Keycloak group path, validated for **shape**, not existence: the identity provider may
    #: create the group tomorrow. At least one character after the slash, because a token never
    #: carries a bare `/` and a grant on it would be inert; `\Z` rather than `$`, which also
    #: matches before a trailing newline.
    group_path = serializers.RegexField(r"^/[^\s/][^\s]{0,253}\Z", max_length=255)
    role = serializers.ChoiceField(choices=_GRANT_ROLES, default=UseCaseMembership.USER)


class AddMemberSerializer(serializers.Serializer[Any]):
    username = serializers.CharField()
    role = serializers.ChoiceField(choices=_GRANT_ROLES, default=UseCaseMembership.USER)


class RetiredUseCaseSerializer(serializers.ModelSerializer[UseCase]):
    """A tombstone, as the role deciding its fate reads it (`FRD-607`).

    Not `UseCaseSerializer`: per-row permissions have no answer for a use case nobody may act on,
    and computing them would invite a screen to offer actions. A reader needs what it *was*.
    """

    purgeable_on = serializers.SerializerMethodField()

    class Meta:
        model = UseCase
        fields = [
            "slug",
            "name",
            "description",
            "processing_notes",
            "store_payloads",
            "retention_days",
            "created_at",
            "deleted_at",
            "deleted_by",
            "purgeable_on",
        ]
        read_only_fields = fields

    def get_purgeable_on(self, obj: UseCase) -> str | None:
        """When a purge becomes possible, as a date, so no screen offers one that would fail."""
        if obj.deleted_at is None:
            return None
        return (obj.deleted_at + timedelta(days=PURGE_AFTER_DAYS)).isoformat()
