"""Serializer for the model catalog (FRD-403)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_management.apps.catalog.models import Model


class ModelSerializer(serializers.ModelSerializer[Model]):
    is_priced = serializers.BooleanField(read_only=True)

    class Meta:
        model = Model
        fields = [
            "name",
            "display_name",
            "provider",
            "input_price_per_million",
            "output_price_per_million",
            "is_priced",
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
        return attrs
