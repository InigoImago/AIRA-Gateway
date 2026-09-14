"""Model catalog API (FRD-403, FRD-114).

Prices are a fact about the provider contract, not a per-use-case setting: every authenticated user
may read the catalog (the budget views explain their figures with it), only a Global Administrator
may change it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_common.permissions import Permission
from aira_management.apps.catalog.models import Model
from aira_management.apps.catalog.serializers import ModelSerializer
from aira_management.apps.usecases.events import emit
from aira_management.rbac import requires


def _price(value: Decimal | None) -> str | None:
    """A price as a decimal *string*: a JSON number is a float, and money must not round-trip
    through one."""
    return str(value) if value is not None else None


def _payload(model: Model) -> dict[str, Any]:
    """The `model.*` event.

    Carries everything validation needs, because the gateway never calls Management on the request
    path (FRD-114 FR-8). Null prices travel too, so removing a price removes it from the gateway.
    """
    return {
        "name": model.name,
        "display_name": model.display_name,
        "approved": model.approved,
        "provider": model.provider,
        "input_price_per_million": _price(model.input_price_per_million),
        "output_price_per_million": _price(model.output_price_per_million),
        "cached_input_price_per_million": _price(model.cached_input_price_per_million),
        "cache_write_price_per_million": _price(model.cache_write_price_per_million),
        "capabilities": list(model.capabilities or []),
        "publisher": model.publisher,
        "platform": model.platform,
        "addressing": model.addressing,
        "underlying_model": model.underlying_model,
        "context_window": model.context_window,
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

    # Deliberately **not** paged: the catalog is bounded by what an organisation has contracted,
    # and the console's warnings ("N models have no price on file") are counts over the whole of it.
    def get_queryset(self) -> QuerySet[Model]:
        return Model.objects.all().order_by("name")

    def get_permissions(self) -> list[Any]:
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAuthenticated(), requires(Permission.CATALOG_WRITE)()]

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
