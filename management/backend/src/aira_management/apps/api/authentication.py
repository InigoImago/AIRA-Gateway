"""Keycloak JWT bearer authentication for DRF (FRD-200).

Verifies the bearer token with the shared :class:`aira_common.oidc.JwtVerifier` and
provisions a Django user from the verified claims. Verified claims are attached as
``request.auth`` for RBAC (FRD-201).

Users are bound to the token's ``sub``, not to its ``preferred_username`` — see
:mod:`aira_management.apps.api.models` for why (ADR-0007).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework.request import Request

from aira_common.oidc import JwtVerifier, build_jwks_client
from aira_management.apps.api.attempts import FailedAuthentications
from aira_management.apps.api.models import OidcIdentity, PendingIdentity
from aira_management.config.runtime import get_settings
from aira_management.rbac import sync_user_groups, sync_user_roles

_BEARER = "bearer "


@lru_cache(maxsize=1)
def build_management_verifier() -> JwtVerifier | None:
    """Build the JWT verifier from settings, or None when OIDC is unconfigured."""
    settings = get_settings()
    if not settings.oidc_issuer:
        return None
    return JwtVerifier(
        settings.oidc_issuer,
        settings.oidc_audience,
        build_jwks_client(settings.jwks_uri()),
        clock_skew_seconds=settings.oidc_clock_skew_seconds,
        expiry_leeway_seconds=settings.oidc_expiry_leeway_seconds,
    )


@lru_cache(maxsize=1)
def build_attempt_bound() -> FailedAuthentications:
    """The bound on refused authentications. Built once; the counting is in the cache."""
    return FailedAuthentications(get_settings().throttle_auth_failures)


class KeycloakJWTAuthentication(BaseAuthentication):
    def authenticate(self, request: Request) -> tuple[AbstractBaseUser, dict[str, Any]] | None:
        header = request.headers.get("Authorization", "")
        if header[: len(_BEARER)].lower() != _BEARER:
            return None

        verifier = build_management_verifier()
        if verifier is None:
            return None

        # **Before the verification, because the verification is the cost.** A presented token is
        # checked against the issuer's JWKS before anything decides it is invalid, so an address
        # probing credentials pays nothing and this service pays for every attempt. Counted as
        # refusals only (`apps.api.attempts`), so a working credential never touches this bucket
        # however busy its holder is — the rule `ADR-0015` settled on the other plane.
        bound = build_attempt_bound()
        if bound.over_the_bound(request):
            raise Throttled(wait=bound.retry_after(request))

        claims = verifier.verify(header[len(_BEARER) :].strip())
        if claims is None:
            bound.record_failure(request)
            raise AuthenticationFailed("Invalid or expired token.")
        subject = claims.get("sub")
        if not subject:
            bound.record_failure(request)
            raise AuthenticationFailed("Token has no subject.")
        user = self._provision_user(str(subject), claims)
        # Keycloak is the source of truth for both: which roles this person holds, and which
        # groups they are in. The second is what makes a group grant reach them (`FRD-209`), and
        # it is re-read on every request so leaving a department takes effect on the next token
        # rather than whenever somebody remembers to edit an access list here.
        sync_user_roles(user, claims)
        sync_user_groups(user, claims)
        return user, claims

    def authenticate_header(self, request: Request) -> str:
        # Present so DRF returns 401 (not 403) for unauthenticated requests.
        return "Bearer"

    @staticmethod
    @transaction.atomic
    def _provision_user(subject: str, claims: dict[str, Any]) -> AbstractBaseUser:
        """Resolve the Django user for ``subject``, creating one on first sight.

        Resolution order:
        1. an existing ``OidcIdentity`` for this ``sub`` — the authoritative binding;
        2. otherwise an account somebody **invited** under this ``preferred_username``
           (:class:`PendingIdentity`), which is then bound and the invitation consumed;
        3. otherwise a fresh user, whose username is suffixed if the preferred one is taken by
           somebody else's identity.

        **Step 2 used to be "any unbound account with this name", and that was the hole.** Trust on
        first use is a reasonable rule when the account was created *for* the person arriving; it is
        an account takeover when it was created for anybody else. Measured on 2026-08-30: a token
        with an arbitrary `sub` and `preferred_username: "admin"` was handed the seeded `admin`
        account together with its memberships and object permissions. Nothing was compromised to do
        it and nothing recorded that it had happened — the claim simply went to whoever asked first.
        An invitation is the same trust made **explicit, one-shot and attributable**, and an account
        nobody invited is now claimable by nobody.

        **The first request from a new person is more than one request.** The console loads
        `/api/v1/me` and `/api/v1/use-cases/` at the same moment, so two requests carrying the same
        brand-new `sub` arrive concurrently: both find no identity, both create one, and the second
        loses on `api_oidcidentity_subject_key` — a **500 on the first screen, for every user's
        first login**. Measured on 2026-08-11 against a freshly seeded stack, which is exactly the
        state a demonstration starts from.

        `transaction.atomic` does not prevent it and was never going to: it makes each attempt
        atomic, not exclusive, and the two attempts are on different connections. The fix is the
        one the race actually calls for — **lose gracefully**: whoever arrives second re-reads and
        uses the row the winner wrote. A savepoint keeps the failed INSERT from poisoning the
        surrounding transaction, which is why the create sits in its own `atomic` block rather
        than relying on the decorator.
        """
        identity = OidcIdentity.objects.filter(subject=subject).select_related("user").first()
        if identity is not None:
            return identity.user

        user_model = get_user_model()
        preferred = safe_username(claims.get("preferred_username"), subject)
        email = safe_email(claims.get("email"))

        try:
            with transaction.atomic():
                invited = (
                    PendingIdentity.objects.select_related("user")
                    .filter(user__username=preferred)
                    .first()
                )
                if invited is not None:
                    existing_user = invited.user
                    OidcIdentity.objects.create(subject=subject, user=existing_user)
                    # Consumed, not kept. A second person carrying the same name later is a
                    # stranger, and an invitation that can be redeemed twice is not an invitation.
                    invited.delete()
                    return existing_user

                taken = user_model.objects.filter(username=preferred).exists()
                username = preferred if not taken else _suffixed(preferred, subject)
                user = user_model.objects.create(username=username, email=email)
                # **No password, and said so.** `objects.create` leaves the column empty, and
                # Django reads an empty password as *usable* — so every provisioned account
                # carried a credential of a kind, against the day somebody adds the login this API
                # deliberately does not have (no sessions, no admin site, bearer tokens only).
                # Keycloak owns authentication here; an account that could ever be signed into
                # another way would be a second answer to "who is this".
                user.set_unusable_password()
                user.save(update_fields=["password"])
                OidcIdentity.objects.create(subject=subject, user=user)
                return user
        except IntegrityError:
            # Somebody else provisioned this subject between the read above and the write. Their
            # row is the binding; ours was never needed. Re-read rather than retry the create,
            # because the winner's row is exactly what this function was trying to produce.
            identity = OidcIdentity.objects.filter(subject=subject).select_related("user").first()
            if identity is None:
                # Not the race, then: a genuine constraint failure — most likely the username,
                # taken by an account this subject may not have. Raised rather than worked
                # around, because inventing a second account for one person is how an audit
                # trail comes to name two people who are one.
                raise
            return identity.user


#: What Django will actually store. A `username` column is `varchar(150)` and a name longer than
#: that is a `DataError` from the driver — a **500 on somebody's first sign-in**, on a value the
#: caller does not control and cannot shorten. SQLite enforces no length, which is why the hermetic
#: suite could never see it (`LESSONS.md` §1).
USERNAME_MAX_LENGTH = 150
#: Django's own `EmailField` width. Bounded here for the same reason and with the same consequence.
EMAIL_MAX_LENGTH = 254


def safe_username(claimed: object, subject: str) -> str:
    """The username to store for ``claimed``, falling back to the subject.

    Three things a directory may hand over that this column cannot take, and all three arrive on
    the **first** request of somebody who has done nothing wrong:

    - a name longer than the column;
    - a name containing characters Django's own validator refuses — a `/` in particular, which
      would also make the member-removal route unable to address them;
    - no name at all.

    The fallback is the **subject**, which is unique, stable and always storable. It is deliberately
    not a mangled version of the claimed name: silently rewriting somebody's name produces an
    account whose identity nobody can search for, where a subject at least says plainly that the
    directory gave us nothing usable.
    """
    text = claimed.strip() if isinstance(claimed, str) else ""
    if text and len(text) <= USERNAME_MAX_LENGTH:
        try:
            UnicodeUsernameValidator()(text)
        except DjangoValidationError:
            pass
        else:
            return text
    return subject[:USERNAME_MAX_LENGTH]


def safe_email(claimed: object) -> str:
    """The address to store, bounded to the column. Never a reason to refuse a sign-in."""
    return claimed.strip()[:EMAIL_MAX_LENGTH] if isinstance(claimed, str) else ""


def _suffixed(preferred: str, subject: str) -> str:
    """A distinct username for somebody whose preferred one belongs to another identity.

    Truncated so the result still fits the column: `preferred` may already be at the limit, and a
    suffix appended past it is the very `DataError` :func:`safe_username` exists to prevent.
    """
    tail = f"-{subject[:8]}"
    return f"{preferred[: USERNAME_MAX_LENGTH - len(tail)]}{tail}"
