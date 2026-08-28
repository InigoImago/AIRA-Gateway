"""Pipeline dry-run endpoint (FRD-306).

Evaluates a (possibly unsaved) pipeline against a sample system + user prompt and returns the full
per-step decision trace — the builder's "test this pipeline" button. It never dispatches the
caller's own generation, but it **does** run the real engine, so an LLM-backed step reaches a real
provider and spends real tokens.

That last sentence is why this module was rewritten on 2026-08-11. The docstring above it used to
claim the bounds on sample size and step count meant "a single call cannot be turned into a free
LLM relay", and it was measured: a caller posted a pipeline naming any model as its classifier and
the gateway **called it** — no use case, no release check (`FRD-308`), no approval check
(`FRD-307`), no budget, no rate limit, **and no audit row**. 1000 tokens spent, nothing recorded.
A comment claiming a rule the system did not have, which is a pattern this project has now named
several times.

So the rule the rest of the request path has is the rule here:

- the caller names a **use case** and must be allowed to act on it (`use_case_refusal` — the same
  one function both surfaces use, so a selector still never grants access);
- **every model the pipeline would touch** must be released to that use case, refused by name;
- a use case with nothing released can dry-run nothing, which is the point: an endpoint that
  bypassed the release would make the release advisory.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.api.gemini.routes import refusal_response as _refusal
from aira_gateway.api.serving import (
    REFUSALS,
    catalog_of,
    declared_model,
    guard_before_work,
    record_pipeline_calls,
    released_for,
)
from aira_gateway.audit import AuditTrail
from aira_gateway.auth.attribution import Attribution
from aira_gateway.auth.dependencies import require_principal, use_case_refusal
from aira_gateway.auth.principal import Principal
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.pipeline.config import Pipeline
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.requirements import ModelApproved
from aira_gateway.upstreams.base import ProviderRegistry

router = APIRouter(tags=["pipeline"])

# A dry-run is a builder aid, not a generation endpoint: keep the sample small.
MAX_SAMPLE_CHARS = 8_000


class DryRunRequest(BaseModel):
    #: **Required.** A dry run spends tokens, so it belongs to a use case exactly as a request
    #: does — and without one there is nothing to check a model against.
    use_case: str = Field(min_length=1, max_length=64)
    system: str = Field(default="", max_length=MAX_SAMPLE_CHARS)
    user: str = Field(default="", max_length=MAX_SAMPLE_CHARS)
    model: str = Field(default="", max_length=128)
    pipeline: dict[str, Any] = {}
    #: Keep evaluating after a step refuses — a simulation of the steps production never reaches.
    #: Costs real tokens for those steps, which is why it is opt-in and off by default.
    past_blocks: bool = False


def models_named_in(pipeline: dict[str, Any]) -> list[str]:
    """Every model this pipeline could reach, wherever it is written.

    Collected in one place because the release check has to see **all** of them: the classifier a
    filter runs, the classifier a router runs, each category's target, the default target, and the
    fallback chain. A check that read one of those would refuse the obvious escape and leave four.
    """
    named: list[str] = []
    for step in pipeline.get("steps") or []:
        config = (step.get("config") or {}) if isinstance(step, dict) else {}
        if config.get("model"):
            named.append(str(config["model"]))
        if config.get("default_model"):
            named.append(str(config["default_model"]))
        for category in config.get("categories") or []:
            if isinstance(category, dict) and category.get("model"):
                named.append(str(category["model"]))
    named.extend(str(name) for name in pipeline.get("fallback_models") or [] if name)
    return list(dict.fromkeys(named))


#: Whether a step of this kind reaches a model of its own, given its configuration.
#:
#: A table rather than a chain of `elif`s, because the question each line answers — *does this step
#: call anything* — is one a fourth step will have to answer too, and a chain is where the fourth
#: one gets forgotten. A step whose kind is absent here calls nothing.
ASKS_A_MODEL: dict[str, Callable[[dict[str, Any]], bool]] = {
    # Always: rewriting is the whole step.
    "pii_filter": lambda config: True,
    # Only in `llm` mode; the heuristic asks nobody and costs nothing (`classifiers.py`).
    "injection_filter": lambda config: config.get("mode") == "llm",
    # Only with categories to choose between; without them the step falls straight to its default.
    "model_route": lambda config: bool(config.get("categories")),
}


def classifiers_named_in(pipeline: dict[str, Any]) -> list[str]:
    """The models this endpoint will actually **call**, which is a smaller set than it may name.

    `models_named_in` above answers "what could this pipeline reach", and the release is checked
    against all of it — rightly, because a saved pipeline routing to a model the use case may not
    call is a configuration that fails later, at dispatch, on a screen that looked correct.

    A dry run dispatches nothing. It runs the steps, so it asks each step's own model: the
    classifier an LLM filter runs, the classifier a router runs, and the model a redactor rewrites
    with. It never reaches a category's target, a `default_model` or the fallback chain — those
    are named on the way to a dispatch that does not happen here.

    The distinction matters because the two gates ask different questions of it. "May this use case
    call that model" is about the whole configuration. "May this installation use that model at
    all" is about the calls this request makes, and stretching it to cover a target nobody dials
    would refuse a builder a preview of a pipeline whose *classifier* is perfectly approved.
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
    return list(dict.fromkeys(named))


