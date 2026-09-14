"""Budget consumption for one use case (`FRD-402`), for the console's budget view.

Read-only, but per-use-case operational data, so the caller must be entitled to the use case
(`ADR-0007`). Entitled to *read* is wider than to *act*: an oversight role reads the per-use-case
figure it already sees in full on the reporting screen.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from aira_common.permissions import Permission
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
    # `report.read_all` reads; everybody else has to be a member (the `FRD-601` split). A
    # permission, not membership: IT Security is a member of nothing (`ADR-0007`) and must see it.
    if not principal.allows(Permission.REPORT_READ_ALL):
        authorize_use_case(principal, use_case)
    service: BudgetService = budgets_of(request)
    # A per-person budget has one figure per person. The reader is shown their own, keyed by person
    # as the gateway enforces it (`aira_gateway.scopes.person`) — never a named colleague's.
    figures = await service.usage(use_case, subject=principal.person)
    return JSONResponse({"use_case": use_case, "usage": figures})
