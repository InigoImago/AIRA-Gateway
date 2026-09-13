"""Serializer for the model catalog (FRD-403, FRD-114)."""

from __future__ import annotations

from typing import Any

from django.db.models import Max
from rest_framework import serializers

from aira_management.apps.catalog.models import Model
from aira_management.apps.catalog.validation import validate_declaration

#: The fields the declaration rules (`validation.validate_declaration`) are asked about.
DECLARATION_FIELDS = (
    "capabilities",
    "hosting",
    "thinking",
    "embedding",
    "attachments",
    "context_window",
    "max_output_tokens",
    "default_max_output_tokens",
)


class ModelSerializer(serializers.ModelSerializer[Model]):
    is_priced = serializers.BooleanField(read_only=True)
    is_declared = serializers.BooleanField(read_only=True)

    #: Where auto-assigned KIRA ids start: above every id this repository ships or documents (the
    #: demo seeds `9001`/`9002`, the showcase `9102`), so a console-created model cannot take a
    #: number a later `make seed` wants. Lower ids stay free for installations migrating from the
    #: predecessor, which set them explicitly.
    KIRA_ID_BASE = 9500

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
        #: DRF's generated `UniqueValidator` is dropped so `validate_numeric_id` can name the other
        #: model in its refusal; the database constraint still holds underneath.
        extra_kwargs: dict[str, dict[str, Any]] = {"numeric_id": {"validators": []}}

    def validate_name(self, name: str) -> str:
        """A model's name is its **identity**, and an identity is set once.

        The name crosses Kafka into the gateway's `model_catalog` and is what releases (`FRD-308`),
        pipeline steps, prices and every `request_logs.model` name. A rename would leave the old row
        approved in the gateway and unreachable from here — the loophole `FRD-307` closed. The
        upsert in `ModelViewSet.create` re-posts the **same** name, which is not a change.
        """
        if self.instance is not None and name != self.instance.name:
            raise serializers.ValidationError(
                f"A model's name is its identity and cannot be changed. '{self.instance.name}' is "
                "what the gateway's catalog, every use case's release, every pipeline step and "
                "every audit row already name. Catalogue the new name as its own model and "
                "un-approve this one — `display_name` is the field for what people should read."
            )
        return name

    def validate_numeric_id(self, value: int | None) -> int | None:
        """The integer a KIRA client addresses this model by (`FRD-107`), refused when taken.

        The gateway answers 503 for a duplicate rather than guess which model to bill, so the write
        side says so where somebody can still fix it — rather than as a constraint's 500.
        """
        if value is None:
            return None
        if value < 1:
            # Dropping DRF's validators also dropped the field's range check.
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

        A model without one is invisible on the KIRA surface (`MODEL_NOT_FOUND`) while looking
        fully configured — a control displayed as working and doing nothing (`FRD-125`).
        Auto-assigned rather than required: an installation that cares sets it explicitly.
        """
        if validated_data.get("numeric_id") is None:
            highest = Model.objects.aggregate(top=Max("numeric_id"))["top"]
            validated_data["numeric_id"] = max(highest or 0, self.KIRA_ID_BASE) + 1
        return super().create(validated_data)

    def _effective(self, attrs: dict[str, Any], field: str) -> Any:
        """What the field will hold after the save: the incoming value, or the stored one.

        Every rule here is about the resulting model, not the edit — a partial `PATCH` of one price
        must be checked against the other price already on the row.
        """
        return attrs.get(field, getattr(self.instance, field, None))

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # A half-priced model would bill one direction and ignore the other: a figure that looks
        # complete and is wrong. Clearing one of the two is still refused — `attrs` then carries
        # the explicit `None` the model ends up with.
        has_input = self._effective(attrs, "input_price_per_million") is not None
        has_output = self._effective(attrs, "output_price_per_million") is not None
        if has_input != has_output:
            raise serializers.ValidationError(
                "Set both the input and the output price, or neither — a model priced in only "
                "one direction would report costs that look complete but are not."
            )
        # Merged over the instance like `_effective`, but falling back to the field's *empty*
        # value: DRF omits a field with a model default from `attrs`, so a create that never
        # mentions `capabilities` must be validated as `[]`, not refused as `None` (FRD-114 FR-3).
        empty: dict[str, Any] = {"capabilities": [], "hosting": ""}
        declaration = {
            field: attrs.get(field, getattr(self.instance, field, empty.get(field)))
            for field in DECLARATION_FIELDS
        }
        errors = validate_declaration(declaration)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs
