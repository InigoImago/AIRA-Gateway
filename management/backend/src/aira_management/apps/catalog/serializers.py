"""Serializer for the model catalog (FRD-403, FRD-114)."""

from __future__ import annotations

from typing import Any

from django.db.models import Max
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
            "approved",
            "provider",
            "input_price_per_million",
            "output_price_per_million",
            "cached_input_price_per_million",
            "cache_write_price_per_million",
            "is_priced",
            # FRD-114
            "capabilities",
            "publisher",
            "platform",
            "addressing",
            "underlying_model",
            "context_window",
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
        #: The unique constraint on `numeric_id` is enforced below with a sentence that names the
        #: **other model**. DRF's generated `UniqueValidator` runs first and would answer "model
        #: with this numeric id already exists" — true, and it leaves the reader to go and find
        #: which one. Dropping it hands the check to `validate_numeric_id`; the database constraint
        #: is still there underneath, so nothing is weakened by saying it better.
        extra_kwargs: dict[str, dict[str, Any]] = {"numeric_id": {"validators": []}}

    #: Where auto-assigned KIRA ids start: above everything this repository ships or documents
    #: (the demo seeds `9001` and `9002`, the showcase catalogues `9102` by hand), so a machine's
    #: first console-created model cannot take a number a later `make seed` wants. Everything below
    #: is left free for an installation migrating from the predecessor, whose clients already send
    #: particular ids — those are set explicitly, which is the whole point of the field.
    KIRA_ID_BASE = 9500

    def validate_numeric_id(self, value: int | None) -> int | None:
        """The integer a KIRA client addresses this model by (`FRD-107`).

        Uniqueness is a database constraint, and a constraint alone answers a caller with a 500 and
        a sentence about a key name. The read side already treats a duplicate as unservable — two
        entries claiming one id make the surface answer 503 rather than guess which model to bill —
        so the write side says so where somebody can still fix it.
        """
        if value is None:
            return None
        if value < 1:
            # Kept because taking DRF's validators off the field took its range check with them.
            raise serializers.ValidationError("A KIRA id is a positive integer.")
        clash = Model.objects.filter(numeric_id=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        other = clash.first()
        if other is not None:
            raise serializers.ValidationError(
                f"KIRA id {value} already belongs to '{other.name}'. Two models sharing one id "
                "cannot be told apart by a KIRA client, and the gateway refuses both rather than "
                "guessing which one to bill."
            )
        return value

    def create(self, validated_data: dict[str, Any]) -> Model:
        """Assign a KIRA id when none was given.

        **A model without one is addressable on the Gemini surface and invisible on the KIRA one.**
        It can be catalogued, approved and released, and a KIRA client still cannot name it —
        `by_numeric_id` finds nothing and the surface answers `MODEL_NOT_FOUND`. That is a control
        displayed as working and doing nothing (`FRD-125`), and it was the state of every model
        created through the console, because the field exists on the API and the form never offered
        it.

        Auto-assigned rather than required, because a number nobody chose is still better than no
        number, and an installation that *does* care — one migrating from the predecessor, whose
        clients already send particular ids — sets it explicitly and keeps its clients unchanged.
        That is what the field is for (`FRD-107`).
        """
        if validated_data.get("numeric_id") is None:
            highest = Model.objects.aggregate(top=Max("numeric_id"))["top"]
            validated_data["numeric_id"] = max(highest or 0, self.KIRA_ID_BASE) + 1
        return super().create(validated_data)

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
                "context_window",
                "max_output_tokens",
                "default_max_output_tokens",
            )
        }
        errors = validate_declaration(declaration)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs
