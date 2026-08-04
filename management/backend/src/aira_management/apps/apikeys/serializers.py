"""Serializers for API keys (FRD-205)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_management.apps.apikeys.models import ApiKey


class ApiKeySerializer(serializers.ModelSerializer[ApiKey]):
    """Public, masked view of a key: prefix + metadata, never the hash or plaintext."""

    owner = serializers.CharField(source="owner.username", read_only=True)

    class Meta:
        model = ApiKey
        fields = ["prefix", "label", "owner", "is_active", "created_at", "revoked_at"]


class IssueApiKeySerializer(serializers.Serializer[Any]):
    # ``label`` shadows the inherited ``Field.label`` attribute; the field is what we want here.
    label = serializers.CharField(  # type: ignore[assignment]
        required=False, allow_blank=True, default=""
    )
