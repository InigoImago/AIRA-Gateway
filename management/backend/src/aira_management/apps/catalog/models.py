"""Model catalog with prices (FRD-403).

This is the table FRD-307 foresees for the governed model catalog, introduced here with the
attribute cost budgeting needs: what a model costs. Approval and the builder's model pickers
land on top of the same row later.

Prices are quoted the way providers quote them — per **one million tokens**, separately for
input and output, because every provider charges differently for the two. A single price, or a
budget in tokens, cannot express what a request actually costs.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class Model(models.Model):
    """A model AIRA knows about, and what it costs."""

    name = models.CharField(
        max_length=128, unique=True, help_text="Model id as the gateway exposes it"
    )
    display_name = models.CharField(max_length=255, blank=True)
    provider = models.CharField(max_length=64, blank=True)

    # Null means "no price on file": the gateway still serves the model, but its consumption is
    # counted separately so the gap is visible rather than silently costing nothing.
    input_price_per_million = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Price per 1,000,000 input tokens, in the installation currency",
    )
    output_price_per_million = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Price per 1,000,000 output tokens, in the installation currency",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_priced(self) -> bool:
        return (
            self.input_price_per_million is not None and self.output_price_per_million is not None
        )
