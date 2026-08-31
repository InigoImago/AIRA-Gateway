"""Serializers for use-cases (FRD-202)."""

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
            # **What the gateway will accept from this person's token**, which is a third question
            # and not a stricter phrasing of `is_member` (`access.may_call_queryset`). Reported
            # from the console: a use-case administrator and a global administrator both pressed
            # *Run dry-run* on the showcase use case and were refused, because its members were
            # Django rows and the gateway reads Keycloak groups. `is_member` said yes to both —
            # it grants a global administrator everything, and it counts direct rows.
            #
            # Offering an action the server will refuse is the `FRD-206` defect: it reads as a
            # broken system rather than as a boundary. The screen that spends tokens needs the
            # answer of the system that spends them.
            "may_call": may_call_queryset(user, UseCase.objects.filter(pk=obj.pk)).exists(),
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

    def validate_slug(self, slug: str) -> str:
        """The slug is the use case's **identity**, and an identity is set once (2026-08-27).

        It is not a display name. It keys the gateway's whole read-model — `use_cases`,
        `api_keys.use_case`, `pipeline_configs`, `budgets`, `rate_limits`, `use_case_members`,
        `use_case_groups`, every `request_logs` row — it is what an API key is bound to, and it is
        the second half of the Keycloak convention `/use-cases/<slug>` (`FRD-102`), which grants
        data-plane access **from the token alone**, touching no table this plane owns.

        Renaming it therefore does not rename anything. It **abandons** one use case and starts
        another, and only this plane learns which. Measured on 2026-08-27 against the running
        stack, one `PATCH` by a use-case administrator on their own use case:

            Management                    knows only the new slug (404 for the old)
            gateway `use_cases`           two rows — the old one intact, no tombstone
            gateway `api_keys`            still bound to the old slug, still active
            gateway `pipeline_configs`    still on the old slug, still enforcing
            the key issued before it      **still served, 200**

        So a use-case administrator can, with one field, move a fully provisioned use case out of
        the control plane's sight while it keeps serving traffic. Retirement cannot reach it —
        `FRD-607` writes the tombstone for the *new* slug, so `refuse_if_retired` never fires and
        the keys go on working, which is the one thing that feature exists to prevent. Nor can a
        key revocation, a budget, a limit, or a purge. The audit trail splits in two and reporting
        follows only half of it.

        Refused rather than silently ignored, which is what `read_only` would do: a caller who
        `PATCH`es a slug and is answered `200` with the old one believes they renamed it. *A value
        silently transformed is worse than one refused, because only the refusal is visible*
        (`FRD-124`).
        """
        if self.instance is not None and slug != self.instance.slug:
            raise serializers.ValidationError(
                f"A use case's slug is its identity and cannot be changed. '{self.instance.slug}' "
                "is what the gateway's read-model, its API keys, its audit rows and the Keycloak "
                "group '/use-cases/<slug>' all name. Retire this use case and create the one you "
                "want instead — the name and description are yours to edit."
            )
        return slug

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
    #: `\Z` rather than `$`: Python's `$` matches before a trailing newline, so `/ai/vertrieb\n`
    #: passed a validator that exists to stop a grant naming a path Keycloak never emits — which
    #: is silently inert, which is exactly what it is here to catch.
    group_path = serializers.RegexField(r"^/[^\s/][^\s]{0,253}\Z", max_length=255)
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


class RetiredUseCaseSerializer(serializers.ModelSerializer[UseCase]):
    """A tombstone, as the role deciding its fate needs to read it (`FRD-607`).

    Deliberately **not** `UseCaseSerializer`. That one computes object-level permissions per row
    and reports what this caller may do here — questions with no answer for a use case nobody may
    act on, and computing them would invite a screen to offer the actions. What a reader of this
    list needs is the opposite: what the use case *was*, so the traffic still in the audit trail
    can be read as evidence.
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
        """When the deliberate second decision becomes available, as a date rather than a rule.

        A reader should not have to hold `PURGE_AFTER_DAYS` in their head and do arithmetic on a
        timestamp to find out whether a button will work — that is how a screen ends up offering
        an action that fails.
        """
        if obj.deleted_at is None:
            return None
        return (obj.deleted_at + timedelta(days=PURGE_AFTER_DAYS)).isoformat()
