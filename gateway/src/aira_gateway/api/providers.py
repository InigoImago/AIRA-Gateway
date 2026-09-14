"""What this installation's credentials put within reach (`FRD-507` stage C).

Three lists, and they are not the same list:

    the vendor offers   — what a credential can reach.  This module.
    the gateway serves  — what an adapter is wired for.  `/v1beta/models`.
    the catalog permits — what may actually be used.     `FRD-307`.

Asking the vendor removes the transcription, not the decision: `approved` still defaults to false
and nothing here writes anything. Bounded by **role**: it describes the installation, and only
whoever may declare a model needs it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from aira_common.permissions import Permission
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.residency import parse_allowed
from aira_gateway.state import providers_of, settings_of
from aira_gateway.upstreams.base import (
    OfferedModel,
    ProviderRegistry,
    Upstream,
    UpstreamError,
    can_enumerate,
)

router = APIRouter(tags=["providers"])


def _require_catalog_role(principal: Principal) -> None:
    """Only whoever may declare a model may ask a vendor what it offers."""
    if principal.method == "demo":
        # Authentication is switched off: there is no identity to authorise.
        return
    if not principal.allows(Permission.CATALOG_WRITE):
        raise GeminiHTTPError(
            403,
            "Listing what a provider offers needs the permission to write the catalogue "
            "(catalog.write).",
            "PERMISSION_DENIED",
        )


def _grouped(registry: ProviderRegistry) -> dict[str, list[Upstream]]:
    """Adapters by the provider name they stamp on their models — a **list** per name.

    One name can mean several adapters: an EU Vertex deployment registers Gemini and Anthropic, one
    platform and credential, two dialects, both stamped ``vertex`` (`ADR-0011`). Keeping only the
    last would describe the provider with whichever registered second.
    """
    grouped: dict[str, list[Upstream]] = {}
    for upstream in registry.each():
        name = _provider_name(upstream)
        if name:
            grouped.setdefault(name, []).append(upstream)
    return grouped


def _provider_name(upstream: Upstream) -> str:
    """What this adapter calls itself: the provider on its models' audit rows (`FRD-115` FR-10),
    otherwise its declared provenance — an adapter with no configured model is a normal shape."""
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
    """What to call this provider on a screen, as the adapter states it; empty where none does.

    The *name* is an identifier (`generative-language`) that tells a reader nothing, and a label map
    in the console would be a second vocabulary to keep in step.
    """
    for upstream in upstreams:
        label = getattr(upstream, "platform_label", "")
        if label:
            return str(label)
    return ""


def _entry(name: str, upstreams: list[Upstream], registry: ProviderRegistry) -> dict[str, Any]:
    """One provider, as somebody about to declare a model needs to see it.

    - ``canEnumerate`` is stated rather than discovered by trying, so a missing listing reads as a
      capability gap rather than a fault. Only a single adapter can answer for a name.
    - ``cataloguedIsEnough``: whether a model name is the whole addressing, i.e. an adapter owns the
      provider name (`FRD-507` stage B). Elsewhere — Vertex, Azure — the model must also be named
      in the gateway's configuration.
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


def _offered_payload(model: OfferedModel) -> dict[str, Any]:
    """One vendor entry on the wire. A capability the vendor did not state travels as ``null`` —
    never ``false`` — so the console asks rather than declares (`FRD-114` FR-7)."""
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


@router.get("/v1beta/providers")
async def list_providers(
    request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    """The upstreams this gateway is configured with, which can be asked for a list — and where
    this installation permits processing.

    ``allowedRegions`` rides along so the console can refuse an unpermitted region when a model is
    catalogued, reading the gateway's policy rather than holding a copy (`ADR-0012` §6).
    """
    _require_catalog_role(principal)
    registry = providers_of(request)
    grouped = _grouped(registry)
    settings = settings_of(request)
    return JSONResponse(
        {
            "providers": [
                _entry(name, upstreams, registry) for name, upstreams in sorted(grouped.items())
            ],
            "allowedRegions": sorted(parse_allowed(settings.allowed_regions)),
        }
    )


@router.get("/v1beta/providers/{name}/offerings")
async def list_offerings(
    request: Request, name: str, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    """What one provider says it offers this credential.

    Unfiltered against the catalog: the console makes that comparison, and filtering here would
    answer "nothing left to import" and "this credential reaches nothing" with the same empty list.
    """
    _require_catalog_role(principal)
    upstreams = _grouped(providers_of(request)).get(name)
    if not upstreams:
        raise GeminiHTTPError(404, f"No provider named '{name}' is configured.", "NOT_FOUND")
    first = upstreams[0]
    if len(upstreams) > 1 or not can_enumerate(first):
        # 501, not a 404 or an empty list: "we have no way to ask" sends an administrator somewhere
        # other than "your credential reaches nothing".
        raise GeminiHTTPError(
            501,
            f"'{name}' cannot be asked which models it offers. Name its models in the gateway's "
            "configuration or in the catalog instead.",
            "UNIMPLEMENTED",
        )

    try:
        offered = await first.available_models()
    except UpstreamError as exc:
        # The upstream's own text is not repeated (`FRD-506`): it can carry the request URL, and
        # for this adapter family the URL carries the key.
        raise GeminiHTTPError(
            502,
            f"'{name}' did not answer its model listing. Its credential or endpoint may be wrong.",
            "UNAVAILABLE",
        ) from exc

    return JSONResponse({"provider": name, "models": [_offered_payload(m) for m in offered]})
