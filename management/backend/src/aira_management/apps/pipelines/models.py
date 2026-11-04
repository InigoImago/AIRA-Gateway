"""Per-use-case pipeline configuration (FRD-300/303).

One config per use case: an ordered list of ``steps``, and a fallback chain for each kind of request
(`FRD-310`). Authored here and distributed to the gateway over Kafka; the gateway executes it
pre-dispatch.
"""

from __future__ import annotations

from django.db import models

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
    #: The models each kind of the use case's requests falls back to, in the order they are tried
    #: (`FRD-310`, `aira_common.fallback.CHAIN_FIELDS`). **Empty is the default and an answer**: a
    #: model that cannot answer is then the caller's error. Text generation, speech included:
    fallback_models = models.JSONField(default=list)
    #: Embeddings: models giving vectors of the width the model asked for gives.
    embedding_fallback_models = models.JSONField(default=list)
    #: Rankings: rerankers.
    ranking_fallback_models = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"pipeline for {self.use_case.slug}"
