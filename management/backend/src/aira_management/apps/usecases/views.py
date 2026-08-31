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
from django.http import Http404
from django.utils import timezone
from guardian.shortcuts import assign_perm, remove_perm
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_common.apikeys import generate_api_key
from aira_common.directory import DirectoryUnavailable
from aira_common.roles import Role
from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.anomalies.serializers import AnomalyRuleSerializer
from aira_management.apps.anomalies.views import upsert_use_case_rule
from aira_management.apps.api.models import PendingIdentity
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.apikeys.serializers import ApiKeySerializer, IssueApiKeySerializer
from aira_management.apps.budgets.models import Budget
from aira_management.apps.budgets.serializers import BudgetSerializer
from aira_management.apps.directory.service import known_person
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
    holds_a_grant,
    is_member,
    may_admin,
    may_call_queryset,
    may_manage,
)
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import (
    PURGE_AFTER_DAYS,
    UseCase,
    UseCaseGroupGrant,
    UseCaseMembership,
)
from aira_management.apps.usecases.serializers import (
    AddMemberSerializer,
    GrantGroupSerializer,
    MembershipSerializer,
    RetiredUseCaseSerializer,
    UseCaseGroupGrantSerializer,
    UseCaseSerializer,
)
from aira_management.pagination import ConsolePagination, apply_search
from aira_management.rbac import (
    IsGlobalAdmin,
    django_group_name,
    has_governance_role,
    has_role,
    scope_queryset,
)

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
        "include_reasoning": usecase.include_reasoning,
        "prompt_caching_enabled": usecase.prompt_caching_enabled,
        "prompt_cache_ttl": usecase.prompt_cache_ttl,
        "retention_days": usecase.retention_days,
        # `FRD-308`. Sorted so the event is stable: a compacted topic keyed by slug replays the
        # last value, and a payload that differs only in ordering makes every diff of the log a
        # false positive.
        "allowed_models": sorted(usecase.allowed_models.values_list("name", flat=True)),
    }


def _resolve_user(username: str) -> Any:
    """The local account for ``username``, which must already exist.

    Used where a *removal* names somebody: taking access away from a name nobody has is a no-op
    dressed as an action, and the caller is better told the name is wrong.
    """
    user = get_user_model().objects.filter(username=username).first()
    if user is None:
        raise ValidationError({"username": [f"Unknown user '{username}'."]})
    return user


def _resolve_or_invite(username: str, *, invited_by: str) -> Any:
    """The local account for ``username``, **creating an invited one** if there is none yet.

    This is `FRD-209` FR-4 finally holding on the half it never did. The picker offers everybody
    the *directory* knows and this resolved only against people who had already signed in, so
    granting access to a new colleague answered `Unknown user 'x'` — a control the console offers
    and the server refuses, which is `FRD-206`'s defect on the one route the person-grant half of
    `FRD-209` exists for. Measured on 2026-08-30 against a freshly seeded stack.

    An account is created **only for a name the directory confirms**, and it carries an invitation
    rather than a binding (`apps.api.models.PendingIdentity`): there is no `sub` to bind to until
    they arrive. That confirmation is the whole of the guard `_resolve_owner` already states in
    words — *"a credential attached to a username nobody has is an accountability chain ending in
    a string"* — and it applies to a membership for the same reason.

    Three outcomes, kept apart because they send three different people to fix them: the directory
    has no such person (the typist's), no directory is configured (the operator's), and the
    directory could not be reached (nobody's, yet).
    """
    user = get_user_model().objects.filter(username=username).first()
    if user is not None:
        return user
    try:
        found = known_person(username)
    except DirectoryUnavailable:
        raise ValidationError(
            {
                "username": [
                    f"There is no user '{username}' here yet, and the directory could not be "
                    "asked whether there is one. Either configure the directory client "
                    "(AIRA_DIRECTORY_CLIENT_ID / _SECRET) or have them sign in to the console "
                    "once, which creates the account."
                ]
            }
        ) from None
    if found is None:
        raise ValidationError({"username": [f"The directory knows no user '{username}'."]})
    with transaction.atomic():
        created = get_user_model().objects.create(username=found.id, email=found.detail[:254])
        PendingIdentity.objects.create(user=created, invited_by=invited_by[:150])
    return created


