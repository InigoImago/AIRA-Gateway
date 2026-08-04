"""Per-use-case pipeline configuration (FRD-300/303).

One config per use case: an ordered list of ``steps`` plus a dispatch ``fallback_models`` chain.
Authored here and distributed to the gateway over Kafka; the gateway executes it pre-dispatch.
"""

from __future__ import annotations

from django.db import models

from aira_management.apps.usecases.models import UseCase


class PipelineConfig(models.Model):
    use_case = models.OneToOneField(UseCase, on_delete=models.CASCADE, related_name="pipeline")
    steps = models.JSONField(default=list)
    fallback_models = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"pipeline for {self.use_case.slug}"
