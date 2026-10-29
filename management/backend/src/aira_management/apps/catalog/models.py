"""Model catalog: what a model costs, what it can do, and how it is reached (FRD-403, FRD-114).

Two rules from `ADR-0011` shape the columns:

- **A caller names a model; the platform's addressing is configuration here.** No pipeline config
  may contain an Azure deployment name or a Vertex publisher path, or a vendor-side redeployment
  becomes a migration across every use case.
- **The price attaches to the underlying model, not to the addressing.** An Azure deployment
  called ``production`` has no price, and unpriced traffic is counted apart rather than as zero —
  so getting this wrong would quietly make the spend figures incomplete.

Prices are per **one million tokens**, separately for input and output, the way providers quote
them.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from aira_common.fallback import FallbackKind


class Model(models.Model):
    """A model AIRA knows about, and what it costs."""

    name = models.CharField(
        max_length=128, unique=True, help_text="Model id as the gateway exposes it"
    )
    display_name = models.CharField(max_length=255, blank=True)
    approved = models.BooleanField(
        default=False,
        help_text=(
            "Approving a model takes the permission to write the catalogue (catalog.write), and "
            "only an approved model may be used by a use case (FRD-307). Default off: a model "
            "appearing on an upstream is not the same event as somebody deciding it may be used "
            "here."
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

    # What a prompt cache costs (`FRD-133`): rates rather than a multiplier, because vendors quote
    # them differently and a rate can be copied off a price list. Null is never "free": a read is
    # then charged at the ordinary input rate and a write at 1.25x it, Anthropic's rate — the one
    # provider that reports writes (`FRD-408`).
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

    # What a request costs whose prompt is longer than a stated length (`FRD-408`): Google and
    # Anthropic bill such a request — every token of it, the answer too — at a second set of rates,
    # 200,000 tokens on every model that has one. Null throughout for a model priced alike at every
    # length. The same rules as the ordinary prices: input and output together or neither, and an
    # absent cache price read the same way.
    long_context_threshold = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="The longest prompt, in tokens, the ordinary prices apply to",
    )
    long_context_input_price_per_million = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Price per 1,000,000 input tokens of a request whose prompt is longer",
    )
    long_context_output_price_per_million = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Price per 1,000,000 output tokens of a request whose prompt is longer",
    )
    long_context_cached_input_price_per_million = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Price per 1,000,000 cached input tokens of a request whose prompt is longer",
    )
    long_context_cache_write_price_per_million = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Price per 1,000,000 tokens written into the cache by a request whose prompt is "
        "longer",
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
    #: Not enforced here — the upstream refuses what does not fit. Published because a client has
    #: nowhere else to learn it (Gemini's `inputTokenLimit`). Empty reads as unknown, not zero.
    context_window = models.PositiveIntegerField(null=True, blank=True)

    max_output_tokens = models.PositiveIntegerField(null=True, blank=True)
    #: Applied when the caller sets no cap. Anthropic **requires** ``max_tokens`` on every request
    #: (`FRD-119` §5.3), so without it a caller who omits one gets a vendor error.
    default_max_output_tokens = models.PositiveIntegerField(null=True, blank=True)

    #: ``{"modes": [...], "min_tokens": n, "max_tokens": n, "default": {...}, "levels": {...}}``
    thinking = models.JSONField(null=True, blank=True)
    #: ``{"task_types": [...], "supports_batch": bool, "dimensions": [...], "default": n}``
    embedding = models.JSONField(null=True, blank=True)
    #: ``{"media_types": {"application/pdf": {"tokens": n}}}`` — accepted types and their cost.
    attachments = models.JSONField(null=True, blank=True)

    hosting = models.CharField(max_length=16, blank=True)

    #: The model samples at its own settings: a request that sets temperature, top_p or top_k to
    #: anything but the neutral value is not sent to it, because the vendor refuses it with a 400
    #: (Anthropic's newer Claude models). False is no restriction, so a row or an event that never
    #: names it keeps today's behaviour.
    fixed_sampling = models.BooleanField(default=False)

    #: ``{"top_p": x, "top_k": n}``, either or both: applied where the caller sets that control
    #: itself not at all (`FRD-146` FR-20). Empty means the model's own defaults — nothing is set
    #: on the caller's behalf unless an administrator decided it here.
    default_sampling = models.JSONField(null=True, blank=True)

    #: Warns, never blocks — blocking is FRD-307's revocation, so a retirement can be announced
    #: before it is performed.
    deprecated = models.BooleanField(default=False)

    #: A stable integer alias, for the KIRA surface's ``model_id`` (`FRD-107`).
    numeric_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_declared(self) -> bool:
        """Whether anyone has said what this model can do — shown as visibly incomplete, like an
        unpriced model."""
        return bool(self.capabilities)

    @property
    def is_priced(self) -> bool:
        return (
            self.input_price_per_million is not None and self.output_price_per_million is not None
        )


class FallbackChain(models.Model):
    """The installation's fallback chain for one kind of request (`FRD-310`, `ADR-0048`).

    One row per kind, and no row is an empty chain: a model that cannot answer is then the
    caller's error, which is the default. A use case follows these chains unless it keeps its own
    for text generation or refuses fallback altogether (`pipelines.PipelineConfig.fallback_mode`),
    and a request reaches only the models of a chain its use case has been released.
    """

    kind = models.CharField(
        max_length=16,
        unique=True,
        choices=[(kind.value, kind.value) for kind in FallbackKind],
    )
    #: Model names, in the order they are tried, each catalogued and able to serve ``kind``.
    chain = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind"]

    def __str__(self) -> str:
        return f"fallback for {self.kind}"
