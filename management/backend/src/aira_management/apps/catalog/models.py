"""Model catalog: what a model costs, what it can do, and how it is reached (FRD-403, FRD-114).

This is the table FRD-307 foresees for the governed model catalog. It began (FRD-403) with the
attribute cost budgeting needs — what a model costs — and FRD-114 added what validation needs:
capabilities, limits, and the platform addressing behind a caller-facing name.

Two rules from `ADR-0011` shape the columns and are worth stating where they are implemented:

- **A caller names a model; the platform's addressing is configuration here** (rule 2). No use
  case's pipeline config may contain an Azure deployment name or a Vertex publisher path, or a
  vendor-side redeployment becomes a migration across every use case.
- **The price attaches to the underlying model, not to the addressing** (rule 2 again). An Azure
  deployment may be called ``production``; that string has no price, and since unpriced traffic is
  counted apart rather than as zero, getting this wrong would not fail — the spend figures would
  quietly stop being complete.

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
    approved = models.BooleanField(
        default=False,
        help_text=(
            "Only a Global Administrator may approve a model, and only an approved model may be "
            "used by a use case (FRD-307). Default off: a model appearing on an upstream is not "
            "the same event as somebody deciding it may be used here."
        ),
    )
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

    # What a prompt cache costs on this model (`FRD-133`). Two more rates rather than a multiplier,
    # because the vendors do not agree on one: Anthropic publishes 0.1x for a read and 1.25x or 2x
    # for a write depending on the lifetime, Azure publishes an absolute cached-input price, and a
    # self-hosted runtime has neither. A rate is a fact you can copy off a price list; a multiplier
    # is one somebody has to derive, and derive again per model.
    #
    # Null means "charge the ordinary input rate", which never under-bills — deliberately not
    # "free". `FRD-403`'s rule about unpriced traffic, applied one field in.
    cached_input_price_per_million = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Price per 1,000,000 input tokens served from the provider's prompt cache",
    )
    cache_write_price_per_million = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Price per 1,000,000 input tokens written into the provider's prompt cache",
    )

    # -- what it can do, and within what bounds (FRD-114) ---------------------------------

    #: Declared capabilities (`aira_common.models.Capability`). Empty means undeclared, which the
    #: gateway reads as the baseline and nothing more — absence of information is not permission.
    capabilities = models.JSONField(default=list, blank=True)

    #: Which vendor's API shape it speaks — selects the upstream dialect (`ADR-0011`).
    publisher = models.CharField(max_length=32, blank=True)
    #: Which transport reaches it (``vertex``, ``foundry``, …).
    platform = models.CharField(max_length=32, blank=True)
    #: Platform-specific addressing: a publisher path, a resource + deployment, an endpoint id.
    addressing = models.JSONField(null=True, blank=True)
    #: What the price attaches to when the addressing is not the model's real name.
    underlying_model = models.CharField(max_length=128, blank=True)

    #: How much the model can hold at once, prompt and answer together (`FRD-132` §11).
    #:
    #: Not a limit AIRA enforces — the upstream refuses what does not fit, and a second copy of
    #: that number here would be one more thing to keep true. It is here because a **client** has
    #: nowhere else to learn it: the official Gemini model resource carries it as
    #: `inputTokenLimit`, and a coding assistant shows *how full the conversation is* out of it.
    #: Left empty it simply is not published, which reads as unknown rather than as zero.
    context_window = models.PositiveIntegerField(null=True, blank=True)

    #: How much the model can hold at once, prompt and answer together (`FRD-132` §11).
    #:
    #: Not a limit AIRA enforces — the upstream refuses what does not fit, and a second copy of
    #: that number here would be one more thing to keep true. It is here because a **client** has
    #: nowhere else to learn it: the official Gemini model resource carries it as
    #: `inputTokenLimit`, and a coding assistant shows *how full the conversation is* out of it.
    #: Left empty it is simply not published, which reads as unknown rather than as zero.
    context_window = models.PositiveIntegerField(null=True, blank=True)

    max_output_tokens = models.PositiveIntegerField(null=True, blank=True)
    #: Applied when the caller sets no cap. Not polish: Anthropic **requires** ``max_tokens`` on
    #: every request (`FRD-119` §5.3), so without this a caller who omits it — most of them, since
    #: it is optional today — would get a vendor error about a field they never set.
    default_max_output_tokens = models.PositiveIntegerField(null=True, blank=True)

    #: ``{"modes": [...], "min_tokens": n, "max_tokens": n, "default": {...}, "levels": {...}}``
    thinking = models.JSONField(null=True, blank=True)
    #: ``{"task_types": [...], "supports_batch": bool, "dimensions": [...], "default": n}``
    embedding = models.JSONField(null=True, blank=True)
    #: ``{"media_types": {"application/pdf": {"tokens": n}}}`` — accepted types and their cost.
    attachments = models.JSONField(null=True, blank=True)

    hosting = models.CharField(max_length=16, blank=True)

    #: Warns, never blocks. Blocking is what FRD-307's revocation is for, and conflating the two
    #: removes the ability to announce a retirement before performing one.
    deprecated = models.BooleanField(default=False)

    #: A stable integer alias, for the predecessor's ``model_id`` (`FRD-107`). Unused unless that
    #: surface is built, and it should be removed with it rather than left to read as meaningful.
    numeric_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_declared(self) -> bool:
        """Whether anyone has said what this model can do. Shown in the UI the same way an
        unpriced model is: visibly incomplete rather than silently absent."""
        return bool(self.capabilities)

    @property
    def is_priced(self) -> bool:
        return (
            self.input_price_per_million is not None and self.output_price_per_million is not None
        )
