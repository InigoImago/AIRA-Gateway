"""The use case's pre-dispatch pipeline (`FRD-300`/`303`): members read it, admins replace it."""

from __future__ import annotations

from django.db import transaction
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.pipelines.serializers import PipelineConfigSerializer
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.views.base import UseCaseViewBase


class PipelineMixin(UseCaseViewBase):
    @action(detail=True, methods=["get", "put"], url_path="pipeline")
    def pipeline(self, request: Request, slug: str | None = None) -> Response:
        """Read or replace the pipeline; a saved one is published as `pipeline.upserted`."""
        usecase = self.get_object()
        config = PipelineConfig.objects.filter(use_case=usecase).first()
        if request.method == "GET":
            if config is None:
                # No row is still a pipeline — one with no steps — so it answers the serializer's
                # shape, field for field, rather than a 404.
                return Response({"steps": [], "fallback_models": []})
            return Response(PipelineConfigSerializer(config).data)

        if not self._may_manage(usecase):
            raise PermissionDenied("You cannot edit the pipeline of this use case.")
        if config is None:
            config = PipelineConfig(use_case=usecase)
        # The use case travels in the context so every model the pipeline names is checked against
        # what has been released to it (`FRD-308`).
        serializer = PipelineConfigSerializer(
            config, data=request.data, context={"use_case": usecase}
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            config = serializer.save()
            emit(
                "pipeline.upserted",
                {
                    "use_case": usecase.slug,
                    "steps": config.steps,
                    "fallback_models": config.fallback_models,
                },
            )
        return Response(PipelineConfigSerializer(config).data)
