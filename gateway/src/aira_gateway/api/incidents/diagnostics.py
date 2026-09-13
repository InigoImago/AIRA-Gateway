"""What both model checks share: who may press them, what they ask, and the row each probe leaves.

A check describes the *installation*, so it is bounded by role and belongs to no use case. Every
probe is still recorded (`FRD-610`): these calls can spend money, and "what did this cost" must be
answerable.
"""

from __future__ import annotations

import structlog
from fastapi import Request

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.serving import catalog_of
from aira_gateway.audit import Outcome
from aira_gateway.auth.attribution import Attribution, attribute
from aira_gateway.auth.principal import Principal
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.persistence.recorder import record_request
from aira_gateway.state import pricing_of, providers_of
from aira_gateway.upstreams.base import Upstream

#: A check must be quick enough that somebody presses the button and waits for it.
MODEL_CHECK_TIMEOUT_SECONDS = 5.0

_log = structlog.get_logger(__name__)


def _require_a_checker(principal: Principal) -> None:
    """Only the roles that investigate an installation may ask a model about itself."""
    if not principal.may_act_on_incidents:
        raise GeminiHTTPError(
            403,
            "Checking a model is available to IT Security and Global Administrators.",
            "PERMISSION_DENIED",
        )


async def _asked_upstream(
    request: Request, model: str, provider: str, publisher: str
) -> tuple[ModelDeclaration, Upstream | None]:
    """The model's declaration, and the adapter serving the provenance the caller asked about.

    The caller's ``provider`` and ``publisher`` win over the saved row: the console's buttons sit in
    an editor whose provenance may not be saved yet. Both, because one platform hosts several
    dialects and the publisher selects one.
    """
    declaration = await catalog_of(request).declaration(model)
    asked_provider = provider or declaration.provider
    asked_publisher = publisher or declaration.publisher
    upstream = providers_of(request).provider_for(model, asked_provider, asked_publisher)
    return declaration, upstream


def _regions_asked(region: str, declaration: ModelDeclaration) -> list[str]:
    """Which regions to check: the form's, where it named any, otherwise the catalogue's."""
    if region:
        return [part.strip() for part in region.split(",") if part.strip()]
    return list(declaration.regions)


def _attribute_diagnostic(request: Request, principal: Principal) -> None:
    """Attribute the check to the person who pressed it, and to **no use case**.

    Set here rather than by widening `require_principal`, which would make every diagnostic look
    like a use case's request.
    """
    attribute(
        request,
        Attribution(
            subject=principal.subject,
            method=principal.method,
            use_case=None,
            credential=principal.credential,
            username=principal.username,
        ),
    )


async def _record_diagnostic(
    request: Request,
    principal: Principal,
    *,
    operation: str,
    model: str,
    region: str,
    usage: CanonicalUsage | None,
    status: int,
    latency_ms: int,
) -> None:
    """Leave an audit row for a probe somebody pressed (`FRD-610`).

    - **No use case, and none invented**: a check exists for models released to nobody yet
      (`FRD-403`).
    - **`Outcome.DIAGNOSTIC`, not `served`**, so request figures do not count traffic no use case
      made (`FRD-125b`).
    - **The real usage and price**, from the answer and the catalogue.

    Never raises: a failed write is logged and the verdict still returns.
    """
    try:
        cost = await pricing_of(request).cost_nanos(model, usage) if usage else None
        await record_request(
            request,
            operation=operation,
            model=model,
            status=status,
            usage=usage,
            latency_ms=latency_ms,
            request_payload=None,
            response_payload=None,
            cost_nanos=cost,
            outcome=Outcome.DIAGNOSTIC,
            provenance=("", "", region),
            api="console",
        )
    except Exception:  # noqa: BLE001 — the verdict matters more than its bookkeeping
        _log.warning(
            "diagnostic_not_recorded",
            model=model,
            operation=operation,
            subject=principal.subject,
        )
