"""Serializers for API keys (`FRD-205`)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_common.apikeys import KeyLifetimeOutOfRange, check_key_lifetime
from aira_common.permissions import Permission
from aira_management.apps.apikeys.models import ApiKey
from aira_management.config.runtime import get_settings
from aira_management.rbac import permissions_of


class ApiKeySerializer(serializers.ModelSerializer[ApiKey]):
    """Public, masked view of a key: prefix + metadata, never the hash or plaintext."""

    owner = serializers.CharField(source="owner.username", read_only=True)
    owner_withheld = serializers.SerializerMethodField()

    class Meta:
        model = ApiKey
        fields = [
            "prefix",
            "label",
            "owner",
            "issued_by",
            "owner_withheld",
            "is_active",
            "created_at",
            "revoked_at",
            "expires_at",
        ]
        #: **Every one of them**, stated rather than left to no endpoint writing with it today:
        #: nothing here is a caller's to decide, and a writable one would let a caller revive a
        #: revoked key or choose the prefix of somebody else's.
        read_only_fields = fields

    def _withheld(self, key: ApiKey) -> bool:
        """Whether this key's person is withheld from the reader (`FRD-627` FR-15).

        Under strict data mode a key's owner and issuer are a person, and with the prefix on every
        request row the owner would name whose traffic that is — so they are for the person
        concerned, and for whoever sees people (Global Administrator and IT Security). Without a
        reader in the context nothing is withheld: the issuing response goes to the issuer.
        """
        viewer = self.context.get("viewer")
        if viewer is None or not get_settings().strict_data_mode:
            return False
        if Permission.INCIDENT_INVESTIGATE in permissions_of(viewer):
            return False
        name = viewer.get_username()
        return name not in {key.owner.get_username(), key.issued_by or ""}

    def get_owner_withheld(self, key: ApiKey) -> bool:
        return self._withheld(key)

    def to_representation(self, instance: ApiKey) -> dict[str, Any]:
        data = super().to_representation(instance)
        if data["owner_withheld"]:
            data["owner"] = None
            data["issued_by"] = None
        return data


class IssueApiKeySerializer(serializers.Serializer[Any]):
    # ``label`` shadows the inherited ``Field.label`` attribute; the field is what we want here.
    # Bounded to its column, which only PostgreSQL enforces — as a `DataError`, past this check.
    label = serializers.CharField(  # type: ignore[assignment]
        required=False,
        allow_blank=True,
        default="",
        max_length=ApiKey._meta.get_field("label").max_length,
    )
    #: Lifetime in days. **Optional to state, never optional to have**: omitted, it takes
    #: `AIRA_API_KEY_DEFAULT_DAYS`; past `AIRA_API_KEY_MAX_DAYS` it is refused. No key never
    #: expires. Bounded in `validate`, not on the field, because a field validator does not run for
    #: an omitted field — exactly the case that must still end with a date.
    expires_in_days = serializers.IntegerField(required=False, allow_null=True, default=None)
    #: Issue it **on behalf of** somebody else, e.g. a technical account for a shared credential
    #: (`FRD-604` FR-5). The view checks the name against the directory and against access to this
    #: use case.
    owner = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        days = attrs.get("expires_in_days")
        if days is None:
            attrs["expires_in_days"] = settings.api_key_default_days
            return attrs
        # The rule the gateway's break-glass CLI applies too: one definition, two planes.
        try:
            check_key_lifetime(days, settings.api_key_max_days)
        except KeyLifetimeOutOfRange as exc:
            raise serializers.ValidationError({"expires_in_days": [str(exc)]}) from exc
        return attrs
