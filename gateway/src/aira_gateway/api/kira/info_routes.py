"""The predecessor's informational endpoints: models, health, build info and usage (`FRD-107`).

None of them dispatches to a model. `/health` and `/version-info` are unauthenticated, as the
predecessor's are, and carry neither configuration nor catalogue — `/health` names the upstreams
only to an operator; `/models` and `/ki-usage` are authenticated.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from aira_common.models import Capability, ThinkingMode
from aira_gateway.api.kira import BASE, errors, schemas
from aira_gateway.api.kira.headers import surface_headers
from aira_gateway.api.serving import catalog_of, served_models
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.diagnostics import UpstreamProbe
from aira_gateway.reporting.service import ReportingService
from aira_gateway.routes.health import may_see_diagnosis

router = APIRouter(tags=["kira"], prefix=BASE)

#: The predecessor's word for each capability on `/models`, in the order it lists them.
CAPABILITY_WORDS = (
    (Capability.GENERATE, "Chat"),
    (Capability.EMBED, "Embedding"),
    (Capability.RERANK, "Reranker"),
)

#: What starts the predecessor's CSV, so a spreadsheet reads it as UTF-8 and not as Latin-1.
CSV_BOM = "\ufeff"


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
        capabilities = [
            word for capability, word in CAPABILITY_WORDS if declaration.can(capability)
        ]
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
    return JSONResponse([_listed(model) for model in listed], headers=surface_headers(request))


def _listed(model: schemas.KiModel) -> dict[str, Any]:
    """One entry as the predecessor writes it: fields that do not apply to the kind are left out,
    and the thinking block keeps its nulls — a bound nobody declared is ``null`` there, not absent.
    """
    entry = model.model_dump(exclude_none=True)
    if model.thinkingConfig is not None:
        entry["thinkingConfig"] = model.thinkingConfig.model_dump()
    return entry


def _thinking_config(declaration: ModelDeclaration) -> schemas.ThinkingConfig | None:
    """What `/models` says about a model's thinking.

    ``None`` when nothing is declared: an empty config would claim "thinking exists and nothing is
    allowed" instead of "nobody has said". Otherwise every setting the model takes — the control
    modes and its level words, as the predecessor lists them in one array — and always a default:
    the declared one, else ``auto``, because a request that names none leaves the model to decide,
    which is what ``auto`` means.
    """
    if not declaration.offers_thinking:
        return None
    minimum, maximum = declaration.thinking_bounds
    default = declaration.thinking_default
    return schemas.ThinkingConfig(
        mode=[
            *sorted(str(mode) for mode in declaration.thinking_modes),
            *declaration.thinking_levels,
        ],
        minTokens=minimum,
        maxTokens=maximum,
        defaultThinking=(
            schemas.ThinkingSetting(mode=str(default.get("mode")), tokens=default.get("tokens"))
            if default and default.get("mode")
            else schemas.ThinkingSetting(mode=str(ThinkingMode.AUTO))
        ),
    )


@router.get("/health")
async def health(request: Request) -> Response:
    """Gateway and upstream health as the predecessor reports it — ``Unhealthy`` also means 503.

    No I/O: upstreams are read from the background probe's cached verdict (`FRD-117` §5.2), so the
    endpoint is neither as slow as the slowest provider nor billed per call. What the contract's
    shape cannot say — that an upstream was not probed, how old a verdict is — goes in the tags.

    **The verdict is public, the upstreams are not** (`ADR-0015` §4): which servers exist, and what
    their operator called them, is listed only to whoever `/readyz` would show its diagnosis.
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
    listed = checks if await may_see_diagnosis(request) else checks[:1]
    return JSONResponse(
        schemas.HealthResponse(
            status="Healthy" if healthy else "Unhealthy",
            total_time_taken=round(time.monotonic() - started, 3),
            entities=listed,
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
    """**The caller's own** consumption in a window, one row per model (`FRD-146` FR-21).

    The predecessor's rows — `(user, model)` — narrowed to the one user asking, whatever their role.
    Another person's figures are the console's reports to show, under their own visibility rules;
    a second entry point to them would need a second copy of those rules. So nobody needs a
    permission here beyond being authenticated, and strict data mode has nothing to withhold: it
    governs who sees *another* person (`FRD-627`). The figures are the record `/v1beta/me/budget`
    already gives the same caller.

    ``Accept: text/csv`` answers the same rows as CSV with a byte-order mark, as the predecessor's
    download does, so a spreadsheet opens it as UTF-8.
    """
    start, end = _window(request)
    person = principal.person
    rows: list[dict[str, Any]] = []
    if person:
        # **Narrowed to this person, or nothing.** `person=None` is the whole installation to the
        # report, so a credential without a person answers an empty list rather than everybody.
        service: ReportingService = request.app.state.reporting
        report = await service.report(None, start, end, person=person)
        catalog = catalog_of(request)
        for figures in report["by_model"]:
            declared = await catalog.declaration(str(figures["key"]))
            rows.append(
                schemas.KiUsageRow(
                    user_id=person,
                    # 0 for a row no id names — a refusal before any model, a model since removed.
                    model_id=declared.numeric_id or 0,
                    entry_count=figures["requests"],
                    token_input_sum=figures["prompt_tokens"],
                    token_output_sum=figures["completion_tokens"],
                ).model_dump()
            )
    if "text/csv" in request.headers.get("accept", ""):
        return Response(
            _csv(rows), media_type="text/csv; charset=utf-8", headers=surface_headers(request)
        )
    return JSONResponse(rows, headers=surface_headers(request))


def _window(request: Request) -> tuple[datetime, datetime]:
    """``startDatum``/``endDatum`` as a half-open UTC window, or the predecessor's refusal."""
    start_raw = request.query_params.get("startDatum")
    end_raw = request.query_params.get("endDatum")
    for name, value in (("startDatum", start_raw), ("endDatum", end_raw)):
        if not value:
            raise errors.KiraError(400, errors.MISSING_QUERY_PARAM, f"'{name}' is required.")
    try:
        start = datetime.fromisoformat(str(start_raw))
        end = datetime.fromisoformat(str(end_raw))
    except ValueError as exc:
        # 422, the predecessor's status: its dates are typed parameters, and a value that does not
        # parse reaches its validation handler (`FRD-146` FR-15).
        raise errors.KiraError(422, errors.VALIDATION_ERROR, "Dates must be ISO-8601.") from exc
    if end <= start:
        raise errors.KiraError(
            400, errors.INVALID_TIME_RANGE, "'endDatum' must be after 'startDatum'."
        )
    return (
        start if start.tzinfo else start.replace(tzinfo=UTC),
        end if end.tzinfo else end.replace(tzinfo=UTC),
    )


def _csv(rows: list[dict[str, Any]]) -> str:
    """The rows as the predecessor's CSV: a byte-order mark, a header, one line per row."""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(schemas.KiUsageRow.model_fields), lineterminator="\r\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return CSV_BOM + buffer.getvalue()
