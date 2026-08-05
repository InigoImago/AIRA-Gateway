"""Pipeline dry-run endpoint (FRD-306).

Evaluates a (possibly unsaved) pipeline against a sample system + user prompt and returns the
full per-step decision trace — the builder's "test this pipeline" button. It never dispatches a
generation and never touches stored data, but it *does* run the real engine, so LLM-backed steps
reach the configured providers. It therefore requires an authenticated caller (ADR-0007) and
bounds the sample text and step count so a single call cannot be turned into a free LLM relay.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.pipeline.config import Pipeline
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry

router = APIRouter(tags=["pipeline"])

# A dry-run is a builder aid, not a generation endpoint: keep the sample small.
MAX_SAMPLE_CHARS = 8_000


class DryRunRequest(BaseModel):
    system: str = Field(default="", max_length=MAX_SAMPLE_CHARS)
    user: str = Field(default="", max_length=MAX_SAMPLE_CHARS)
    model: str = Field(default="", max_length=128)
    pipeline: dict[str, Any] = {}


@router.post("/v1beta/pipeline:dryRun")
async def dry_run(
    request: Request, _principal: Principal = Depends(require_principal)
) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "Request body is not valid JSON.", "INVALID_ARGUMENT")
    try:
        payload = DryRunRequest.model_validate(body)
    except ValidationError as exc:
        return _error(400, str(exc.errors()[0].get("msg", "invalid")), "INVALID_ARGUMENT")

    registry: ProviderRegistry = request.app.state.providers
    engine: PipelineEngine = request.app.state.pipeline_engine

    models = registry.models()
    model = payload.model or (models[0].name if models else "mock-1")
    messages: list[CanonicalMessage] = []
    if payload.system:
        messages.append(CanonicalMessage(role=Role.SYSTEM, text=payload.system))
    messages.append(CanonicalMessage(role=Role.USER, text=payload.user))

    canonical = CanonicalRequest(model=model, messages=messages)
    result = await engine.dry_run(Pipeline.from_dict(payload.pipeline), canonical)

    return JSONResponse(
        {
            "blocked": result.blocked,
            "block_reason": result.block_reason,
            "effective_model": result.effective_model,
            "fallback_models": list(result.fallback_models),
            "trace": [
                {"type": entry.type, "action": entry.action, "detail": entry.detail}
                for entry in result.trace
            ],
        }
    )
