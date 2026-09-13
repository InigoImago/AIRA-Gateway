"""One pipeline step, evaluated: what it did, without deciding what to do about it (FRD-300/306).

Each step type is one method returning a `StepEvaluation`; `runner.PipelineEngine` interprets it for
the served path and for the dry run alike. A step reaches only the model its own configuration
names — never a default nobody chose — so the release, approval and residency checks on that name
hold. A step whose model does not resolve degrades in its own safe direction: the LLM filter to the
heuristic, the router to its `default_model`, the redactor to a block.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.audit import ModelCall
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import CanonicalRequest, Role, TextPart, Thinking
from aira_gateway.pipeline.classifiers import (
    Classification,
    HeuristicInjectionClassifier,
    InjectionClassifier,
    LlmCategoryRouter,
    LlmInjectionClassifier,
    LlmRedactor,
    Verdict,
)
from aira_gateway.pipeline.config import PipelineStep, StepType
from aira_gateway.pipeline.engine.outcomes import StepEvaluation
from aira_gateway.thinking import for_a_classifier
from aira_gateway.upstreams.base import ProviderRegistry

#: How a step learns what the **catalogue** says about the model it wants to call — handed in per
#: request, because only the request has the catalogue. A model can be reachable because it is
#: catalogued (`FRD-507`), and a lookup by name alone found nothing, so the step silently did less.
#: It answers with the whole declaration because the step needs two facts from it: who serves the
#: model, and whether its thinking may be switched off.
DeclarationOf = Callable[[str], Awaitable[ModelDeclaration]]

#: What a filter does when its classifier reached no verdict (`FRD-125`). Blocking is the default:
#: a filter configured to block that serves an unchecked request is an absent control that still
#: looks active. An operator who prefers availability says so, and the choice is on the audit row.
UNDETERMINED_BLOCKS = "block"
UNDETERMINED_ALLOWS = "allow"

#: How much of a model's reply the dry run shows. The interesting part of a classifier's answer is
#: at the front, and the ellipsis says the rest exists.
_SHOWN = 600


def _blocks(verdict: Verdict, config: dict[str, Any]) -> bool:
    """Whether this verdict stops the request, given how the step is configured."""
    if config.get("action", "block") != "block":
        return False
    if verdict is Verdict.INJECTION:
        return True
    if verdict is Verdict.UNDETERMINED:
        return str(config.get("on_undetermined", UNDETERMINED_BLOCKS)) != UNDETERMINED_ALLOWS
    return False


def _rejection(verdict: Verdict) -> str:
    """Say which of the two happened: a caller to talk to, or a classifier to fix."""
    if verdict is Verdict.UNDETERMINED:
        return (
            "The prompt-injection filter could not reach a verdict, and this use case is "
            "configured to refuse rather than serve an unchecked request."
        )
    return "Request rejected by the prompt-injection filter."


def _shown(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _SHOWN else text[:_SHOWN] + "…"


def _said(reply: str, model: str | None) -> dict[str, Any]:
    """What a step's model replied, for the dry run's trace — **screen only, never persisted**
    (`FRD-122` §5.3). One function for every LLM step, so `output` means the same in each. An empty
    reply stays out entirely: a caption over nothing reads as a rendering fault."""
    return {
        **({"output": _shown(reply)} if reply.strip() else {}),
        **({"classifier": model} if model else {}),
    }


def _filled(template: str, **values: str) -> str:
    """A notice with ``{model}`` / ``{category}`` filled in.

    Not `str.format`: the template is operator-written, and a stray brace would make `format` raise
    and crash the request the notice describes. An unknown placeholder is left standing, so `{modl}`
    reads as the typo it is.
    """
    for name, value in values.items():
        template = template.replace("{" + name + "}", value)
    return template


def _with_user_text(request: CanonicalRequest, text: str) -> CanonicalRequest:
    """The request with the **last user message's text** replaced.

    Attachments and tool parts on that message are kept — dropping a document because its covering
    sentence was rewritten would lose what the request was about. Their contents are not scanned
    (`FRD-110`'s stated blind spot).
    """
    messages = list(request.messages)
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role is Role.USER:
            message = messages[index]
            kept = [part for part in message.parts if not isinstance(part, TextPart)]
            messages[index] = message.model_copy(update={"parts": [TextPart(text=text), *kept]})
            break
    return request.model_copy(update={"messages": messages})


def _system_and_user(request: CanonicalRequest) -> str:
    return "\n".join(m.text for m in request.messages if m.role in (Role.SYSTEM, Role.USER))


def _scanned_text(request: CanonicalRequest, scope: str) -> str:
    """What the injection filter reads.

    `user` (the default) is **the last user message**, not the conversation: an injection planted in
    an earlier turn is not scanned again — a stated blind spot, documented in
    `docs/REQUEST-LIFECYCLE.md`. `system_user` reads every system and user message.
    """
    if scope == "system_user":
        return _system_and_user(request)
    return request.last_user_text()


@dataclass(frozen=True, slots=True)
class _Routed:
    """What `_route` worked out: the category, the model to send to, the cost, and the reply."""

    category: str | None
    target: str | None
    call: ModelCall | None = None
    reply: str = ""
    #: The model asked to classify — not the routed-to one, so the screen names who decided.
    classifier: str | None = None


class StepEvaluator:
    """Evaluates one step of any type against the models this gateway can reach."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def evaluate(
        self,
        step: PipelineStep,
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None,
    ) -> StepEvaluation:
        """The single dispatch over step types: a new step is a branch here and a method below."""
        if step.type is StepType.INJECTION_FILTER:
            return await self._evaluate_injection_filter(step.config, request, declaration_of)
        if step.type is StepType.MODEL_ROUTE:
            return await self._evaluate_model_route(step.config, request, declaration_of)
        if step.type is StepType.PII_FILTER:
            return await self._evaluate_pii_filter(step.config, request, declaration_of)
        # Unreachable while `StepType` is exhaustive: unknown steps are dropped at parse time.
        return StepEvaluation(type=str(step.type), action="unchanged")

    # -- the steps ------------------------------------------------------------------------

    async def _evaluate_injection_filter(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None,
    ) -> StepEvaluation:
        classification = await self._classify(config, request, declaration_of)
        verdict = classification.verdict
        said = _said(
            classification.reply, classification.call.model if classification.call else None
        )
        action = config.get("action", "block")
        blocking = _blocks(verdict, config)
        return StepEvaluation(
            type="injection_filter",
            action="blocked" if blocking else ("passed" if verdict is Verdict.CLEAN else "flagged"),
            detail={
                "mode": config.get("mode", "heuristic"),
                "action": action,
                "verdict": str(verdict),
                **said,
            },
            # On **every** outcome: "ran and passed" and "no filter configured" must differ on the
            # audit row (`FRD-122` FR-4, `FRD-125`).
            decision={
                "step": "injection_filter",
                "flagged": verdict is not Verdict.CLEAN,
                "action": "blocked" if blocking else action,
                "why": str(verdict),
            },
            call=classification.call,
            block_reason=_rejection(verdict) if blocking else None,
        )

    async def _evaluate_model_route(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None,
    ) -> StepEvaluation:
        routed = await self._route(config, request, declaration_of)
        category, target, call = routed.category, routed.target, routed.call
        said = _said(routed.reply, routed.classifier)
        if call is None and config.get("categories"):
            # A router that could not be asked **says so** — otherwise it is indistinguishable from
            # one that ran and matched nothing. The default model still applies, on both paths.
            fallback = config.get("default_model")
            retarget = bool(fallback) and fallback != request.model
            return StepEvaluation(
                type="model_route",
                action="not_asked",
                detail={
                    "why": "the classifier could not be reached or refused the request",
                    **({"to": fallback} if retarget else {"model": request.model}),
                },
                decision={"step": "model_route", "action": "not_asked", "why": "classifier_failed"},
                request=request.model_copy(update={"model": fallback}) if retarget else None,
            )
        # The caller is told about the classification in the operator's words (`FRD-309`) — only
        # where a category matched; a notice about a decision never taken would be false.
        template = str(config.get("notice") or "").strip()
        notice = (
            _filled(template, category=category or "", model=target or request.model)
            if template and category
            else None
        )

        if target and target != request.model:
            return StepEvaluation(
                type="model_route",
                action="rerouted",
                detail={"category": category, "from": request.model, "to": target, **said},
                decision={
                    "step": "model_route",
                    "category": category,
                    "from": request.model,
                    "to": target,
                },
                call=call,
                request=request.model_copy(update={"model": target}),
                notice=notice,
            )
        # A router that ran leaves a decision even when it changed nothing. "Matched a category
        # that maps to the current model" and "matched nothing" stay apart: the second is a
        # classifier or a category list to look at.
        return StepEvaluation(
            type="model_route",
            action="unchanged",
            detail={"category": category, "model": request.model, **said},
            decision={
                "step": "model_route",
                "action": "unchanged",
                "category": category or "",
                "why": "matched" if category else "no_category_matched",
            },
            call=call,
            notice=notice,
        )

    async def _evaluate_pii_filter(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None,
    ) -> StepEvaluation:
        """Replace personal data in the prompt, using a model the use case trusts (`FRD-309`).

        This step has no lesser version of itself: passing the original through would send exactly
        what it exists to withhold. So a failure **blocks by default** (`FRD-125`); `on_failure:
        allow` is the operator's recorded choice. Only the **user's own text** is rewritten — the
        system instruction is the use case's, and a redactor must not edit it.
        """
        model = config.get("model")
        provider = await self._provider_for(model, declaration_of)
        if provider is None or not model:
            return self._pii_failure(config, "no model is available to redact with", None)

        original = request.last_user_text()
        redactor = LlmRedactor(
            provider,
            model,
            config.get("instruction"),
            await self._thinking_for(model, declaration_of),
        )
        result = await redactor.rewrite(original)
        if result.text is None:
            return self._pii_failure(config, result.failure or "the redactor failed", result.call)

        changed = result.text.strip() != original.strip()
        return StepEvaluation(
            type="pii_filter",
            action="redacted" if changed else "unchanged",
            # Screen only: what the redaction did to the sample the tester typed.
            detail={
                "classifier": model,
                "changed": changed,
                "before": _shown(original),
                "after": _shown(result.text),
            },
            # `changed`, not a count: the placeholder shape is the operator's instruction's, so a
            # count would be a number nobody measured.
            decision={
                "step": "pii_filter",
                "action": "redacted" if changed else "unchanged",
                "why": model,
            },
            call=result.call,
            request=_with_user_text(request, result.text) if changed else None,
            notice=str(config.get("notice") or "").strip() if changed else None,
            rewrote=(original, result.text) if changed else None,
        )

    @staticmethod
    def _pii_failure(config: dict[str, Any], why: str, call: ModelCall | None) -> StepEvaluation:
        """A redactor that did not produce a usable rewrite."""
        allows = str(config.get("on_failure", UNDETERMINED_BLOCKS)) == UNDETERMINED_ALLOWS
        return StepEvaluation(
            type="pii_filter",
            action="allowed" if allows else "blocked",
            detail={"why": why, "on_failure": "allow" if allows else "block"},
            decision={
                "step": "pii_filter",
                "action": "allowed" if allows else "blocked",
                "why": why,
            },
            call=call,
            block_reason=None if allows else f"Personal data could not be removed: {why}.",
        )

    # -- what the steps ask ---------------------------------------------------------------

    async def _classify(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None = None,
    ) -> Classification:
        text = _scanned_text(request, config.get("scope", "user"))
        classifier = await self._injection_classifier(config, declaration_of)
        return await classifier.classify_text(text)

    async def _injection_classifier(
        self, config: dict[str, Any], declaration_of: DeclarationOf | None = None
    ) -> InjectionClassifier:
        """The classifier this step is configured with.

        An `llm` step whose model does not resolve falls back to the **heuristic** — a downgrade of
        the method, not of the verdict, so a mistyped model name cannot take a use case's traffic
        down. The lookup asks the catalogue, so a catalogued model is not mistaken for a typo.
        """
        if config.get("mode") == "llm":
            model = config.get("model")
            provider = await self._provider_for(model, declaration_of)
            if provider is not None and model is not None:
                return LlmInjectionClassifier(
                    provider,
                    model,
                    config.get("instruction"),
                    await self._thinking_for(model, declaration_of),
                )
        extras = tuple(config.get("patterns", []))
        return HeuristicInjectionClassifier(extras, use_builtins=config.get("use_builtins", True))

    async def _route(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None = None,
    ) -> _Routed:
        """The category, the model to use, what asking cost (billed even when nothing matched,
        `FRD-125`), and what the model said (for the dry run's screen)."""
        categories: list[dict[str, str]] = config.get("categories", [])
        default_model = config.get("default_model")
        if not categories:
            return _Routed(None, default_model)
        model = config.get("model")
        provider = await self._provider_for(model, declaration_of)
        if provider is None or model is None:
            return _Routed(None, default_model)
        router = LlmCategoryRouter(
            provider, model, categories, await self._thinking_for(model, declaration_of)
        )
        routing = await router.classify_text(_system_and_user(request))
        if routing.category:
            for category in categories:
                if category.get("name") == routing.category:
                    return _Routed(
                        routing.category,
                        category.get("model") or default_model,
                        routing.call,
                        routing.reply,
                        model,
                    )
        return _Routed(None, default_model, routing.call, routing.reply, model)

    async def _provider_for(self, model: str | None, declaration_of: DeclarationOf | None) -> Any:
        """The adapter serving ``model``, asking the catalogue when configuration does not know it
        (`FRD-507`). Every caller reads "nothing" as "quietly do less", so both lookups matter."""
        if not model:
            return None
        direct = self._registry.provider_for(model)
        if direct is not None or declaration_of is None:
            return direct
        declaration = await declaration_of(model)
        # The publisher too: one platform can host two dialects, and on Vertex the provider alone
        # identifies neither.
        return self._registry.provider_for(model, declaration.provider, declaration.publisher)

    async def _thinking_for(self, model: str | None, declaration_of: DeclarationOf | None) -> Any:
        """What to tell ``model`` about thinking, from the catalogue.

        Without a resolver the classifier asks for off: a reasoning model sent nothing thinks and
        spends its one-word allowance doing so (`FRD-125`).
        """
        if declaration_of is None or not model:
            return Thinking(mode=ThinkingMode.DISABLED)
        return for_a_classifier(await declaration_of(model))
