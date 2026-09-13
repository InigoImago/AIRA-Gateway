"""The predecessor's informational endpoints: models, health, build info and usage (`FRD-107`).

None of them dispatches to a model. `/health` and `/version-info` are unauthenticated, as the
predecessor's are, and carry neither configuration nor catalogue; `/models` and `/ki-usage` are
authenticated.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from aira_common.models import Capability
from aira_gateway.api.kira import BASE, errors, schemas
from aira_gateway.api.kira.headers import surface_headers
from aira_gateway.api.serving import catalog_of, served_models
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.diagnostics import UpstreamProbe
from aira_gateway.reporting.service import ReportingService

router = APIRouter(tags=["kira"], prefix=BASE)


@router.get("/models")
async def models(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    """The catalogued models, in the predecessor's shape.

    Authenticated, unlike the predecessor's (`FRD-107` §5.5): which models an organisation approved,
    and their limits, are not public. Thinking and embedding options are listed too, because a
    client reads this list to decide what to ask for.
    """
    del principal
    catalog = catalog_of(request)
    listed: list[schemas.KiModel] = []
    for described in await served_models(request):
        declaration = await catalog.declaration(described.name)
        if declaration.numeric_id is None:
            # A KIRA client addresses models by id; without one it cannot call this model.
            continue
        capabilities = []
        if declaration.can(Capability.GENERATE):
            capabilities.append("CHAT")
        if declaration.can(Capability.EMBED):
            capabilities.append("EMBEDDING")
        listed.append(
            schemas.KiModel(
                id=declaration.numeric_id,
                name=described.name,
                provider=(declaration.publisher or described.publisher or "").capitalize(),
                capabilities=capabilities,
                deprecated=declaration.deprecated,
                max_output_tokens=declaration.max_output_tokens,
                thinkingConfig=_thinking_config(declaration),
                embedding_dimensions=declaration.default_dimensions,
                task_types=sorted(declaration.embedding_task_types) or None,
                supports_aggregation=(
                    declaration.supports_batch if declaration.can(Capability.EMBED) else None
                ),
            )
        )
    return JSONResponse(
        [model.model_dump(exclude_none=True) for model in listed], headers=surface_headers(request)
    )


def _thinking_config(declaration: ModelDeclaration) -> schemas.ThinkingConfig | None:
    """What `/models` says about a model's thinking.

    ``None`` when nothing is declared: an empty config would claim "thinking exists and nothing is
    allowed" instead of "nobody has said".
    """
    modes = declaration.thinking_modes
    if not modes:
        return None
    minimum, maximum = declaration.thinking_bounds
    default = declaration.thinking_default
    return schemas.ThinkingConfig(
        mode=sorted(str(mode) for mode in modes),
        minTokens=minimum,
        maxTokens=maximum,
        defaultThinking=(
            schemas.ThinkingSetting(mode=str(default.get("mode")), tokens=default.get("tokens"))
            if default and default.get("mode")
            else None
        ),
    )


@router.get("/health")
async def health(request: Request) -> Response:
    """Gateway and upstream health as the predecessor reports it — ``Unhealthy`` also means 503.

    No I/O: upstreams are read from the background probe's cached verdict (`FRD-117` §5.2), so the
    endpoint is neither as slow as the slowest provider nor billed per call. What the contract's
    shape cannot say — that an upstream was not probed, how old a verdict is — goes in the tags.
    """
    started = time.monotonic()
    checks = [
        schemas.HealthCheck(service="Gateway", status="Healthy", time_taken=0.0, tags=["aira"])
    ]
    probe: UpstreamProbe | None = getattr(request.app.state, "upstream_probe", None)
    for name, verdict in (probe.snapshot() if probe is not None else {}).items():
        took = verdict.get("took_seconds")
        checks.append(
            schemas.HealthCheck(
                service=name,
                status=(
                    "Healthy"
                    if bool(verdict.get("ok", False)) and not verdict.get("stale", False)
                    else "Unhealthy"
                ),
                # The last probe's own duration; 0.0 where nothing was probed (the tags say so).
                time_taken=float(took) if isinstance(took, int | float) else 0.0,
                tags=_upstream_tags(verdict),
            )
        )

    # A monitor reads the status code long before the body, so unhealthy is a 503 as well.
    healthy = all(check.status == "Healthy" for check in checks)
    return JSONResponse(
        schemas.HealthResponse(
            status="Healthy" if healthy else "Unhealthy",
            total_time_taken=round(time.monotonic() - started, 3),
            entities=checks,
        ).model_dump(),
        status_code=200 if healthy else 503,
        headers=surface_headers(request),
    )


def _upstream_tags(verdict: dict[str, object]) -> list[str]:
    """What this check is and how fresh its answer is: ``not-probed``, ``cached:<n>s``, ``stale``.

    Tags, because a compatibility surface does not get to add fields to the contract's shape.
    """
    tags = ["upstream"]
    if not verdict.get("probed", True):
        tags.append("not-probed")
        return tags
    age = verdict.get("age_seconds")
    if isinstance(age, int | float):
        tags.append(f"cached:{int(age)}s")
    if verdict.get("stale", False):
        tags.append("stale")
    return tags


@router.get("/version-info")
async def version_info(request: Request) -> Response:
    """Build metadata, or nulls: a development run has no build number, which is not an error."""
    settings = request.app.state.settings
    info = schemas.VersionInfo(
        buildNumber=getattr(settings, "build_number", None) or None,
        buildTime=getattr(settings, "build_time", "") or None,
        git=schemas.GitInfo(
            commit=getattr(settings, "git_commit", "") or None,
            commitShort=(getattr(settings, "git_commit", "") or "")[:7] or None,
            branch=getattr(settings, "git_branch", "") or None,
            stage=settings.environment,
        ),
    )
    return JSONResponse(info.model_dump(), headers=surface_headers(request))


@router.get("/ki-usage")
async def ki_usage(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    """Token consumption per user, from `FRD-601`'s report.

    For oversight roles (`is_oversight`), the same visibility rule reporting applies — a second
    entry point to the same data must not have a second rule (`FRD-602` §5.3).
    """
    if not principal.is_oversight:
        raise errors.KiraError(
            403, errors.ADMIN_PERMISSION_REQUIRED, "This endpoint requires an oversight role."
        )

    start_raw = request.query_params.get("startDatum")
    end_raw = request.query_params.get("endDatum")
    for name, value in (("startDatum", start_raw), ("endDatum", end_raw)):
        if not value:
            raise errors.KiraError(400, errors.MISSING_QUERY_PARAM, f"'{name}' is required.")
    try:
        start = datetime.fromisoformat(str(start_raw))
        end = datetime.fromisoformat(str(end_raw))
    except ValueError as exc:
        raise errors.KiraError(400, errors.VALIDATION_ERROR, "Dates must be ISO-8601.") from exc
    if end <= start:
        raise errors.KiraError(
            400, errors.INVALID_TIME_RANGE, "'endDatum' must be after 'startDatum'."
        )

    service: ReportingService = request.app.state.reporting
    report = await service.report(
        None,
        start if start.tzinfo else start.replace(tzinfo=UTC),
        end if end.tzinfo else end.replace(tzinfo=UTC),
    )

    # The predecessor keys usage by (user, model); the report aggregates the two separately. Rows
    # are therefore per user with model id 0, rather than a fabricated cross-tabulation.
    rows: list[dict[str, Any]] = [
        schemas.KiUsageRow(
            user_id=member["key"],
            model_id=0,
            entry_count=member["requests"],
            token_input_sum=member["prompt_tokens"],
            token_output_sum=member["completion_tokens"],
        ).model_dump()
        for member in report["by_member"]
    ]
    return JSONResponse(rows, headers=surface_headers(request))
