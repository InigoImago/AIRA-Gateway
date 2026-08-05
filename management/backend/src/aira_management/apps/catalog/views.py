"""Model catalog API (FRD-403).

Prices are a fact about the provider contract, not a per-use-case setting, so they are
maintained centrally: every authenticated user may read the catalog (the budget views need it to
explain their figures), only a Global Administrator may change it.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
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
    }


class ModelViewSet(viewsets.ModelViewSet[Model]):
    serializer_class = ModelSerializer
    queryset = Model.objects.all()
    lookup_field = "name"
    lookup_value_regex = "[^/]+"

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
