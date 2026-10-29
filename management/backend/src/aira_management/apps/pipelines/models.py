"""Per-use-case pipeline configuration (FRD-300/303).

One config per use case: an ordered list of ``steps`` plus a dispatch ``fallback_models`` chain.
Authored here and distributed to the gateway over Kafka; the gateway executes it pre-dispatch.
"""

from __future__ import annotations

from django.db import models

from aira_common.fallback import FallbackMode
from aira_management.apps.usecases.models import UseCase


class PipelineConfig(models.Model):
    """A use case's pre-dispatch pipeline.

    **Every use case has one**, whether or not a row exists here. A request comes in and a request
    is dispatched; the steps are what happens in between, and no steps means nothing happens — a
    configuration, not an absence. The console's graph draws the two endpoints for exactly that
    reason, and the API answers an empty pipeline rather than a 404.
    """

    use_case = models.OneToOneField(UseCase, on_delete=models.CASCADE, related_name="pipeline")
    steps = models.JSONField(default=list)
    #: The use case's own chain for text generation, used only under ``fallback_mode = own``.
    fallback_models = models.JSONField(default=list)
    #: What the use case does with the installation's fallback chains (`FRD-310`): follow them,
    #: keep its own for text generation, or have none — `aira_common.fallback.FallbackMode`.
    fallback_mode = models.CharField(
        max_length=16,
        default=FallbackMode.DEFAULT.value,
        choices=[(mode.value, mode.value) for mode in FallbackMode],
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"pipeline for {self.use_case.slug}"
