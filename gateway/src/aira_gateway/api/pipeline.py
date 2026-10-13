"""Pipeline dry-run endpoint (FRD-306).

Evaluates a (possibly unsaved) pipeline against a sample system + user prompt and returns the full
per-step trace — the builder's "test this pipeline" button. It never dispatches the caller's own
generation, but it **does** run the real engine, so an LLM-backed step reaches a real provider and
spends real tokens. So it takes the rules every spending request takes:

- the caller names a **use case** and must be allowed to act on it (`use_case_refusal`, the same
  function both surfaces use, so a selector never grants access);
- **every model the pipeline names** must be released to that use case (`FRD-308`), and every model
  it will call must be approved for the installation (`FRD-307`);
- the controls that stop spending — suspension, rate limits, exhausted budget — are taken before any
  step runs, and every call a step makes is audited and billed (`FRD-125b`).

**Trying a pipeline is authoring one** (`FRD-306`). A pipeline's steps send the caller's text to a
model under an instruction the pipeline carries, and the trace shows what the model said. So an
unsaved pipeline — the caller's own instructions — and steps run past a block, which production
never reaches, are for whoever may save a pipeline on the use case: its administrators. A member
may still test the **saved** pipeline, the one their own requests go through, and is shown what
each step decided but not the words its model produced: production never shows those either, and
a redactor coaxed into answering would otherwise answer a prompt the next step refuses.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from aira_common.access import GrantRole
from aira_common.permissions import Permission
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.api.gemini.refusals import refusal_response as _refusal
from aira_gateway.api.serving import (
    REFUSALS,
    catalog_of,
    declared_model,
    guard_before_work,
    json_body,
    record_pipeline_calls,
    released_for,
)
from aira_gateway.audit import AuditTrail
from aira_gateway.auth.attribution import Attribution, attribute
from aira_gateway.auth.dependencies import require_principal, use_case_refusal
from aira_gateway.auth.principal import Principal
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.payloads import grant_role_in
from aira_gateway.pipeline.config import Pipeline, problem_in
from aira_gateway.pipeline.engine import DryRunResult, PipelineEngine
from aira_gateway.pipeline.store import PipelineStore
from aira_gateway.requirements import ModelApproved
from aira_gateway.state import providers_of, sessionmaker_of
from aira_gateway.upstreams.base import ProviderRegistry

router = APIRouter(tags=["pipeline"])

#: A dry run is a builder aid, not a generation endpoint: keep the sample small.
MAX_SAMPLE_CHARS = 8_000

#: What a step's trace detail holds that a model wrote: a classifier's reply and a redactor's
#: rewrite. Shown to those who may author the pipeline, withheld from everyone else.
MODEL_WORDS = frozenset({"output", "after"})

#: Whether a step of this kind calls a model of its own, given its configuration. A step whose kind
#: is absent calls nothing; a new step kind has to answer this here.
ASKS_A_MODEL: dict[str, Callable[[dict[str, Any]], bool]] = {
    # Always: rewriting is the whole step.
    "pii_filter": lambda config: True,
    # Only in `llm` mode; the heuristic asks nobody.
    "injection_filter": lambda config: config.get("mode") == "llm",
    # Only with categories to choose between; without them the step falls straight to its default.
    "model_route": lambda config: bool(config.get("categories")),
}


class DryRunRequest(BaseModel):
    #: Required: a dry run spends tokens, so it belongs to a use case exactly as a request does.
    use_case: str = Field(min_length=1, max_length=64)
    system: str = Field(default="", max_length=MAX_SAMPLE_CHARS)
    user: str = Field(default="", max_length=MAX_SAMPLE_CHARS)
    model: str = Field(default="", max_length=128)
    pipeline: dict[str, Any] = {}
    #: Keep evaluating after a step refuses. Spends real tokens on steps production never reaches,
    #: which is why it is opt-in.
    past_blocks: bool = False


def models_named_in(pipeline: dict[str, Any]) -> list[str]:
    """Every model this pipeline could reach, wherever it is written.

    The release check has to see **all** of them — each classifier, **each step's own fallback
    chain** (`FRD-302` FR-10), each category's target, the default target and the dispatch chain.
    Mirrored by Management's serializer; a test compares the two.
    """
    named: list[str] = []
    for step in pipeline.get("steps") or []:
        config = (step.get("config") or {}) if isinstance(step, dict) else {}
        if config.get("model"):
            named.append(str(config["model"]))
        if config.get("default_model"):
            named.append(str(config["default_model"]))
        for fallback in config.get("fallback_models") or []:
            if fallback:
                named.append(str(fallback))
        for category in config.get("categories") or []:
            if isinstance(category, dict) and category.get("model"):
                named.append(str(category["model"]))
    named.extend(str(name) for name in pipeline.get("fallback_models") or [] if name)
    return list(dict.fromkeys(named))


def classifiers_named_in(pipeline: dict[str, Any]) -> list[str]:
    """The models a dry run will actually **call** — a subset of `models_named_in`.

    A dry run runs each step's own model, **and its own chain where that model cannot be reached**
    (`FRD-302` FR-7), and never reaches a category's target, a `default_model` or the dispatch
    chain. The release is about the whole configuration; the installation's approval is about calls
    this request makes, and applying it to a target nobody dials would refuse a preview whose
    classifier is approved.
    """
    named: list[str] = []
    for step in pipeline.get("steps") or []:
        if not isinstance(step, dict):
            continue
        config = step.get("config") or {}
        if not isinstance(config, dict) or not config.get("model"):
            continue
        asks = ASKS_A_MODEL.get(str(step.get("type")))
        if asks is not None and asks(config):
            named.append(str(config["model"]))
            named.extend(str(name) for name in config.get("fallback_models") or [] if name)
    return list(dict.fromkeys(named))


def _model_the_pipeline_is_about(
    pipeline: dict[str, Any], models: list[Any], released: list[str] | None = None
) -> str:
    """Which model to simulate when the caller named none — a guess, reported as `effective_model`.

    In order: a model the pipeline's own steps name (a category target, then a default), the first
    fallback, then the first *released* model that can generate, then any released model. The first
    registered model is the last resort only when the use case has no release at all: answering
    about a model nobody chose makes a working pipeline look broken.
    """
    steps = pipeline.get("steps") or []
    for step in steps if isinstance(steps, list) else []:
        config = (step.get("config") or {}) if isinstance(step, dict) else {}
        if not isinstance(config, dict):
            continue
        categories = config.get("categories") or []
        for category in categories if isinstance(categories, list) else []:
            if isinstance(category, dict) and category.get("model"):
                return str(category["model"])
        if config.get("default_model"):
            return str(config["default_model"])
    fallbacks = pipeline.get("fallback_models") or []
    for fallback in fallbacks if isinstance(fallbacks, list) else []:
        return str(fallback)
    if released:
        # A pipeline is about a request that generates; an embedding model never serves one.
        generating = {
            model.name for model in models if "generateContent" in model.supported_methods
        }
        for name in released:
            if name in generating:
                return name
        return released[0]
    return models[0].name if models else "mock-1"


async def _models_refusal(
    request: Request,
    payload: DryRunRequest,
    model: str,
    released: list[str] | None,
    registry: ProviderRegistry,
) -> JSONResponse | None:
    """Refuse a dry run that names an unreleased model or would call an unapproved one."""
    if released is not None:
        # `None` means no event has described this use case yet — the same third state dispatch
        # reads (`FRD-308` §4.1).
        wanted = [model, *models_named_in(payload.pipeline)]
        withheld = sorted({name for name in wanted if name and name not in released})
        if withheld:
            return _error(
                400,
                f"Use case '{payload.use_case}' has not been released "
                f"{', '.join(repr(name) for name in withheld)}. A dry run calls the models a "
                "pipeline names, so it may only name models the use case may call. An "
                "administrator of the use case releases them.",
                "FAILED_PRECONDITION",
            )

    # Approval is a fact about the installation and has no third state (`FRD-307`), so it is asked
    # unconditionally — folding it into the branch above would make it conditional on the release.
    approved = ModelApproved(catalog_of(request), registry)
    for name in classifiers_named_in(payload.pipeline):
        refused = await approved.refusal(name)
        if refused is not None:
            return _error(400, refused, "FAILED_PRECONDITION")
    return None


async def _may_author(request: Request, principal: Principal, use_case: str) -> bool:
    """Whether ``principal`` may try a pipeline of their own on ``use_case``: what saving one takes.

    The use case's administrators, and whoever may administer every use case. Without
    authentication there is nobody to tell apart, and the unbound break-glass key is an operator's
    (`ADR-0015`). A key bound to the use case acts for an agent and is never its administrator,
    whoever minted it.
    """
    if principal.method == "demo" or (principal.method == "api_key" and not principal.use_cases):
        return True
    if principal.method != "oidc":
        return False
    if principal.allows(Permission.USECASE_MANAGE_ALL):
        return True
    async with sessionmaker_of(request)() as session:
        return await grant_role_in(session, principal, use_case) == GrantRole.ADMIN.value


async def _authority_refusal(
    request: Request, payload: DryRunRequest, pipeline: Pipeline, authors: bool
) -> JSONResponse | None:
    """Refuse a member what only an author may do: an unsaved pipeline, or steps past a block."""
    if authors:
        return None
    store: PipelineStore = request.app.state.pipeline_store
    saved = await store.get(payload.use_case) or Pipeline()
    if pipeline != saved:
        return _error(
            403,
            f"Only an administrator of use case '{payload.use_case}' may try a pipeline that is "
            "not its saved one: its steps send your text to a model under the instructions it "
            "carries. Test the saved pipeline, or ask an administrator.",
            "PERMISSION_DENIED",
        )
    if payload.past_blocks:
        return _error(
            403,
            f"Only an administrator of use case '{payload.use_case}' may run steps past a block, "
            "which production never reaches.",
            "PERMISSION_DENIED",
        )
    return None


def _rendered(result: DryRunResult, *, model_words: bool) -> JSONResponse:
    return JSONResponse(
        {
            "blocked": result.blocked,
            "block_reason": result.block_reason,
            "effective_model": result.effective_model,
            "fallback_models": list(result.fallback_models),
            "trace": [
                {
                    "type": entry.type,
                    "action": entry.action,
                    "detail": (
                        entry.detail
                        if model_words
                        else {k: v for k, v in entry.detail.items() if k not in MODEL_WORDS}
                    ),
                    "after_block": entry.after_block,
                }
                for entry in result.trace
            ],
        }
    )


@router.post("/v1beta/pipeline:dryRun")
async def dry_run(
    request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    try:
        body = await json_body(request)
    except ValueError:
        return _error(400, "Request body is not valid JSON.", "INVALID_ARGUMENT")
    try:
        payload = DryRunRequest.model_validate(body)
    except ValidationError as exc:
        # Name the field: a bare "Field required" says something is wrong and not what.
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        detail = str(first.get("msg", "invalid"))
        return _error(400, f"{where}: {detail}".strip(": "), "INVALID_ARGUMENT")
    # The pipeline is a body somebody typed: a field the engine cannot use is named, not crashed on.
    problem = problem_in(payload.pipeline)
    if problem is not None:
        return _error(400, problem, "INVALID_ARGUMENT")
    pipeline = Pipeline.from_dict(payload.pipeline)

    # A selector never grants access: naming somebody else's use case would borrow their release
    # (`FRD-206`, `ADR-0015`).
    refusal = use_case_refusal(principal, payload.use_case)
    if refusal is not None:
        return _error(403, refusal, "PERMISSION_DENIED")
    authors = await _may_author(request, principal, payload.use_case)
    refused_authority = await _authority_refusal(request, payload, pipeline, authors)
    if refused_authority is not None:
        return refused_authority

    registry = providers_of(request)
    engine: PipelineEngine = request.app.state.pipeline_engine

    models = registry.models()
    released = await released_for(request, payload.use_case)
    model = payload.model or _model_the_pipeline_is_about(payload.pipeline, models, released)
    refused = await _models_refusal(request, payload, model, released, registry)
    if refused is not None:
        return refused

    messages: list[CanonicalMessage] = []
    if payload.system:
        messages.append(CanonicalMessage(role=Role.SYSTEM, text=payload.system))
    messages.append(CanonicalMessage(role=Role.USER, text=payload.user))
    canonical = CanonicalRequest(model=model, messages=messages)

    # Set here rather than by the middleware, because this endpoint takes its use case from the
    # body; the rows this run writes and the gate below both read it.
    attribute(request, Attribution.of(principal, payload.use_case))
    # The controls that do not need to know a model — suspension, rate limits, exhausted budget —
    # before anything is spent (`FRD-126`: one bundle, not an order assembled at the call site).
    try:
        await guard_before_work(request)
    except REFUSALS as exc:
        return _refusal(exc)

    trail = AuditTrail(operation="pipeline:dryRun", api="gemini")
    try:
        result = await engine.dry_run(
            pipeline,
            canonical,
            model_calls=trail.model_calls,
            declaration_of=await declared_model(request),
            past_blocks=payload.past_blocks,
        )
    finally:
        # A filter that blocked still spent the tokens it took to decide that. There is no response
        # row — a dry run dispatches nothing — only the calls its steps made.
        await record_pipeline_calls(request, trail)

    return _rendered(result, model_words=authors)
