"""Model catalog API (FRD-403, FRD-114).

Prices are a fact about the provider contract, not a per-use-case setting: every authenticated user
may read the catalog (the budget views explain their figures with it), only a Global Administrator
may change it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, connection, transaction
from django.db.models import QuerySet
from rest_framework import status, viewsets
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from aira_common.permissions import Permission
from aira_management.apps.catalog import reference
from aira_management.apps.catalog.models import Model
from aira_management.apps.catalog.serializers import ModelSerializer
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.views.payloads import _snapshot
from aira_management.config.runtime import get_settings
from aira_management.rbac import requires

#: The Postgres advisory lock every catalogue write holds until it commits, so two writers run one
#: after the other: the name check and the KIRA id the serializer computes then see every earlier
#: write. "AIRACAT" and a version byte, so it is recognisable in `pg_locks`.
CATALOGUE_WRITE_LOCK = 0x4149_5241_4341_5401


class CatalogueConflict(APIException):
    """Another write took the name or the KIRA id between this one's check and its insert."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = (
        "Another change to the catalogue was saved at the same moment and took this model's name "
        "or KIRA id. Nothing was saved: try again, and the catalogue answers with what is there "
        "now."
    )
    default_code = "conflict"


def _hold_catalogue_lock() -> None:
    """Wait for any other catalogue write, until this transaction ends.

    Postgres only: SQLite already lets one writer at a time hold the database.
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [CATALOGUE_WRITE_LOCK])


@contextmanager
def _one_writer() -> Iterator[None]:
    """One transaction holding the catalogue's write lock; a constraint that still fires is a
    `409` saying what happened, never a `500`."""
    try:
        with transaction.atomic():
            _hold_catalogue_lock()
            yield
    except IntegrityError as exc:
        raise CatalogueConflict() from exc


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
        "fixed_sampling": model.fixed_sampling,
        "deprecated": model.deprecated,
        "numeric_id": model.numeric_id,
    }


class ModelViewSet(viewsets.ModelViewSet[Model]):
    serializer_class = ModelSerializer
    queryset = Model.objects.all()
    lookup_field = "name"
    #: The whole rest of the path, `/` included: a name like `meta-llama/Llama-3.1-8B-Instruct` is
    #: the ordinary form on an OpenAI-compatible server, and the server decodes a percent-encoded
    #: `/` before routing, so a one-segment pattern could create such a model and never address it
    #: again. Nothing is routed below a model.
    lookup_value_regex = ".+"

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
        """Remove the model, and announce every **live** use case that had it released.

        The releases cascade here, while the gateway keeps its own copy of each use case's list:
        unannounced, a name catalogued again later would be callable there by every use case that
        once had it, and released nowhere on this console (`FRD-308`). A retired use case is not
        announced, because an upsert after its retirement would bring it back.
        """
        with transaction.atomic():
            name = instance.name
            releasing = list(instance.use_cases.filter(deleted_at__isnull=True).order_by("slug"))
            instance.delete()
            emit("model.deleted", {"name": name})
            for usecase in releasing:
                emit("usecase.upserted", _snapshot(usecase))

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Upsert by name, so re-posting a price corrects it instead of colliding.

        Under the catalogue's write lock, so a second create of the same name waits for the first
        and then corrects it, as it would have a moment later.
        """
        data = request.data if isinstance(request.data, dict) else {}
        with _one_writer():
            existing = Model.objects.filter(name=data.get("name")).first()
            if existing is None:
                return super().create(request, *args, **kwargs)
            serializer = self.get_serializer(existing, data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """A change, under the same lock: a KIRA id is checked and written as one step."""
        with _one_writer():
            return super().update(request, *args, **kwargs)


class ModelReferenceView(APIView):
    """Drafts of catalog entries from a public model list (`FRD-508`).

    For whoever writes the catalogue: it saves research, and decides nothing. Each entry is the
    model editor's fields in this catalog's vocabulary, with where it comes from and when; prices
    are the list's, in US dollars.
    """

    def get_permissions(self) -> list[Any]:
        return [IsAuthenticated(), requires(Permission.CATALOG_WRITE)()]

    def get(self, request: Request) -> Response:
        settings = get_settings()
        found = reference.current(settings.model_reference_url)
        # A rate converts only into another currency than the list's, and only where one is set.
        conversion = (
            {"from": found.currency, "to": settings.currency, "rate": rate}
            if (rate := settings.model_reference_exchange_rate)
            and settings.currency != found.currency
            else None
        )
        return Response(
            {
                "source": found.source,
                "as_of": found.as_of,
                "currency": found.currency,
                "conversion": conversion,
                "live_error": found.live_error or None,
                "entries": found.entries,
            }
        )