def _revoke_keys_without_access(usecase: UseCase) -> list[str]:
    """Revoke every active key of ``usecase`` whose owner no longer holds a grant on it.

    **Access ending has to end the credential that rested on it.** Removing somebody from a use
    case took away their console view, their guardian permissions and the membership row the
    gateway reads — and left every API key they held for that use case active, bound to that use
    case, and serving traffic against its budget until it happened to expire (up to
    `AIRA_API_KEY_MAX_DAYS`, 180 days by default). Measured on 2026-08-30: a removed member's key
    answered `200` on the surface they had just been removed from.

    That is the offboarding hole in its plainest form. Every other consequence of the removal was
    immediate and complete; the one that actually reaches a model was not, and the screen said
    nothing, so whoever removed them believed access had ended.

    Asked as *"does this owner still hold a grant"* rather than *"was this the person removed"*,
    because the same sentence has to be true after a **group** grant is revoked — where nobody was
    named at all and a key's owner may have been reaching the use case only through that group.
    Somebody who also holds a direct membership keeps their keys, which is `FRD-209` FR-5 one
    layer down: revoking one route must not silently close another.

    Deliberately **not** a deletion. Revocation is terminal and dated on both planes (`ADR-0007`),
    and a key that stops working is a fact an investigation asks about later.
    """
    revoked: list[str] = []
    keys = ApiKey.objects.filter(use_case=usecase, is_active=True).select_related("owner")
    for key in keys:
        if holds_a_grant(key.owner, usecase):
            continue
        key.is_active = False
        key.revoked_at = timezone.now()
        key.save(update_fields=["is_active", "revoked_at"])
        emit(
            "api_key.revoked",
            {"prefix": key.prefix, "use_case": usecase.slug, "status": "revoked"},
        )
        revoked.append(key.prefix)
    return revoked


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

    def _wants_may_call(self) -> bool:
        """Whether **the list** was asked for the gateway's answer rather than Management's.

        `?may_call=true` swaps one question for another: not *what may I see* (a guardian object
        permission, `scope_queryset`) but *what will the gateway accept from my token*
        (`aira_common.access.resolve` over the token's groups). The `/use-cases/<slug>` convention
        (`FRD-102`) answers the second without granting the first, so the two sets genuinely differ
        — which is the whole point of offering it, and the reason it must never decide **which
        object a detail route resolves**.

        It did. `get_queryset` answered the parameter unconditionally, and DRF resolves every
        detail route and every `@action(detail=True)` through `get_object()` → `get_queryset()`.
        Measured on 2026-08-15 with a caller holding nothing but the Keycloak group
        `/use-cases/secret-uc`:

            GET /use-cases/secret-uc/                    404
            GET /use-cases/secret-uc/?may_call=true      200   + the whole object
            GET /use-cases/secret-uc/members/…           200   the member list
            GET /use-cases/secret-uc/budgets/…           200   the budgets
            GET /use-cases/secret-uc/pipeline/…          200   the pipeline configuration
            GET /use-cases/secret-uc/api-keys/…          200   the key metadata

        The mutations were never reachable — they ask `_may_manage`/`_is_member` independently of
        the queryset — so it was disclosure rather than escalation. Bounded to the list action,
        which is the only one that ever meant it.
        """
        if getattr(self, "action", None) != "list":
            return False
        return str(self.request.query_params.get("may_call", "")).lower() in ("1", "true", "yes")

    def get_queryset(self) -> QuerySet[UseCase]:
        if self._wants_may_call():
            # Resolved against **every** use case, not against the visible ones. The gateway's
            # answer does not depend on Management visibility, and the `/use-cases/<slug>`
            # convention grants calling without granting a guardian object permission — so
            # filtering the visible set here would hand somebody an empty attribution list while
            # the gateway happily accepted their requests. Nothing is disclosed by it: these are
            # exactly the use cases this caller may already name in a request.
            scoped = may_call_queryset(self.request.user, self._live())
        else:
            scoped = scope_queryset(self.request.user, _VIEW, self._live())
        # Ordered explicitly: paging an unordered queryset is undefined, and Postgres is entitled
        # to hand back the same row on two pages and no row for a third. By name, because that is
        # what the list is read by.
        scoped = scoped.order_by("name", "slug")
        # The search runs here, so the rows a reader is not looking at are never built. That is the
        # whole reason this moved off the browser: the serializer computes object-level permissions
        # per row (`access.py`), and client-side paging left every one of them happening.
        return apply_search(scoped, self.request, "name", "slug")

    def get_permissions(self) -> list[Any]:
        if self.action == "create":
            # **A Global Administrator creates a use case** (`ADR-0017`), and names the group that
            # administers it. This was `global-admin or use-case-admin`, and it is a deliberate
            # narrowing: `use-case-admin` was an organisation-wide realm role, so it let anybody
            # who administered one use case create another — which is not what administering a use
            # case means.
            return [IsAuthenticated(), IsGlobalAdmin()]
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

    def _live(self) -> QuerySet[UseCase]:
        """Every use case that has not been retired.

        **One place, deliberately.** A soft delete that some queries honour and others do not is
        worse than none: it makes a retired use case appear on the screens nobody audited and
        vanish from the ones they did, which is a harder bug to see than a hard delete.
        Purged rows are gone from the table entirely, so they need no filter.
        """
        return UseCase.objects.filter(deleted_at__isnull=True)

    def perform_destroy(self, instance: UseCase) -> None:
        """**Retire, never remove** (`FRD-607`).

        The threat this answers, stated by the owner: *"somebody uses a use case for the wrong
        purposes, compromises it, and deletes the use case."* The person best placed to do that is
        its administrator, and that is exactly who reaches this method — so this method must not be
        able to destroy anything.

        What still happens, unchanged: the same `usecase.deleted` event goes out, so the gateway
        deactivates the keys, drops the memberships, group grants, budgets, limits, rules and
        pipeline, and the use case stops serving traffic within a Kafka round trip. Retiring is
        immediate and complete as far as *access* is concerned.

        What no longer happens: the row disappearing. What it was for, which models it had
        released, whether it stored prompts, how long it kept them and who its members were all
        live here — and the gateway's audit rows, which are kept on purpose, name it only by slug.
        Destroying this row leaves the traffic without the context that makes it evidence.
        """
        if not self._may_admin(instance):
            raise PermissionDenied("You are not an admin of this use case.")
        if instance.deleted_at is not None:
            # Not an error worth failing on — the caller asked for a state the system is in — but
            # not a second event either: re-emitting would re-run the gateway's cascade against
            # rows already gone, and overwrite *who* retired it with whoever asked twice.
            return
        with transaction.atomic():
            instance.deleted_at = timezone.now()
            instance.deleted_by = str(getattr(self.request.user, "username", "") or "")
            instance.save(update_fields=["deleted_at", "deleted_by", "updated_at"])
            emit("usecase.deleted", {"slug": instance.slug})

    def _may_admin(self, usecase: UseCase) -> bool:
        return may_admin(self.request.user, usecase)

    def _may_manage(self, usecase: UseCase) -> bool:
        return may_manage(self.request.user, usecase)

    def _is_member(self, usecase: UseCase) -> bool:
        return is_member(self.request.user, usecase)

    def _resolve_owner(self, usecase: UseCase, requested: str) -> tuple[Any, str]:
        """Who answers for the key, and who created it (`FRD-604` FR-5).

        Ordinarily the same person, and then `issued_by` stays blank — a distinction nobody asked
        for should not appear on every row. Naming somebody else splits the two questions a shared
        credential otherwise collapses: the owner is a technical account whose name the audit trail
        carries, and the issuer is the human, which is the fact that signing in *as* the technical
        user destroys.

        Three refusals, and each matters more than the feature:

        - naming **somebody else** is an administrator's act. Any member could do it, and a name in
          this field is not decoration: the gateway resolves a key to its owner, so the key acts
          with the owner's standing in the use case, spends the owner's per-person allowance, and
          writes the owner's name into every audit row. Measured on 2026-08-30 — a use-case *user*
          issued a key owned by that use case's administrator and read every stored prompt in a use
          case set to show each member their own, with the access recorded against the
          administrator. Nothing in the chain was a bug on its own; the choice of owner was the
          whole of it, and it was open to anybody.
        - an unknown name is refused rather than created, because a credential attached to a
          username nobody has is an accountability chain ending in a string;
        - somebody with no **grant** on this use case is refused, or the owner column becomes a
          place to put a colleague's name — which is `FRD-604`'s own defect with the sign reversed.
          `holds_a_grant`, not `is_member`: the latter says yes to a Global Administrator who is a
          member of nothing, and an owner has to be somebody whose access can end.
        """
        caller: Any = self.request.user
        if not requested or requested == caller.get_username():
            return caller, ""

        if not may_manage(caller, usecase):
            raise PermissionDenied(
                "Only an administrator of this use case may issue a key owned by somebody else. "
                "A key acts with its owner's standing, spends their allowance and carries their "
                "name in the audit trail."
            )

        owner = get_user_model().objects.filter(username=requested).first()
        if owner is None:
            raise ValidationError(
                {
                    "owner": [
                        f"There is no user '{requested}'. A key is owned by an identity the "
                        "directory knows, so that somebody can be asked about it."
                    ]
                }
            )
        if not holds_a_grant(owner, usecase):
            raise ValidationError(
                {
                    "owner": [
                        f"'{requested}' has no access to this use case, so a key cannot be owned "
                        "by them. Give them access first — a credential names who answers for it."
                    ]
                }
            )
        return owner, caller.get_username()

    @action(detail=False, methods=["get"], url_path="retired")
    def retired(self, request: Request) -> Response:
        """The tombstones, for the role that decides what becomes of them.

        Its own route rather than a flag on the list, because these are not use cases any more:
        nothing may call them, no member may reach them, and every screen that offers an action
        would be offering one that cannot be carried out. What is left is a record, and a record
        is read from somewhere that says so.

        Bounded to governance — a **Global Administrator** decides, and `IT Steuerung` oversees
        without acting. Notably **not** the use-case administrator, including the one who retired
        it: the whole design assumes that person may be the reason the record matters.
        """
        if not has_governance_role(request.user):
            raise PermissionDenied("Retired use cases are visible to governance roles.")
        rows = UseCase.objects.filter(deleted_at__isnull=False).order_by("-deleted_at", "slug")
        return Response(RetiredUseCaseSerializer(rows, many=True).data)

    @action(detail=True, methods=["delete"], url_path="purge")
    def purge(self, request: Request, slug: str | None = None) -> Response:
        """Remove a retired use case for good — **the deliberate later decision** (`FRD-607`).

        Three conditions, and each one is a separate defence:

        1. **A Global Administrator only.** Not the use-case administrator who retired it, and not
           `IT Steuerung`, which oversees and does not act. The point of splitting retire from
           purge is that the party who might want the record gone is not the party who can remove
           it.
        2. **It must already be retired.** Purging in one step would rebuild exactly the hole this
           feature closes, behind a longer URL.
        3. **It must have been retired for at least `PURGE_AFTER_DAYS`.** A decision that can be
           taken in the same minute as the deletion is not a second decision. The window is short
           enough to be operable and long enough that erasing evidence requires waiting for it —
           and waiting is what makes the act visible in the retired list meanwhile.

        The object is fetched off the retired set explicitly. `get_object()` reads `get_queryset()`,
        which excludes retired rows on purpose, so it would answer 404 here — correctly for every
        other route and wrongly for this one.
        """
        if not has_role(request.user, Role.GLOBAL_ADMIN):
            raise PermissionDenied("Only a Global Administrator may purge a retired use case.")
        # Fetched **without** the retired filter, so that one expression below enforces the whole
        # rule. Written first as `filter(deleted_at__isnull=False)` *and* a guard, and a mutation
        # run showed the property surviving the loss of either: two independent copies of one
        # rule, which is redundancy rather than defence in depth — nothing could tell which was
        # load-bearing, and a later reader deleting "the duplicate" would have had even odds.
        usecase = UseCase.objects.filter(slug=slug).first()
        if usecase is None or usecase.deleted_at is None:
            # Live, or never existed. Both are "there is nothing here to purge", and telling the
            # two apart would say whether a slug exists to somebody who cannot see it.
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
            # A **second** event, not a repeat of `usecase.deleted`. That one ends access and keeps
            # the tombstone; this one says the record itself is gone, and the gateway drops the
            # last row it kept — the one retention reads a use case's own period from.
            emit("usecase.purged", {"slug": purged})
        return Response(status=status.HTTP_204_NO_CONTENT)

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
            revoked = _revoke_keys_without_access(usecase)
        return Response({"revoked_keys": revoked}, status=status.HTTP_200_OK)

    #: `[^/]+`, and the dot is the point. It was `[^/.]+` — the router's default, which exists to
    #: keep `.json` format suffixes routable — so **any username containing a dot was unaddressable
    #: by this route**: `DELETE …/members/vadim.scheibe/` answered `404`, on the single most
    #: ordinary shape a directory hands out (`first.last`). Percent-encoding does not help, because
    #: the path is decoded before it is matched. Format suffixes on a `DELETE` are worth nothing;
    #: being able to remove a colleague is worth a great deal.
    @action(detail=True, methods=["delete"], url_path="members/(?P<username>[^/]+)")
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
            # **The name as it is stored, not as it was typed.** The consumer keys
            # `use_case_members` on this string, and the route's own capture is whatever survived
            # URL routing — one character different and the gateway keeps a membership Management
            # has removed.
            emit("membership.removed", {"slug": usecase.slug, "username": user.get_username()})
            revoked = _revoke_keys_without_access(usecase)
        return Response({"revoked_keys": revoked}, status=status.HTTP_200_OK)

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
        owner, issued_by = self._resolve_owner(usecase, payload.validated_data["owner"])
        # Always a date: the serializer fills in the configured default when none was asked for,
        # and refuses anything past the configured maximum. There is no branch here for "never".
        days = payload.validated_data["expires_in_days"]
        expires_at = timezone.now() + timedelta(days=days)
        full, prefix, key_hash = generate_api_key()
        with transaction.atomic():
            ApiKey.objects.create(
                use_case=usecase,
                owner=owner,
                issued_by=issued_by,
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
                    # The **owner**: who answers for this credential, and the name every audit row
                    # will carry. Not the issuer — a row describes what called, not who authorised
                    # the credential months earlier (`FRD-604` §5.3).
                    "subject": owner.get_username(),
                    "issued_by": issued_by,
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
                "owner": owner.get_username(),
                "issued_by": issued_by,
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
                # The same shape the serializer produces, field for field. A default that is
                # *missing* a key the saved form has is a default the console has to special-case.
                #
                # **A use case with no row here still has a pipeline** — a request comes in and a
                # request is dispatched, and no steps means nothing happens in between. That is a
                # configuration, not an absence, which is why this answers a pipeline rather than
                # a 404.
                return Response({"steps": [], "fallback_models": []})
            return Response(PipelineConfigSerializer(config).data)

        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot edit the pipeline of this use case.")
        if config is None:
            config = PipelineConfig(use_case=usecase)
        # The use case travels in the context so the serializer can check every model the
        # pipeline names against what has been released to it (`FRD-308`).
        serializer = PipelineConfigSerializer(
            config, data=request.data, context={"use_case": usecase}
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            config = serializer.save()
            emit(
                "pipeline.upserted",
                {
                    "use_case": usecase.slug,
                    "steps": config.steps,
                    "fallback_models": config.fallback_models,
                    # Where a caller who names no model enters (`ADR-0020`). Carried to the gateway
                    # because the **dry run** needs it there — it used to guess, and its own
                    # comments record three wrong guesses in a row.
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
        defaults: dict[str, Any] = {
            "limit_cost": data.get("limit_cost"),
            "limit_tokens": data.get("limit_tokens"),
            "limit_requests": data.get("limit_requests"),
        }
        # **Only when it was said.** This read `data.get("enabled", True)`, so any upsert that did
        # not mention the field switched the budget back on — and the console's own save never
        # mentions it. Measured: disable a budget, change its token cap from the console, and it is
        # enforcing again with nothing on screen having said so. A limit somebody deliberately
        # lifted is a decision; silently reversing it is worse than never offering the switch.
        # Absent on a *create* still means the model's default, which is on.
        if "enabled" in data:
            defaults["enabled"] = data["enabled"]
        with transaction.atomic():
            budget, _created = Budget.objects.update_or_create(
                use_case=usecase,
                scope=data["scope"],
                subject=data["subject"],
                period=data["period"],
                defaults=defaults,
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
                # Same rule as budgets above: an upsert that says nothing about `enabled` leaves it
                # alone rather than switching the limit back on.
                defaults={
                    "limit_rpm": data["limit_rpm"],
                    "burst": data.get("burst", 0),
                    **({"enabled": data["enabled"]} if "enabled" in data else {}),
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
