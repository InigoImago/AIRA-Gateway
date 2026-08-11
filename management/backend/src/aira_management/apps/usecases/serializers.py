"""Serializers for use-cases (FRD-202)."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import Group
from django.db.models import Count
from rest_framework import serializers

from aira_management.apps.catalog.models import Model as CatalogModel
from aira_management.apps.usecases.access import is_member, may_admin, may_manage
from aira_management.apps.usecases.models import (
    UseCase,
    UseCaseGroupGrant,
    UseCaseMembership,
)
from aira_management.rbac import django_group_name

#: How many models one use case may be released. Generous — an installation contracts tens of
#: models, not hundreds — and finite, which is the point.
MAX_RELEASED_MODELS = 64


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

    #: The models released for this use case, by name (`FRD-308`).
    #:
    #: Names rather than ids, because a name is what a caller puts in a request, what the audit row
    #: carries and what the gateway enforces against. An id would be a fourth identifier for one
    #: model, meaningful only inside this database.
    allowed_models = serializers.SlugRelatedField(
        many=True,
        slug_field="name",
        required=False,
        queryset=CatalogModel.objects.all(),
    )

    def validate_allowed_models(self, models: list[CatalogModel]) -> list[CatalogModel]:
        """Only an **approved** model may be released to a use case, and not unboundedly many.

        Two gates, two owners: a Global Administrator decides what may be used in this installation
        at all (`FRD-307`), and a use-case administrator decides which of those this use case
        reaches. Letting the second hand out something the first has not released would invert
        them — and the request would be refused at dispatch anyway, so the console would show a
        release that never works.

        Refused **by name**, because "one of the models you chose is not approved" sends somebody
        back through a list of thirty to find out which.
        """
        if len(models) > MAX_RELEASED_MODELS:
            # The bound the `allow_check` step used to carry, kept when the step went. Every name
            # here is a database lookup, and an input nobody bounded is one somebody eventually
            # sends ten thousand of (`ADR-0007`).
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
            "prompt_caching_enabled",
            "prompt_cache_ttl",
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
