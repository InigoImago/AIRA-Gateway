"""Running a use case's pre-dispatch pipeline (FRD-300) and accounting for the calls it made.

Both runners share one contract, on **every** way out of the pipeline including a refusal:

- the model calls a step made are audited and billed (`FRD-125`) — a blocking filter still spent
  the tokens it took to decide to block;
- the stored request is the rewritten one (`FRD-309` FR-3), and if a redaction could not be applied
  the stored body is dropped rather than kept.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from aira_common.logging import get_logger
from aira_common.observability import set_span_attributes
from aira_gateway.api.serving.context import attribution_of, declared_model, provenance
from aira_gateway.audit import PIPELINE_OPERATION_PREFIX, AuditTrail, Outcome, redaction_failed
from aira_gateway.core.canonical import CanonicalEmbeddingRequest, CanonicalRequest
from aira_gateway.persistence.recorder import record_request
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.store import PipelineStore
from aira_gateway.state import budgets_of, pricing_of

_log = get_logger("aira_gateway")


async def run_pipeline(
    request: Request, canonical: CanonicalRequest, trail: AuditTrail
) -> tuple[CanonicalRequest, tuple[str, ...], tuple[str, ...]]:
    """Apply the use case's pipeline to a generation request. Pass-through when none is configured.

    Returns the effective request (re-routed or rewritten), the fallback chain, and the notices the
    caller is owed (`FRD-309`). May raise ``PipelineRejected``; the decisions taken until then are
    already on the trail, so a blocked request records *why* (FRD-122 FR-4).
    """
    store: PipelineStore = request.app.state.pipeline_store
    engine: PipelineEngine = request.app.state.pipeline_engine
    use_case = getattr(attribution_of(request), "use_case", None)
    pipeline = await store.get(use_case)
    if pipeline is None:
        return canonical, (), ()
    rewrites: list[tuple[str, str]] = []
    try:
        outcome = await engine.run(
            pipeline,
            canonical,
            # The engine appends into the trail's lists, so a step that blocks still leaves the
            # decisions, model calls and rewrites made before it.
            decisions=trail.decisions,
            model_calls=trail.model_calls,
            rewrites=rewrites,
            # Lets a step call a model the catalogue knows and configuration does not (`FRD-507`).
            declaration_of=await declared_model(request),
        )
    finally:
        # One site, on every way out: a blocking filter still spent tokens deciding to block.
        await record_pipeline_calls(request, trail)
        _keep_only_what_a_redactor_allows(trail, rewrites)
    trail.routed_to(outcome.request.model)
    if outcome.decisions:
        set_span_attributes(
            {
                "aira.pipeline.decisions": len(outcome.decisions),
                "aira.pipeline.model": outcome.request.model,
            }
        )
        _log.info(
            "pipeline_applied",
            use_case=use_case,
            model=outcome.request.model,
            decisions=outcome.decisions,
        )
    return outcome.request, outcome.fallback_models, tuple(outcome.notices)


async def run_pipeline_over_texts(
    request: Request, embed: CanonicalEmbeddingRequest, trail: AuditTrail
) -> CanonicalEmbeddingRequest:
    """Apply the steps that mean anything for texts alone (`TEXT_ONLY_STEPS`, i.e. `pii_filter`).

    The embedding sibling of :func:`run_pipeline`: no model to route to and no answer to carry a
    notice, so it returns the request with its texts replaced. Without it, a use case with
    redaction switched on embedded and stored its callers' text unredacted.
    """
    store: PipelineStore = request.app.state.pipeline_store
    engine: PipelineEngine = request.app.state.pipeline_engine
    use_case = getattr(attribution_of(request), "use_case", None)
    pipeline = await store.get(use_case)
    if pipeline is None:
        return embed
    rewrites: list[tuple[str, str]] = []
    try:
        outcome = await engine.run_over_texts(
            pipeline,
            embed.texts,
            model=embed.model,
            decisions=trail.decisions,
            model_calls=trail.model_calls,
            rewrites=rewrites,
            declaration_of=await declared_model(request),
        )
    finally:
        await record_pipeline_calls(request, trail)
        _keep_only_what_a_redactor_allows(trail, rewrites)
    if outcome.decisions:
        _log.info(
            "pipeline_applied",
            use_case=use_case,
            model=embed.model,
            decisions=outcome.decisions,
        )
    return embed.model_copy(update={"texts": list(outcome.texts)})


async def record_pipeline_calls(request: Request, trail: AuditTrail) -> None:
    """Audit and bill the model calls the **pipeline** made (`FRD-125`), one row per call.

    Never allowed to fail the request: the caller's own outcome is already decided, so a failure is
    logged loudly instead of turning a correct answer into a 500.
    """
    if not trail.model_calls:
        return
    try:
        attribution = attribution_of(request)
        for call in trail.model_calls:
            cost = await pricing_of(request).cost_nanos(call.model, call.usage)
            await record_request(
                request,
                # Named for the step, so reporting separates the use case's own traffic from what
                # governing it cost.
                operation=f"{PIPELINE_OPERATION_PREFIX}{call.step}",
                model=call.model,
                status=200,
                usage=call.usage,
                latency_ms=None,
                # Never the prompt: storing it again would double every retention and redaction
                # question (`FRD-404`, `FRD-406`).
                request_payload=None,
                response_payload=None,
                cost_nanos=cost,
                outcome=Outcome.SERVED,
                requested_model=call.model,
                provenance=await provenance(request, call.model),
                api=trail.api,
            )
            await budgets_of(request).book_side_call(
                getattr(attribution, "use_case", None),
                getattr(attribution, "person", None),
                call.usage.total_tokens,
                cost_nanos=cost,
            )
    except Exception:  # noqa: BLE001 — see above
        _log.error("pipeline_call_not_recorded", operation=trail.operation, exc_info=True)


def _keep_only_what_a_redactor_allows(trail: AuditTrail, rewrites: list[tuple[str, str]]) -> None:
    """Make the stored body the rewritten one, or drop it when a redaction failed (`FRD-309` FR-3).

    A failed redaction has no rewritten version, and the original is exactly what the step exists
    to remove — so there is nothing safe to keep.
    """
    if rewrites:
        trail.body = _rewritten_body(trail.body, rewrites)
    if redaction_failed(trail.decisions):
        trail.body = None


def _rewritten_body(
    body: dict[str, Any] | None, rewrites: list[tuple[str, str]]
) -> dict[str, Any] | None:
    """The wire body with each rewritten text substituted — or ``None`` if one cannot be found.

    A literal substitution works for either surface without knowing its shape. When a text is not
    found, the payload is dropped: keeping it would store the personal data the step removed.
    """
    if body is None:
        return None
    text = json.dumps(body, ensure_ascii=False)
    for before, after in rewrites:
        # Escaped on both sides: inside serialised JSON a quote or newline is `\"` or `\n`.
        needle = json.dumps(before, ensure_ascii=False)[1:-1]
        if needle not in text:
            return None
        text = text.replace(needle, json.dumps(after, ensure_ascii=False)[1:-1])
    try:
        rewritten: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError:
        return None
    return rewritten
