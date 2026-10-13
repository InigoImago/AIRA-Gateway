"""Giving and taking away access to one use case, and resolving the people a grant names.

``holder`` is a user **or a Django group** throughout: guardian takes either, and that is the
whole mechanism behind group grants (`FRD-209` §2.2). A second permission path for groups would be
a second chance to forget one.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from guardian.shortcuts import assign_perm, remove_perm
from rest_framework.exceptions import ValidationError

from aira_common.directory import DirectoryUnavailable
from aira_management.apps.api.authentication import namesakes_of, signs_in_as
from aira_management.apps.api.models import PendingIdentity
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.directory.service import known_person
from aira_management.apps.usecases.access import CHANGE as _CHANGE
from aira_management.apps.usecases.access import MANAGE as _MANAGE
from aira_management.apps.usecases.access import VIEW as _VIEW
from aira_management.apps.usecases.access import holds_a_grant
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.apps.usecases.views.payloads import _membership_payload


def _grant(holder: Any, usecase: UseCase, role: str) -> None:
    """Assign the object permissions for ``role``."""
    assign_perm(_VIEW, holder, usecase)
    if role == UseCaseMembership.ADMIN:
        assign_perm(_CHANGE, holder, usecase)
        assign_perm(_MANAGE, holder, usecase)


def _revoke(holder: Any, usecase: UseCase) -> None:
    for perm in (_VIEW, _CHANGE, _MANAGE):
        remove_perm(perm, holder, usecase)


def _admit(usecase: UseCase, user: Any, role: str) -> UseCaseMembership:
    """Make ``user`` a direct member of ``usecase`` as ``role`` — the one sequence every writer of
    a membership runs: the row, the object permissions for exactly that role (revoked first, so
    lowering an administrator to a user lowers them), and the event the gateway's direct
    memberships are built from (`FRD-209` §2.1). Call it inside the change's transaction."""
    membership, _created = UseCaseMembership.objects.update_or_create(
        use_case=usecase, user=user, defaults={"role": role}
    )
    _revoke(user, usecase)
    _grant(user, usecase, role)
    emit("membership.upserted", _membership_payload(usecase, user.get_username(), role))
    return membership


def _resolve_user(username: str) -> Any:
    """The local account for ``username``, which must already exist.

    Used where a *removal* names somebody: taking access from a name nobody has is a no-op dressed
    as an action, and the caller is better told the name is wrong.
    """
    user = get_user_model().objects.filter(username=username).first()
    if user is None:
        raise ValidationError({"username": [f"Unknown user '{username}'."]})
    return user


def _resolve_or_invite(username: str, *, invited_by: str) -> Any:
    """The local account for ``username``, **creating an invited one** if there is none yet.

    `FRD-209` FR-4: the picker offers everybody the directory knows, so a grant must accept
    somebody who has not signed in. An account is created only for a name the directory confirms,
    and it carries an invitation (`PendingIdentity`) rather than a binding — there is no `sub`
    until they arrive.

    Three refusals, kept apart because three different people fix them: the directory has no such
    person (the typist), no directory is configured (the operator), the directory could not be
    reached (nobody, yet).
    """
    user = get_user_model().objects.filter(username=username).first()
    if user is not None:
        _refuse_a_name_the_gateway_reads_differently(user)
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


def _refuse_a_name_the_gateway_reads_differently(user: Any) -> None:
    """Refuse a direct membership whose name would reach a different person at the gateway.

    A membership crosses to the gateway as this plane's username, and the gateway matches it
    against the token's `preferred_username` (`FRD-209` §2.1). The two coincide until provisioning
    has to rename somebody whose sign-in name was already taken here (`_suffixed`): from then on
    one name stands for two people, and the second person's own account name for nobody.
    """
    name = user.get_username()
    namesakes = sorted(other.get_username() for other in namesakes_of(name))
    if namesakes:
        raise ValidationError(
            {
                "username": [
                    f"'{name}' is ambiguous: the account {', '.join(namesakes)} also signs in as "
                    f"'{name}'. The gateway grants a direct membership to whoever signs in under "
                    "that name, which is no longer the account shown here, so the grant is "
                    "refused. Grant access through a Keycloak group instead."
                ]
            }
        )
    signed_in_as = signs_in_as(user)
    if signed_in_as is not None:
        raise ValidationError(
            {
                "username": [
                    f"'{name}' signs in as '{signed_in_as}' — the account was renamed here because "
                    "that name was taken — and the gateway matches a direct membership by the "
                    "sign-in name, so a membership under this name would reach nobody there. "
                    "Grant access through a Keycloak group instead."
                ]
            }
        )


def _revoke_key(key: ApiKey, slug: str) -> None:
    """Deactivate one key and publish the revocation — terminal and dated (`ADR-0007`)."""
    key.is_active = False
    key.revoked_at = timezone.now()
    key.save(update_fields=["is_active", "revoked_at"])
    emit("api_key.revoked", {"prefix": key.prefix, "use_case": slug, "status": "revoked"})


def _revoke_keys_without_access(usecase: UseCase) -> list[str]:
    """Revoke every active key of ``usecase`` whose owner no longer holds a grant on it.

    Access ending has to end the credentials that rested on it (`FRD-613`); otherwise a removed
    member's key keeps serving against the use case's budget until it expires.

    Asked as *"does this owner still hold a grant"* rather than *"was this the person removed"*,
    so it also holds after a **group** grant is revoked, and somebody with another route in keeps
    their keys (`FRD-209` FR-5). Revoked, never deleted: a key that stopped working is a fact an
    investigation asks about.
    """
    revoked: list[str] = []
    keys = ApiKey.objects.filter(use_case=usecase, is_active=True).select_related("owner")
    for key in keys:
        if holds_a_grant(key.owner, usecase):
            continue
        _revoke_key(key, usecase.slug)
        revoked.append(key.prefix)
    return revoked
