"""Serializer for the model catalog (FRD-403, FRD-114)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_management.apps.catalog.models import Model
from aira_management.apps.catalog.validation import validate_declaration


class ModelSerializer(serializers.ModelSerializer[Model]):
    is_priced = serializers.BooleanField(read_only=True)
    is_declared = serializers.BooleanField(read_only=True)

    class Meta:
        model = Model
        fields = [
            "name",
            "display_name",
            "provider",
            "input_price_per_million",
            "output_price_per_million",
            "is_priced",
            # FRD-114
            "capabilities",
            "publisher",
            "platform",
            "addressing",
            "underlying_model",
            "max_output_tokens",
            "default_max_output_tokens",
            "thinking",
            "embedding",
            "attachments",
            "hosting",
            "deprecated",
            "numeric_id",
            "is_declared",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # A half-priced model would bill one direction and silently ignore the other, which is
        # worse than having no price at all: the figure would look complete and be wrong.
        has_input = attrs.get("input_price_per_million") is not None
        has_output = attrs.get("output_price_per_million") is not None
        if has_input != has_output:
            raise serializers.ValidationError(
                "Set both the input and the output price, or neither — a model priced in only "
                "one direction would report costs that look complete but are not."
            )
        # The catalog is a runtime authority: what it says decides whether a request is accepted.
        # A declaration that cannot work is refused where it is written rather than discovered
        # where it is enforced (FRD-114 FR-3).
        #
        # Merged over the instance on a partial update, or a PATCH that touches only
        # `max_output_tokens` would be validated against a thinking block it cannot see.
        # The fallback is the *field's* empty value, not ``None``: DRF omits a field with a model
        # default from ``attrs``, so a create that never mentions `capabilities` would otherwise be
        # validated as "capabilities is None" and refused for saying nothing at all.
        empty: dict[str, Any] = {"capabilities": [], "hosting": ""}
        declaration = {
            field: attrs.get(field, getattr(self.instance, field, empty.get(field)))
            for field in (
                "capabilities",
                "hosting",
                "thinking",
                "embedding",
                "attachments",
                "max_output_tokens",
                "default_max_output_tokens",
            )
        }
        errors = validate_declaration(declaration)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs
