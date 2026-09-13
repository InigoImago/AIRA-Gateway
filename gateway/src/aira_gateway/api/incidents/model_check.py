"""Can this model be reached at all? (`FRD-506`)

A catalogue entry is a declaration: it needs no credential and proves nothing. So three answers,
never collapsed into "ok":

    declared    somebody wrote it down
    served      an adapter is registered for it — what a missing credential fails
    reachable   the adapter's cheap remote question answered

**Never a generation** (`FRD-117`): a self-deployed model can be scaled to zero, and asking whether
it works must not wake it, bill for it and wait minutes to say so.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from aira_gateway.api.incidents.common import router
from aira_gateway.api.incidents.diagnostics import (
    MODEL_CHECK_TIMEOUT_SECONDS,
    _asked_upstream,
    _attribute_diagnostic,
    _record_diagnostic,
    _regions_asked,
    _require_a_checker,
)
from aira_gateway.api.serving import elapsed_ms
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.residency import RegionNotAllowed

#: Why a declared model is not served. Two causes — no credential for its platform, or a model the
#: adapter's configuration does not name (`AIRA_VERTEX_MODELS`, `AIRA_FOUNDRY_DEPLOYMENTS`) — and
#: naming only one sends people to the wrong system.
NOT_SERVED = (
    "No upstream serves this model. A declaration is metadata; reaching a model needs a "
    "provider that offers it — most often because no credential is configured for its "
    "platform, and otherwise because the catalogue entry names a provider no adapter "
    "claims. Check the provider and publisher on the model: on a platform that hosts two "
    "dialects the publisher is what selects one."
)

#: Said rather than assumed (`FRD-117`): "we did not look" and "it is fine" are different answers.
NOT_ASKED = "This upstream offers nothing cheap to ask; it was not contacted."


@router.get("/v1beta/models/{model:path}:check")
async def check_model(
    request: Request,
    model: str,
    provider: str = "",
    publisher: str = "",
    region: str = "",
    principal: Principal = Depends(require_principal),
) -> JSONResponse:
    """Whether a model is actually served, and whether its provider answers, in each region.

    ``provider``, ``publisher`` and ``region`` describe the editor the button sits in, which may
    hold unsaved values; without them the saved catalogue row is checked.
    """
    _require_a_checker(principal)
    declaration, upstream = await _asked_upstream(request, model, provider, publisher)
    asked_regions = _regions_asked(region, declaration)

    result: dict[str, Any] = {
        "model": model,
        "declared": bool(declaration and declaration.declared),
        "served": upstream is not None,
        "reachable": None,
        "detail": "",
        "regions": [],
    }
    if upstream is None:
        result["detail"] = NOT_SERVED
        return JSONResponse(result)
    ping = getattr(upstream, "ping", None)
    if ping is None:
        result["detail"] = NOT_ASKED
        return JSONResponse(result)

    # Every region, not the first: a model catalogued in several places may answer in some only
    # (`FRD-609`). `:countTokens` is free, so the regions bound nothing but time — and each probe is
    # still recorded, so "who probed this model, and when" has an answer.
    _attribute_diagnostic(request, principal)
    for asked_region in asked_regions or [""]:
        started = time.monotonic()
        verdict = await _reach(ping, model, asked_region)
        await _record_diagnostic(
            request,
            principal,
            operation="models:check",
            model=model,
            region=asked_region,
            usage=None,
            status=200 if verdict["reachable"] else 502,
            latency_ms=elapsed_ms(started),
        )
        result["regions"].append({"region": asked_region, **verdict})

    # The summary is the best region, and the detail names those that failed: a model that answers
    # anywhere will be served.
    reachable = [entry for entry in result["regions"] if entry["reachable"]]
    best = reachable[0] if reachable else result["regions"][0]
    result["reachable"] = best["reachable"]
    result["detail"] = best["detail"]
    if reachable and len(reachable) != len(result["regions"]):
        unreachable = [entry["region"] for entry in result["regions"] if not entry["reachable"]]
        result["detail"] += f" Not reachable in: {', '.join(unreachable)}."
    return JSONResponse(result)


async def _reach(ping: Any, model: str, region: str) -> dict[str, Any]:
    """Ask one region whether it has this model, and say what happened in its own words."""
    addressing = {"regions": [region]} if region else {}
    try:
        detail = await asyncio.wait_for(
            ping(model, addressing), timeout=MODEL_CHECK_TIMEOUT_SECONDS
        )
    except TimeoutError:
        return {
            "reachable": False,
            "detail": f"Did not answer within {MODEL_CHECK_TIMEOUT_SECONDS:g}s.",
        }
    except RegionNotAllowed as exc:
        # Named apart: this is fixed in `AIRA_ALLOWED_REGIONS`, never at the provider.
        return {"reachable": False, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 — anything else here means "not reachable"
        # The type, not the message: a provider's error text can carry a URL with a key in it.
        return {"reachable": False, "detail": f"Not reachable ({type(exc).__name__})."}
    return {"reachable": True, "detail": str(detail)}
