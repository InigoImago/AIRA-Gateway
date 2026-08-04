"""Budget usage endpoint (FRD-402).

Returns current-period consumption per budget for a use case, for the management UI's budget
view. Read-only, no dispatch or payload data — exposed as an unauthenticated builder utility
(same posture as the pipeline dry-run).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aira_gateway.budgets.service import BudgetService

router = APIRouter(tags=["usage"])


@router.get("/v1beta/usage/{use_case}")
async def usage(use_case: str, request: Request) -> JSONResponse:
    service: BudgetService = request.app.state.budgets
    return JSONResponse({"use_case": use_case, "usage": await service.usage(use_case)})
