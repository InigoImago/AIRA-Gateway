"""What this installation's credentials put within reach (`FRD-507` stage C).

Three lists exist and they are not the same list:

    the vendor offers   — what a credential can reach.  This module.
    the gateway serves  — what an adapter is wired for.  `/v1beta/models`.
    the catalog permits — what may actually be used.     `FRD-307`.

The console had the second and the third and asked an administrator to *type* the first, which is
how `gemini-2.5-flash` came to stand in a default after Google had withdrawn it from new keys.
Asking the vendor removes the transcription; it removes nothing from the decision, because
`approved` still defaults to false and nothing in this module writes anything anywhere.

Bounded by **role**, not by use case: this describes the installation rather than anybody's
traffic, and the only people it is useful to are the ones who may declare a model.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from aira_common.roles import may_catalogue
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.upstreams.base import (
    OfferedModel,
    ProviderRegistry,
    Upstream,
    UpstreamError,
    can_enumerate,
)

router = APIRouter(tags=["providers"])


def _require_catalog_role(principal: Principal) -> None:
    """Only whoever may declare a model may ask a vendor what it offers.

    ``principal.method == "demo"`` returns early for the same reason every other role gate here
    does: a deployment that has switched authentication off has no identity to authorise, and the
    demo is not the place to invent one.
    """
    if principal.method == "demo":
        return
    if not may_catalogue(principal.roles):
        raise GeminiHTTPError(
            403,
            "Only a Global Administrator may list what a provider offers.",
            "PERMISSION_DENIED",
        )


def _grouped(registry: ProviderRegistry) -> dict[str, list[Upstream]]:
    """Adapters by the provider name they stamp on their models.

    A **list** per name, and that is the correction this module needed. A provider name does not
    identify one adapter: an EU Vertex deployment registers two — Gemini and Anthropic, one
    platform, one credential, two dialects — and both stamp ``vertex``. Keying by name and keeping
    the last would have shown the console one provider and silently described it with whichever
    adapter happened to register second, which is `ADR-0011`'s ambiguous routing table wearing a
    read-only costume.

    Grouping is also the honest answer for the reader: from a catalog author's seat "vertex" *is*
    one provider. What differs is what may be done with it, and that is reported per entry rather
    than by pretending the shape is simpler than it is.
    """
    grouped: dict[str, list[Upstream]] = {}
    for upstream in registry.each():
        name = _provider_name(upstream)
        if name:
            grouped.setdefault(name, []).append(upstream)
    return grouped


def _provider_name(upstream: Upstream) -> str:
    """What this adapter calls itself, preferring what it puts on the audit row.

    An adapter's models carry the provider name that reaches `request_logs` (`FRD-115` FR-10), so
    that is the name a console must offer — a catalog entry naming something else would produce
    rows nobody can join. An adapter with an empty configured list has no model to read it from,
    which since stage B is the normal shape, so its declared tuple answers instead.
    """
    for model in upstream.models():
        if model.provider:
            return model.provider
    declared = getattr(upstream, "provenance", None)
    if isinstance(declared, tuple) and len(declared) == 3 and declared[0]:
        return str(declared[0])
    return str(getattr(upstream, "serves_provider", "") or "")


def _provenance(upstreams: list[Upstream]) -> tuple[str, str]:
    """Publisher and region, from the first adapter that states them."""
    for upstream in upstreams:
        for model in upstream.models():
            if model.publisher or model.region:
                return (model.publisher, model.region)
        declared = getattr(upstream, "provenance", None)
        if isinstance(declared, tuple) and len(declared) == 3:
            return (str(declared[1]), str(declared[2]))
    return ("", "")


def _label(upstreams: list[Upstream]) -> str:
    """What to call this provider on a screen, from the adapter that knows.

    The *name* is an identifier: it goes in the catalog, on the audit row and into routing, and it
    has to keep being `generative-language` — which tells a reader nothing about which vendor they
    are choosing. A label map in the console would be a second vocabulary restated in TypeScript,
    the shape of drift `FRD-206` and `FRD-602` both paid for; the adapter states its own.

    Falls back to the bare name, which is exactly what was on screen before labels existed — an
    adapter that has not declared one is unlabelled, not unnamed.
    """
    for upstream in upstreams:
        label = getattr(upstream, "platform_label", "")
        if label:
            return str(label)
    return ""


def _entry(name: str, upstreams: list[Upstream], registry: ProviderRegistry) -> dict[str, Any]:
    """One provider, as somebody about to declare a model needs to see it.

    ``canEnumerate`` is **stated rather than discovered by trying**: a picker that offered every
    provider and then showed an error for the ones with no listing would report a *capability gap*
    as a *fault*, and a reader reacts to those differently — one is "ask somebody for a key", the
    other is "type the name yourself". Only a single adapter can answer for a name, because a
    listing merged from two dialects would say nothing about which one serves what.

    ``cataloguedIsEnough`` is the other half, and it is the one that decides whether an import
    produces a working model or a convincing decoration: a model name is the whole addressing only
    where an adapter owns the provider name (`FRD-507` stage B). Where it is not — Vertex, Azure —
    the model must also be named in the gateway's configuration, and saying so at the moment of
    declaring is the difference between a catalog entry and a support ticket.
    """
    single = upstreams[0] if len(upstreams) == 1 else None
    publisher, region = _provenance(upstreams)
    return {
        "name": name,
        "label": _label(upstreams) or name,
        "publisher": publisher,
        "region": region,
        "canEnumerate": single is not None and can_enumerate(single),
        "cataloguedIsEnough": name in registry.by_name(),
        "servedModels": sum(len(upstream.models()) for upstream in upstreams),
        "adapters": len(upstreams),
    }


@router.get("/v1beta/providers")
async def list_providers(
    request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    """The upstreams this gateway is configured with, and which of them can be asked for a list."""
    _require_catalog_role(principal)
    registry: ProviderRegistry = request.app.state.providers
    grouped = _grouped(registry)
    return JSONResponse(
        {
            "providers": [
                _entry(name, upstreams, registry) for name, upstreams in sorted(grouped.items())
            ]
        }
    )


def _offered_payload(model: OfferedModel) -> dict[str, Any]:
    """One vendor entry on the wire.

    Every capability travels as ``null`` where the vendor said nothing, and the console renders
    that as a question rather than as "no". Serialising ``None`` to ``false`` here would be the
    whole `FRD-114` FR-7 mistake in one line: absence of information turned into a declaration,
    arriving in a form somebody is about to save.
    """
    return {
        "name": model.name,
        "displayName": model.display_name,
        "description": model.description,
        "maxOutputTokens": model.max_output_tokens,
        "canGenerate": model.can_generate,
        "canEmbed": model.can_embed,
        "canCachePrompts": model.can_cache_prompts,
        "thinking": model.thinking,
    }


@router.get("/v1beta/providers/{name}/offerings")
async def list_offerings(
    request: Request, name: str, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    """What one provider says it offers this credential.

    Nothing here is filtered against the catalog: the console needs to know which of these it
    already has and which it does not, and that comparison belongs where the catalog is. A gateway
    that returned only the unknown ones would answer "nothing left to import" and "this credential
    reaches nothing" with the same empty list.
    """
    _require_catalog_role(principal)
    registry: ProviderRegistry = request.app.state.providers
    upstreams = _grouped(registry).get(name)
    if not upstreams:
        raise GeminiHTTPError(404, f"No provider named '{name}' is configured.", "NOT_FOUND")
    first = upstreams[0]
    if len(upstreams) > 1 or not can_enumerate(first):
        # 501, not a 404 and not an empty list. The provider exists and the question is a fair
        # one; this platform simply cannot be asked, and saying so is the difference between "your
        # credential reaches nothing" and "we have no way to ask" — which send an administrator to
        # two different systems.
        raise GeminiHTTPError(
            501,
            f"'{name}' cannot be asked which models it offers. Name its models in the gateway's "
            "configuration or in the catalog instead.",
            "UNIMPLEMENTED",
        )

    try:
        offered = await first.available_models()
    except UpstreamError as exc:
        # The upstream's own text is **not** repeated back (`FRD-506`): a provider's message can
        # carry the request URL, and for this adapter family the URL carries the key.
        raise GeminiHTTPError(
            502,
            f"'{name}' did not answer its model listing. Its credential or endpoint may be wrong.",
            "UNAVAILABLE",
        ) from exc

    return JSONResponse({"provider": name, "models": [_offered_payload(m) for m in offered]})
