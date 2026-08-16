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
    #: The model a request **enters** this pipeline at when the caller names none (`ADR-0020`).
    #:
    #: Not a step, and that is why it is a field: it is where the pipeline *begins*, in the same way
    #: `allowed_models` is a property of the use case rather than an `allow_check` stage
    #: (`FRD-308`). Everything after it — a filter, a router, a fallback — is the pipeline's own
    #: decision, and a `model_route` step may well send the request somewhere else.
    #:
    #: Two callers need it and both were guessing:
    #:
    #: - the **question catalogue** (`FRD-504`), which now runs against a use case rather than
    #:   against a model, so nobody names one;
    #: - the **dry run**, whose `_model_the_pipeline_is_about` records three wrong guesses in a row
    #:   — the first registered model, the first released one, the first released one that can
    #:   generate — each reported back as `effective_model`, where it reads as a decision somebody
    #:   made.
    #:
    #: Blank is a valid state and means *this pipeline is only ever entered by a caller who names a
    #: model*, which is every ordinary API request. It is what makes a use case un-testable by the
    #: catalogue, and the console says so rather than picking one.
    start_model = models.CharField(max_length=128, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"pipeline for {self.use_case.slug}"
