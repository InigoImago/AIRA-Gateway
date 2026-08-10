"""Serializers for API keys (FRD-205)."""

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


class IssueApiKeySerializer(serializers.Serializer[Any]):
    # ``label`` shadows the inherited ``Field.label`` attribute; the field is what we want here.
    label = serializers.CharField(  # type: ignore[assignment]
        required=False, allow_blank=True, default=""
    )
    #: Lifetime in days. **Optional to state, never optional to have**: omitting it takes
    #: `AIRA_API_KEY_DEFAULT_DAYS` (30 days), and anything past `AIRA_API_KEY_MAX_DAYS` (180) is
    #: refused by name. There is no way to ask this API for a key that never expires — a credential
    #: with no end date has to be inventoried by somebody who remembers to, and nobody does.
    #:
    #: The bounds are checked in `validate` rather than declared on the field, for two reasons: an
    #: installation that changes the setting means it without a redeploy, and a per-field validator
    #: does **not** run for a field the caller omitted — which is exactly the case that has to end
    #: with a date.
    expires_in_days = serializers.IntegerField(required=False, allow_null=True, default=None)
    #: Issue it **on behalf of** somebody else — a technical account for a shared credential
    #: (`FRD-604` FR-5).
    #:
    #: Optional, and omitting it is the ordinary case: the caller owns what they create. Naming
    #: one splits the two questions a shared key otherwise collapses — who answers for the
    #: credential, and which human made it — and the second is the one that gets destroyed by
    #: signing in as the technical user, which is the alternative this exists to replace.
    #:
    #: A username, checked against the directory *and* against access to this use case by the
    #: view: a credential must not be attached to somebody who has nothing to do with the use
    #: case, or the owner column becomes a place to put a colleague's name.
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