def _model_the_pipeline_is_about(
    pipeline: dict[str, Any], models: list[Any], released: list[str] | None = None
) -> str:
    """Which model to simulate when the caller named none.

    **Everything here is a guess**, and that is worth saying at the top because the answer is
    reported back as `effective_model`, where a builder reads it as a decision somebody made. A
    pipeline briefly carried a declared `start_model` and this preferred it; the field is gone
    (owner's decision — a use case releases several models on purpose and naming one on the
    pipeline narrowed that in the reader's mind), so the guesses are the answer again. The
    question catalogue, the other caller, no longer needs this at all: a run carries the model it
    was started with.

    The first *registered* model was the obvious choice and the wrong one: a builder testing a
    rule about `qwen3:0.6b` was answered with a refusal about `mock-1`, a model the operator never
    chose, on a rule that was working correctly. The dry run looked broken while the pipeline was
    fine.

    So the pipeline's own configuration is asked next. A step that names a model is a step saying
    which models this pipeline is *for*.

    The `models` list of the old `allow_check` step used to be read here too. That branch went
    when the step did (`FRD-308`): no step carries such a list any more, and a lookup that can
    never match is a rule the code claims and does not have — the same unreachable guard
    `parse_role_groups` had to lose.
    """
    steps = pipeline.get("steps") or []
    for step in steps:
        config = step.get("config") or {}
        for category in config.get("categories") or []:
            if category.get("model"):
                return str(category["model"])
        if config.get("default_model"):
            return str(config["default_model"])
    for fallback in pipeline.get("fallback_models") or []:
        return str(fallback)
    # Last resort: something this use case may actually call. The first *registered* model was the
    # fallback until the release existed, and then a pipeline naming no model at all — an injection
    # filter on its own, the commonest one — was answered with a refusal about `mock-1`, a model
    # nobody chose and the use case had no right to. A guess that is guaranteed wrong is worse than
    # the one it replaced.
    #
    # **And it must be able to generate.** `released[0]` alone was alphabetical, so a use case
    # released `all-minilm` and `qwen3:0.6b` had its chat pipeline simulated against the
    # *embedding* model — reported back to the builder as `effective_model`, which is exactly the
    # guess the paragraph above calls guaranteed wrong, in a second costume. A pipeline is about a
    # request that generates; an embedding model can never serve one.
    if released:
        generating = {
            model.name for model in models if "generateContent" in model.supported_methods
        }
        for name in released:
            if name in generating:
                return name
        # None of them generates, or none is registered here: the old answer, because a wrong
        # model named is still more use to a builder than none at all.
        return released[0]
    return models[0].name if models else "mock-1"


