"""Serializers for API keys (`FRD-205`)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_management.apps.apikeys.models import ApiKey
from aira_management.config.runtime import get_settings


class ApiKeySerializer(serializers.ModelSerializer[ApiKey]):
    """Public, masked view of a key: prefix + metadata, never the hash or plaintext."""

    owner = serializers.CharField(source="owner.username", read_only=True)

    class Meta:
        model = ApiKey
        fields = [
            "prefix",
            "label",
            "owner",
            "issued_by",
            "is_active",
            "created_at",
            "revoked_at",
            "expires_at",
        ]
        #: **Every one of them**, stated rather than left to no endpoint writing with it today:
        #: nothing here is a caller's to decide, and a writable one would let a caller revive a
        #: revoked key or choose the prefix of somebody else's.
        read_only_fields = fields


class IssueApiKeySerializer(serializers.Serializer[Any]):
    # ``label`` shadows the inherited ``Field.label`` attribute; the field is what we want here.
    label = serializers.CharField(  # type: ignore[assignment]
        required=False, allow_blank=True, default=""
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
        if days < 1:
            raise serializers.ValidationError(
                {"expires_in_days": ["A lifetime is a number of days, at least 1."]}
            )
        if days > settings.api_key_max_days:
            raise serializers.ValidationError(
                {
                    "expires_in_days": [
                        "The longest lifetime this installation allows is "
                        f"{settings.api_key_max_days} days."
                    ]
                }
            )
        return attrs
