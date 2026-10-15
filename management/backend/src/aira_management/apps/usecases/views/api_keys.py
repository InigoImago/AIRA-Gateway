"""API keys bound to one use case: list, issue, revoke (`FRD-205`, `FRD-604`).

A key is data-plane access, so issuing one takes **membership**, not visibility: the oversight
roles see every use case and must not be able to mint a key for any of them (`ADR-0007`). The
plaintext is returned once; only its hash is stored and distributed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from aira_common.apikeys import generate_api_key
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.apikeys.serializers import ApiKeySerializer, IssueApiKeySerializer
from aira_management.apps.usecases.access import holds_a_grant, may_manage
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.apps.usecases.views.base import UseCaseViewBase
from aira_management.apps.usecases.views.grants import _admit, _revoke_key
from aira_management.apps.usecases.views.payloads import _api_key_payload

#: How many fresh prefixes issuance draws before giving up. A prefix is 32 random bits and unique,
#: so a collision is rare and two in a row rarer still; the bound keeps "rare" from being a loop.
PREFIX_ATTEMPTS = 3


class NoFreePrefix(APIException):
    """Every prefix drawn was taken — astronomically unlikely, and nothing the caller did."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "No unused key prefix could be drawn. Try again."
    default_code = "key_prefix_exhausted"


class ApiKeysMixin(UseCaseViewBase):
    def _resolve_owner(self, usecase: UseCase, requested: str) -> tuple[Any, str]:
        """Who answers for the key, and who issued it when that is somebody else (FR-5).

        `issued_by` stays blank when the caller owns the key. Naming another owner is refused
        unless all three hold:

        - the caller **manages** the use case — a key acts with its owner's standing, spends their
          allowance and writes their name into every audit row, so choosing it is an
          administrator's act;
        - the name is a **known account** — a credential for a username nobody has is an
          accountability chain ending in a string;
        - the owner holds a **grant** here — `holds_a_grant`, not `is_member`, which says yes to a
          Global Administrator who is a member of nothing; an owner's access must be able to end.
        """
        caller: Any = self.request.user
        if not requested or requested == caller.get_username():
            # The caller's own key rests on the caller's grant, checked when it is issued.
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

    @action(detail=True, methods=["get", "post"], url_path="api-keys")
    def api_keys(self, request: Request, slug: str | None = None) -> Response:
        """List keys, or issue one bound to this use case."""
        usecase = self.get_object()
        if request.method == "GET":
            keys = ApiKey.objects.filter(use_case=usecase).select_related("owner")
            return Response(
                ApiKeySerializer(keys, many=True, context={"viewer": request.user}).data
            )

        if not self._is_member(usecase):
            raise PermissionDenied("Only members of this use case may issue API keys.")
        payload = IssueApiKeySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        label = payload.validated_data["label"]
        owner, issued_by = self._resolve_owner(usecase, payload.validated_data["owner"])
        # Always a date: the serializer fills in the default and refuses anything past the maximum.
        days = payload.validated_data["expires_in_days"]
        expires_at = timezone.now() + timedelta(days=days)
        for _attempt in range(PREFIX_ATTEMPTS):
            full, prefix, key_hash = generate_api_key()
            try:
                with transaction.atomic():
                    if not holds_a_grant(owner, usecase):
                        # Only somebody who may manage every use case gets here (`is_member`'s
                        # blanket, the Global Administrator's): issuing is open to them without a
                        # grant, and a key must rest on access that can end (`FRD-613`). So issuing
                        # one makes them a member — a grant the sweep sees and an administrator can
                        # take away — rather than a key whose access was never recorded and that
                        # any unrelated removal would revoke.
                        _admit(usecase, owner, UseCaseMembership.USER)
                    key = ApiKey.objects.create(
                        use_case=usecase,
                        owner=owner,
                        issued_by=issued_by,
                        prefix=prefix,
                        key_hash=key_hash,
                        label=label,
                        expires_at=expires_at,
                    )
                    emit("api_key.created", _api_key_payload(key))
            except IntegrityError:
                # Another key holds this prefix: draw again. Anything else is not ours to retry.
                if not ApiKey.objects.filter(prefix=prefix).exists():
                    raise
                continue
            break
        else:
            raise NoFreePrefix()
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
            _revoke_key(key, usecase.slug)
        return Response(status=status.HTTP_204_NO_CONTENT)
