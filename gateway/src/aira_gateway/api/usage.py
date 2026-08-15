"""Budget usage endpoint (FRD-402).

Returns current-period consumption per budget for a use case, for the management UI's budget
view. Read-only — no dispatch, no payload data — but it *is* per-use-case operational data, so
it requires an authenticated caller who is entitled to that use case (ADR-0007).

**Entitled** is wider here than it is for *acting*. `authorize_use_case` asks for membership,
which is exactly right before spending somebody's budget and wrong before reading a figure an
oversight role can already see in full on the reporting screen. Refusing the per-use-case number
to somebody who is shown the total was not a stricter rule, it was an inconsistent one — and in
the console it appeared as "consumption is hidden" on every budget tab, for every role.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from aira_gateway.auth.dependencies import (
    authorize_use_case,
    require_principal,
    require_valid_use_case,
)
from aira_gateway.auth.principal import Principal
from aira_gateway.budgets.service import BudgetService
from aira_gateway.state import budgets_of

router = APIRouter(tags=["usage"])


@router.get("/v1beta/usage/{use_case}")
async def usage(
    use_case: str, request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    require_valid_use_case(use_case)
    # Oversight reads; everybody else has to be a member. The same split `FRD-601` makes for the
    # spend report, applied to the figure that report is made of.
    if not principal.is_governance:
        authorize_use_case(principal, use_case)
    service: BudgetService = budgets_of(request)
    # A per-person budget has one figure per person, so the answer depends on who is asking. The
    # reader's own subject is the only one they may be shown: reporting somebody else's here would
    # make a consumption bar a way of watching a named colleague, which no role asked for and the
    # requests view (`FRD-505`) grants deliberately and records.
    # The person, so the figure this endpoint reports is the one the gateway enforces
    # against (`aira_gateway.scopes.person`). Reading it by subject would have shown a
    # signed-in reader an empty allowance while their key's traffic filled the same pot.
    figures = await service.usage(use_case, subject=principal.person)
    return JSONResponse({"use_case": use_case, "usage": figures})
