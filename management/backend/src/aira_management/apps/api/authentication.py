"""Keycloak JWT bearer authentication for DRF (`FRD-200`).

Verifies the bearer token with the shared :class:`aira_common.oidc.JwtVerifier` and provisions a
Django user from the verified claims, which are attached as ``request.auth`` for RBAC (`FRD-201`).
Users are bound to the token's ``sub``, never to its ``preferred_username`` (`ADR-0007`, see
:mod:`aira_management.apps.api.models`).
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

#: Django's `username` column width. A longer name is a `DataError` from the driver — a 500 on
#: somebody's first sign-in, on a value they cannot shorten. SQLite enforces no length, so only
#: Postgres shows it (`LESSONS.md` §1).
USERNAME_MAX_LENGTH = 150
#: Django's own `EmailField` width, bounded for the same reason.
EMAIL_MAX_LENGTH = 254


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

        # Checked **before** verification, because verification against the JWKS is the cost.
        # Only refusals are counted, so a working credential never touches this bucket (`ADR-0015`).
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
        # Keycloak decides both roles and group membership, re-read on every request, so leaving a
        # group takes effect on the next token (`FRD-209`).
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

        1. an existing ``OidcIdentity`` for this ``sub`` — the authoritative binding;
        2. otherwise an account somebody **invited** under this ``preferred_username``
           (:class:`PendingIdentity`), which is bound and the invitation consumed;
        3. otherwise a fresh user, suffixed if the preferred name belongs to another identity.

        Step 2 is an invitation, never "any unbound account with this name": claiming an account
        by name alone would hand the seeded `admin` to anybody presenting that username.

        The first sign-in arrives as concurrent requests with the same new `sub`. Whoever loses
        the insert race re-reads and uses the winner's row; the create sits in its own savepoint so
        the failed INSERT does not poison the surrounding transaction.
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
                    # Consumed: an invitation that can be redeemed twice is not an invitation.
                    invited.delete()
                    return existing_user

                taken = user_model.objects.filter(username=preferred).exists()
                username = preferred if not taken else _suffixed(preferred, subject)
                user = user_model.objects.create(username=username, email=email)
                # No usable password: Keycloak owns authentication, and Django would read an empty
                # password column as usable.
                user.set_unusable_password()
                user.save(update_fields=["password"])
                OidcIdentity.objects.create(subject=subject, user=user)
                return user
        except IntegrityError:
            # Somebody provisioned this subject between the read and the write: use their row.
            identity = OidcIdentity.objects.filter(subject=subject).select_related("user").first()
            if identity is None:
                # Not the race but a genuine constraint failure (most likely the username). Raised,
                # because inventing a second account for one person splits the audit trail.
                raise
            return identity.user


def safe_username(claimed: object, subject: str) -> str:
    """The username to store for ``claimed``, falling back to the subject.

    A directory may hand over a name longer than the column, one Django's validator refuses (a `/`
    would also make the member-removal route unable to address it), or none at all. The fallback
    is the **subject** — unique, stable, storable — rather than a silently mangled name nobody can
    search for.
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

    Truncated so the result still fits the column, since ``preferred`` may already be at the limit.
    """
    tail = f"-{subject[:8]}"
    return f"{preferred[: USERNAME_MAX_LENGTH - len(tail)]}{tail}"