@router.post("/v1beta/pipeline:dryRun")
async def dry_run(
    request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "Request body is not valid JSON.", "INVALID_ARGUMENT")
    try:
        payload = DryRunRequest.model_validate(body)
    except ValidationError as exc:
        # **Named**, not merely reported. A bare "Field required" tells a caller that something is
        # wrong and not what — the same correction this project made for query parameters, where
        # the framework's own shape read to a Google client as "unknown error". Found live the
        # minute `use_case` became required.
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        detail = str(first.get("msg", "invalid"))
        return _error(400, f"{where}: {detail}".strip(": "), "INVALID_ARGUMENT")

    # **A selector never grants access.** Without this, naming somebody else's use case would
    # borrow their release — the `FRD-206`/`ADR-0015` defect, on the one endpoint that spends
    # tokens without dispatching a request.
    refusal = use_case_refusal(principal, payload.use_case)
    if refusal is not None:
        return _error(403, refusal, "PERMISSION_DENIED")

    registry: ProviderRegistry = request.app.state.providers
    engine: PipelineEngine = request.app.state.pipeline_engine

    models = registry.models()
    released = await released_for(request, payload.use_case)
    model = payload.model or _model_the_pipeline_is_about(payload.pipeline, models, released)

    if released is not None:
        # `None` means no event has described this use case yet — the same third state the
        # dispatch condition reads, and for the same reason (`FRD-308` §4.1).
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

    # **And the installation's own gate, which has no third state** (`FRD-307`), asked of the
    # models this endpoint will actually call.
    #
    # The release above is a fact about a use case, so "nobody has told us" is a real answer and
    # falling through on it is right (`released_for`). Approval is a fact about the installation:
    # a Global Administrator either catalogued and approved this model or did not, and there is no
    # partially-upgraded state in which the answer is unknown. Asked separately for exactly that
    # reason — folding it into the branch above would make the unconditional gate conditional on
    # the conditional one.
    #
    # Measured on 2026-08-27 against the hermetic app, before this existed: a use case whose
    # read-model row predates `FRD-308` (`allowed_models is None` — the upgrade the third state
    # exists to survive), a pipeline naming `mock-unapproved` as its classifier, and the trace
    # came back carrying that model's reply. The same model on `:generateContent` is a 400. The
    # endpoint had the use case's gate and not the installation's, so the one decision a request
    # is never allowed to argue with was the one it could walk around.
    approved = ModelApproved(catalog_of(request), registry)
    for name in classifiers_named_in(payload.pipeline):
        refused = await approved.refusal(name)
        if refused is not None:
            return _error(400, refused, "FAILED_PRECONDITION")
    messages: list[CanonicalMessage] = []
    if payload.system:
        messages.append(CanonicalMessage(role=Role.SYSTEM, text=payload.system))
    messages.append(CanonicalMessage(role=Role.USER, text=payload.user))

    canonical = CanonicalRequest(model=model, messages=messages)
    # Attribution, so the rows this run writes are attached to the same use case, subject and
    # credential every other row is. Set here rather than by the middleware because this endpoint
    # takes its use case from the body — and `record_pipeline_calls` reads it from one place, so a
    # second way of passing it would be a second way of forgetting it.
    request.state.attribution = Attribution(
        subject=principal.subject,
        method=principal.method,
        username=principal.username,
        use_case=payload.use_case,
        credential=principal.credential,
    )
    # **The controls that do not need to know a model**, which is the same three the served path
    # takes before anything is spent: a suspension, the rate limits, and whether the budget is
    # already exhausted (`guard_before_work`).
    #
    # This endpoint had none of them. The module docstring above names *"no budget, no rate limit"*
    # as part of the defect this file was rewritten for — and the rewrite restored the two that
    # were about **permission** (authorisation and release) and left the two that are about
    # **spending**. So a caller over budget, or rate-limited, or stopped outright by IT Security
    # could still make the gateway call real models here, as often as they liked. Audited and
    # billed, which is why it was visible after the fact and stopped by nothing.
    #
    # One call, not three: `guard_before_work` is the bundle, and assembling the order at a call
    # site is what `FRD-126` exists to prevent. It reads the attribution set just above.
    try:
        await guard_before_work(request)
    except REFUSALS as exc:
        return _refusal(exc)

    # The list is ours, so what the run spent survives whatever the run does — the same shape
    # `run` uses, and the reason it takes a caller-supplied list at all.
    trail = AuditTrail(operation="pipeline:dryRun", api="gemini")
    try:
        result = await engine.dry_run(
            Pipeline.from_dict(payload.pipeline),
            canonical,
            model_calls=trail.model_calls,
            declaration_of=await declared_model(request),
            past_blocks=payload.past_blocks,
        )
    finally:
        # In the `finally` for the same reason the served path puts it there: a filter that blocked
        # still spent the tokens it took to decide that. There is no response row — a dry run
        # dispatches nothing — only the calls its steps made.
        await record_pipeline_calls(request, trail)

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
                    "detail": entry.detail,
                    "after_block": entry.after_block,
                }
                for entry in result.trace
            ],
        }
    )
