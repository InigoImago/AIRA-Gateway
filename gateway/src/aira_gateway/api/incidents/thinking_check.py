"""Does the model accept each declared thinking word? (`ADR-0021`)

A level is free text the vendor takes, so a typo looks exactly like a working declaration until a
caller is refused. The provider's own refusal is the answer. `:countTokens` cannot judge (it ignores
`generationConfig`); a `generateContent` capped at one output token can — a refused word is refused
before any generation and costs nothing, an accepted one costs a token.

Informs, never blocks (`FRD-506`'s shape): the model may be briefly unreachable, and the catalogue
is Management's.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from aira_common.models import ThinkingMode
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.incidents.common import _body_of, router
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
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalUsage,
    Role,
    Thinking,
)
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.telemetry import model_call_span
from aira_gateway.upstreams.base import DialectUnsupported, UpstreamError

#: How many level words, and how many modes, one check may ask about. Each accepted word spends
#: tokens, so an unbounded list is an unbounded bill from one button press.
MAX_LEVELS_PER_CHECK = 12

#: The smallest generation a probe can be: an accepted word bills exactly one output token.
_PROBE_OUTPUT_TOKENS = 1

#: What a mode is told, answered from the dialect without asking the model.
EXPRESSIBLE = "This model's wire format can express it."
INEXPRESSIBLE = (
    "This model's wire format has no way to say '{mode}'. Declaring it here means every request "
    "that asks for it is refused — the model is never reached."
)

#: What every word is told by a dialect that has no field for a level at all.
NO_LEVEL_FIELD = (
    "This model's dialect asks for thinking by naming a token budget and has no field for a level "
    "word. Use 'limited' instead."
)


@router.post("/v1beta/models/{model:path}:checkThinking")
async def check_thinking_levels(
    request: Request,
    model: str,
    provider: str = "",
    publisher: str = "",
    region: str = "",
    principal: Principal = Depends(require_principal),
) -> JSONResponse:
    """Ask the model whether it accepts each level word, and the dialect whether it can express
    each mode.

    ``provider``, ``publisher`` and ``region`` describe the editor the button sits in, as for
    `check_model`.
    """
    _require_a_checker(principal)
    body = await _body_of(request, optional=True)
    words = _words(body, "levels")
    modes = _words(body, "modes")
    if not words and not modes:
        raise GeminiHTTPError(
            400, "Name at least one level word or mode to check.", "INVALID_ARGUMENT"
        )

    declaration, upstream = await _asked_upstream(request, model, provider, publisher)
    if upstream is None:
        raise GeminiHTTPError(
            400,
            "No upstream serves this model, so there is nobody to ask about its levels. Save the "
            "model and check its reachability first.",
            "FAILED_PRECONDITION",
        )

    # Modes are answered from the adapter's declared `thinking_modes`, for nothing: the dialect has
    # the field or it does not. A mode it cannot express turns every request asking for it into a
    # refusal. Answered before the level branch, since a dialect with no field for a word still has
    # one for `disabled`.
    expressible = _expressible_modes(upstream)
    mode_results = [
        {
            "mode": mode,
            "accepted": mode in expressible,
            "detail": EXPRESSIBLE if mode in expressible else INEXPRESSIBLE.format(mode=mode),
        }
        for mode in modes
    ]

    if not getattr(upstream, "expresses_thinking_levels", False):
        # Every word would be refused for the same reason, none of it about the model: nothing sent.
        return JSONResponse(
            {
                "model": model,
                "modes": mode_results,
                "results": [
                    {"region": "", "level": word, "accepted": False, "detail": NO_LEVEL_FIELD}
                    for word in words
                ],
            }
        )

    # Every word in every region: a vendor rolls a family out region by region, so a word accepted
    # in one can be refused in another. One output token per accepted word per region.
    asked_regions = _regions_asked(region, declaration) or [""]
    _attribute_diagnostic(request, principal)
    results: list[dict[str, Any]] = []
    for asked_region in asked_regions:
        for word in words:
            started = time.monotonic()
            verdict, usage = await _accepts(upstream, model, word, asked_region)
            await _record_diagnostic(
                request,
                principal,
                operation=f"models:checkThinking:{word}",
                model=model,
                region=asked_region,
                usage=usage,
                status=200 if verdict["accepted"] else 400,
                latency_ms=elapsed_ms(started),
            )
            results.append({"region": asked_region, "level": word, **verdict})

    return JSONResponse({"model": model, "results": results, "modes": mode_results})


def _words(body: dict[str, Any], field: str) -> list[str]:
    """The non-empty strings in ``body[field]``, lower-cased, at most `MAX_LEVELS_PER_CHECK`."""
    asked = body.get(field)
    if not isinstance(asked, list):
        return []
    return [w.strip().lower() for w in asked if isinstance(w, str) and w.strip()][
        :MAX_LEVELS_PER_CHECK
    ]


def _expressible_modes(upstream: Any) -> set[str]:
    """Which thinking modes this dialect has a field for, as the words the console sends.

    An adapter that declares nothing is taken to express everything, so the button informs rather
    than inventing red marks; the runtime refusal (`DialectUnsupported`, a 400) is the backstop.
    """
    declared = getattr(upstream, "thinking_modes", None)
    if not declared:
        return {str(mode) for mode in ThinkingMode}
    return {str(mode) for mode in declared}


async def _accepts(
    upstream: Any, model: str, word: str, region: str
) -> tuple[dict[str, Any], CanonicalUsage | None]:
    """Ask one model in one region whether it takes one level word, and what that cost.

    The usage travels with the verdict because the answer is the only place it exists.
    """
    probe = CanonicalRequest(
        model=model,
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        max_output_tokens=_PROBE_OUTPUT_TOKENS,
        thinking=Thinking(mode=word),
        addressing={"regions": [region]} if region else {},
    )
    try:
        with model_call_span(model, purpose="probe"):
            answer = await asyncio.wait_for(
                upstream.generate(probe), timeout=MODEL_CHECK_TIMEOUT_SECONDS
            )
    except TimeoutError:
        return {
            "accepted": False,
            "detail": f"Did not answer within {MODEL_CHECK_TIMEOUT_SECONDS:g}s.",
        }, None
    except RegionNotAllowed as exc:
        return {"accepted": False, "detail": str(exc)}, None
    except (UpstreamError, DialectUnsupported) as exc:
        # The provider's own words are the whole value of this button.
        return {"accepted": False, "detail": str(exc)}, None
    except Exception as exc:  # noqa: BLE001 — anything else means "we could not tell"
        # The type, not the message: an arbitrary provider error can carry a URL with a key.
        return {"accepted": False, "detail": f"Could not ask ({type(exc).__name__})."}, None
    return {"accepted": True, "detail": "The model accepted it."}, getattr(answer, "usage", None)
