"""The installation's fallback chains, one per kind of request (`FRD-310`, `ADR-0048`).

Read by anybody who may read the catalogue: a use case's administrator chooses whether to follow
them, and the console shows each use case the chain its requests are served by. Written with
`catalog.write`, because a chain decides which model answers every use case that follows it — the
same kind of decision as approving a model.

**Empty is the default, and empty is an answer**: a model that cannot answer is then the caller's
error. A chain names only models that can serve its kind (`can_serve`), so an embedding never falls
back to a model that writes text.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from aira_common.fallback import FallbackKind
from aira_common.fallback import can_serve as serves
from aira_common.models import BASELINE_CAPABILITIES, parse_capabilities
from aira_common.permissions import Permission
from aira_common.pipelines import MAX_FALLBACK_MODELS, MAX_MODEL_LENGTH
from aira_management.apps.catalog.models import FallbackChain, Model
from aira_management.apps.usecases.events import emit
from aira_management.rbac import requires

#: What each kind is called where a refusal names it.
_KIND_WORDS = {
    FallbackKind.GENERATE: "text generation",
    FallbackKind.EMBED: "embedding",
    FallbackKind.RERANK: "ranking",
}


def can_serve(model: Model, kind: FallbackKind) -> bool:
    """Whether a catalogued model may stand in for a request of ``kind``.

    By its declaration, read as the gateway reads it at every hop: a model nobody declared has the
    baseline — generation and embedding — and nothing more (`FRD-114` FR-7).
    """
    capabilities = parse_capabilities(model.capabilities) or BASELINE_CAPABILITIES
    return serves(capabilities, kind)


def chains() -> dict[str, list[str]]:
    """Every kind's chain, an empty one for a kind nobody has set."""
    stored = {chain.kind: list(chain.chain or []) for chain in FallbackChain.objects.all()}
    return {kind.value: stored.get(kind.value, []) for kind in FallbackKind}


def payload(kind: FallbackKind, models: list[str]) -> dict[str, Any]:
    """The `fallback_chain.upserted` event: the kind, and the whole chain — an empty one included,
    because an empty chain is what removes the last fallback from the gateway."""
    return {"kind": kind.value, "models": list(models)}


def forget(name: str) -> None:
    """Take a model that leaves the catalogue out of every chain, and announce the chains changed.

    A chain naming a model nobody catalogues would be skipped at every hop with "no provider serves
    this model" — and shown on this console as configured.
    """
    for chain in FallbackChain.objects.all():
        kept = [model for model in chain.chain or [] if model != name]
        if kept != list(chain.chain or []):
            chain.chain = kept
            chain.save(update_fields=["chain", "updated_at"])
            emit("fallback_chain.upserted", payload(FallbackKind(chain.kind), kept))


class FallbackChainSerializer(serializers.Serializer[Any]):
    """One kind's chain, as it is written: catalogued, approved, able to serve the kind, no name
    twice, and within the bounds a use case's own chain has (`FRD-302` FR-1)."""

    models = serializers.ListField(
        child=serializers.CharField(max_length=MAX_MODEL_LENGTH, allow_blank=False),
        max_length=MAX_FALLBACK_MODELS,
        allow_empty=True,
    )

    def validate_models(self, names: list[str]) -> list[str]:
        kind: FallbackKind = self.context["kind"]
        if len(set(names)) != len(names):
            raise serializers.ValidationError("A model is named twice; each is tried once.")
        known = {model.name: model for model in Model.objects.filter(name__in=names)}
        problems: list[str] = []
        for name in names:
            model = known.get(name)
            if model is None:
                problems.append(f"'{name}' is not in the catalogue")
            elif not model.approved:
                problems.append(f"'{name}' is not approved for this installation")
            elif not can_serve(model, kind):
                problems.append(
                    f"'{name}' does not declare {_KIND_WORDS[kind]}, so it cannot answer such a "
                    "request"
                )
        if problems:
            raise serializers.ValidationError("; ".join(problems) + ".")
        return names


class FallbackChainsView(APIView):
    """`GET /fallback-chains/`: every kind's chain."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(chains())


class FallbackChainView(APIView):
    """`PUT /fallback-chains/{kind}/`: replace one kind's chain, and announce it."""

    def get_permissions(self) -> list[Any]:
        return [IsAuthenticated(), requires(Permission.CATALOG_WRITE)()]

    def put(self, request: Request, kind: str) -> Response:
        if kind not in {member.value for member in FallbackKind}:
            raise NotFound(
                f"There is no fallback chain for '{kind}': the kinds are "
                f"{', '.join(member.value for member in FallbackKind)}."
            )
        chosen = FallbackKind(kind)
        serializer = FallbackChainSerializer(data=request.data, context={"kind": chosen})
        serializer.is_valid(raise_exception=True)
        models: list[str] = serializer.validated_data["models"]
        with transaction.atomic():
            FallbackChain.objects.update_or_create(kind=chosen.value, defaults={"chain": models})
            emit("fallback_chain.upserted", payload(chosen, models))
        return Response(chains())
