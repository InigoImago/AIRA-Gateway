"""Budget usage endpoint (FRD-402).

Returns current-period consumption per budget for a use case, for the management UI's budget
view. Read-only — no dispatch, no payload data — but it *is* per-use-case operational data, so
it requires an authenticated caller who is entitled to that use case (ADR-0007).
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

router = APIRouter(tags=["usage"])


@router.get("/v1beta/usage/{use_case}")
async def usage(
    use_case: str, request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    require_valid_use_case(use_case)
    authorize_use_case(principal, use_case)
    service: BudgetService = request.app.state.budgets
    return JSONResponse({"use_case": use_case, "usage": await service.usage(use_case)})
