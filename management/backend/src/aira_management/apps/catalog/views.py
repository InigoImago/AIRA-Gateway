"""Model catalog API (FRD-403, FRD-114).

Prices are a fact about the provider contract, not a per-use-case setting, so they are
maintained centrally: every authenticated user may read the catalog (the budget views need it to
explain their figures), only a Global Administrator may change it.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_management.apps.catalog.models import Model
from aira_management.apps.catalog.serializers import ModelSerializer
from aira_management.apps.usecases.events import emit
from aira_management.rbac import IsGlobalAdmin


def _payload(model: Model) -> dict[str, Any]:
    """Event payload. Prices travel as decimal *strings*: JSON numbers are floats, and a price
    that survives the trip only approximately would produce costs nobody can reconcile."""
    return {
        "name": model.name,
        "display_name": model.display_name,
        "approved": model.approved,
        "provider": model.provider,
        "input_price_per_million": (
            str(model.input_price_per_million)
            if model.input_price_per_million is not None
            else None
        ),
        "output_price_per_million": (
            str(model.output_price_per_million)
            if model.output_price_per_million is not None
            else None
        ),
        # `FRD-133`. Carried even when null, so an operator removing a cache price takes it off the
        # gateway too — an event that omits a field the consumer would otherwise leave standing is
        # how a deleted price keeps being charged.
        "cached_input_price_per_million": (
            str(model.cached_input_price_per_million)
            if model.cached_input_price_per_million is not None
            else None
        ),
        "cache_write_price_per_million": (
            str(model.cache_write_price_per_million)
            if model.cache_write_price_per_million is not None
            else None
        ),
        # FRD-114. The gateway reads its own read-model on the request path and never calls
        # Management (FR-8), so everything validation needs has to travel with the event.
        "capabilities": list(model.capabilities or []),
        "publisher": model.publisher,
        "platform": model.platform,
        "addressing": model.addressing,
        "underlying_model": model.underlying_model,
        "max_output_tokens": model.max_output_tokens,
        "default_max_output_tokens": model.default_max_output_tokens,
        "thinking": model.thinking,
        "embedding": model.embedding,
        "attachments": model.attachments,
        "hosting": model.hosting,
        "deprecated": model.deprecated,
        "numeric_id": model.numeric_id,
    }


class ModelViewSet(viewsets.ModelViewSet[Model]):
    serializer_class = ModelSerializer
    queryset = Model.objects.all()
    lookup_field = "name"
    lookup_value_regex = "[^/]+"

    # Deliberately **not** paged, and this is the decision rather than an omission.
    #
    # The catalog is bounded by how many models an organisation has contracted — tens, not
    # thousands — and two of the console's warnings ("N models have no price on file", "N have no
    # capability declaration") are counts over the *whole* catalog. Paging it would turn those into
    # "N on this page", which is a figure that means nothing. The console searches and pages it in
    # the browser instead, which it can honestly do because it has the whole thing.
    #
    # `/api/v1/use-cases/` is the opposite case and is paged at the server: unbounded, and its
    # serializer computes object-level permissions per row.
    def get_queryset(self) -> QuerySet[Model]:
        return Model.objects.all().order_by("name")

    def get_permissions(self) -> list[Any]:
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsGlobalAdmin()]

    def perform_create(self, serializer: Any) -> None:
        with transaction.atomic():
            model = serializer.save()
            emit("model.upserted", _payload(model))

    def perform_update(self, serializer: Any) -> None:
        with transaction.atomic():
            model = serializer.save()
            emit("model.upserted", _payload(model))

    def perform_destroy(self, instance: Model) -> None:
        with transaction.atomic():
            name = instance.name
            instance.delete()
            emit("model.deleted", {"name": name})

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Upsert by name, so re-posting a price corrects it instead of colliding."""
        data = request.data if isinstance(request.data, dict) else {}
        existing = Model.objects.filter(name=data.get("name")).first()
        if existing is None:
            return super().create(request, *args, **kwargs)
        serializer = self.get_serializer(existing, data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data, status=status.HTTP_200_OK)
