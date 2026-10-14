"""One pipeline step, evaluated: what it did, without deciding what to do about it (FRD-300/306).

Each step type is one method returning a `StepEvaluation`; `runner.PipelineEngine` interprets it for
the served path and for the dry run alike. A step reaches only the models its own configuration
names — its `model` and its own `fallback_models` chain (`FRD-302` FR-7), never a default nobody
chose — and asks the installation's conditions of each before calling it (`StepPermits`): those
names were checked when the pipeline was saved, and a release withdrawn or an approval revoked since
must stop the call. A step whose models cannot be reached, or may not be called, degrades in its own
safe direction — the LLM filter to the heuristic, the router to its `default_model`, the redactor to
a block — and says why.

**The chain moves on for reachability and nothing else** (FR-8). A model that answered has
answered: an `UNDETERMINED` verdict or a rewrite too short to trust stops the chain, because a
filter that kept asking until some model agreed would be a filter that weakens under load.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, assert_never

from aira_common.models import ThinkingMode
from aira_common.pipelines import FailurePolicy, FilterAction, InjectionMode, ScanScope
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
    Redaction,
    Routing,
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

#: Why this request may not have a step call a model, or ``None`` if it may — the conditions every
#: model a request reaches meets (`api.serving.conditions`), handed in per request because only the
#: request knows its use case's release.
StepPermits = Callable[[str], Awaitable[str | None]]


class Reachable(Protocol):
    """An answer that says whether its model was reached — what a step's chain moves on for.

    Read-only on purpose: the chain asks, it does not set. `Classification`, `Routing` and
    `Redaction` each carry the field, so one helper can walk a chain for all three steps instead of
    three loops that would drift (the shape `LESSONS.md` §1 lists: a fact applied at each site is
    the fact missing from one of them).
    """

    @property
    def reached(self) -> bool: ...


#: What a filter does when its classifier reached no verdict (`FRD-125`). Blocking is the default:
#: a filter configured to block that serves an unchecked request is an absent control that still
#: looks active. An operator who prefers availability says so, and the choice is on the audit row.
UNDETERMINED_BLOCKS = str(FailurePolicy.BLOCK)
UNDETERMINED_ALLOWS = str(FailurePolicy.ALLOW)

#: How much of a model's reply the dry run shows. The interesting part of a classifier's answer is
#: at the front, and the ellipsis says the rest exists.
_SHOWN = 600

#: Why a step that names a classifier asked none: no model it names resolves to anything this
#: gateway can reach — a retired model, a typo, a missing catalogue entry.
_NO_CLASSIFIER = "no model this step names can be reached from this gateway"

#: Why a verdict covers less than the whole text (`classifiers.MAX_SCANNED_CHARS`).
_PARTIAL_SCAN = "the text is longer than the scanned bound; its first and last part were read"


def _blocks(verdict: Verdict, config: dict[str, Any]) -> bool:
    """Whether this verdict stops the request, given how the step is configured.

    **Only the word `flag` lets a finding through.** Anything else — `block`, nothing at all, or a
    word this build does not know — blocks: a typo in a security control must fail closed, and the
    two-word vocabulary is `aira_common.pipelines.FilterAction`.
    """
    if config.get("action") == FilterAction.FLAG:
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


def _scanned_text(request: CanonicalRequest, scope: Any) -> str:
    """What the injection filter reads.

    `user` (the default) is **the last user message**, not the conversation: an injection planted in
    an earlier turn is not scanned again — a stated blind spot, documented in
    `docs/REQUEST-LIFECYCLE.md`. `system_user` reads every system and user message, and so does a
    scope this build does not know: a misspelt scope must not quietly narrow what is read.
    """
    if scope == ScanScope.USER:
        return request.last_user_text()
    return _system_and_user(request)


@dataclass(frozen=True, slots=True)
class _Candidates:
    """The models a step may ask, in order, each with its adapter — and the ones it may not."""

    found: list[tuple[str, Any]]
    #: `model: why` for each name the request's conditions withheld (`StepPermits`).
    withheld: list[str]

    def why_none(self, otherwise: str) -> str:
        """Why the step asked no model: what the conditions withheld, else ``otherwise``."""
        if self.withheld:
            return "no model this step names may be called: " + "; ".join(self.withheld)
        return otherwise


@dataclass(frozen=True, slots=True)
class _Routed:
    """What `_route` worked out: the category, the model to send to, the cost, and the reply."""

    category: str | None
    target: str | None
    call: ModelCall | None = None
    reply: str = ""
    #: The model asked to classify — not the routed-to one, so the screen names who decided.
    classifier: str | None = None
    #: Why no classifier was asked, where none was.
    unasked: str = ""


class StepEvaluator:
    """Evaluates one step of any type against the models this gateway can reach."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def evaluate(
        self,
        step: PipelineStep,
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None,
        permits: StepPermits | None = None,
    ) -> StepEvaluation:
        """The single dispatch over step types: a new step is a branch here and a method below."""
        if step.type is StepType.INJECTION_FILTER:
            return await self._evaluate_injection_filter(
                step.config, request, declaration_of, permits
            )
        if step.type is StepType.MODEL_ROUTE:
            return await self._evaluate_model_route(step.config, request, declaration_of, permits)
        if step.type is StepType.PII_FILTER:
            return await self._evaluate_pii_filter(step.config, request, declaration_of, permits)
        # Unknown steps are dropped at parse time; a new `StepType` without a branch here is a type
        # error, and at run time a refusal to guess rather than a step reported as having passed.
        assert_never(step.type)

    # -- the steps ------------------------------------------------------------------------

    async def _evaluate_injection_filter(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None,
        permits: StepPermits | None = None,
    ) -> StepEvaluation:
        classification, unasked = await self._classify(config, request, declaration_of, permits)
        fell_back = unasked is not None
        verdict = classification.verdict
        said = _said(
            classification.reply, classification.call.model if classification.call else None
        )
        action = str(config.get("action", FilterAction.BLOCK))
        blocking = _blocks(verdict, config)
        # Said wherever the verdict is shown: a heuristic verdict standing in for an `llm` step
        # reads exactly like the model's own, and only the operator can fix the model name.
        why = f"{verdict} (by the heuristic)" if fell_back else str(verdict)
        notes = [
            *([f"decided by the heuristic: {unasked}"] if unasked is not None else []),
            *([_PARTIAL_SCAN] if classification.unscanned else []),
        ]
        return StepEvaluation(
            type="injection_filter",
            action="blocked" if blocking else ("passed" if verdict is Verdict.CLEAN else "flagged"),
            detail={
                "mode": config.get("mode", InjectionMode.HEURISTIC),
                "action": action,
                "verdict": str(verdict),
                **({"decided_by": "heuristic"} if fell_back else {}),
                **({"unscanned": classification.unscanned} if classification.unscanned else {}),
                **({"why": "; ".join(notes)} if notes else {}),
                **said,
            },
            # On **every** outcome: "ran and passed" and "no filter configured" must differ on the
            # audit row (`FRD-122` FR-4, `FRD-125`).
            decision={
                "step": "injection_filter",
                "flagged": verdict is not Verdict.CLEAN,
                "action": "blocked" if blocking else action,
                "why": why,
            },
            call=classification.call,
            block_reason=_rejection(verdict) if blocking else None,
        )

    async def _evaluate_model_route(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None,
        permits: StepPermits | None = None,
    ) -> StepEvaluation:
        routed = await self._route(config, request, declaration_of, permits)
        category, target, call = routed.category, routed.target, routed.call
        said = _said(routed.reply, routed.classifier)
        if call is None and config.get("categories"):
            # A router that could not be asked **says so** — otherwise it is indistinguishable from
            # one that ran and matched nothing. The default model still applies, on both paths.
            # And which of the two: no model to ask is a configuration to fix, a model that did not
            # answer is an outage to wait out.
            fallback = config.get("default_model")
            retarget = bool(fallback) and fallback != request.model
            reason = "classifier_failed" if routed.classifier else "no_classifier"
            why = (
                "the classifier could not be reached or refused the request"
                if routed.classifier
                else routed.unasked or _NO_CLASSIFIER
            )
            decision = {"step": "model_route", "action": "not_asked", "why": reason}
            return StepEvaluation(
                type="model_route",
                action="not_asked",
                detail={
                    "why": why,
                    **({"to": fallback} if retarget else {"model": request.model}),
                },
                decision=decision,
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
        permits: StepPermits | None = None,
    ) -> StepEvaluation:
        """Replace personal data in the prompt, using a model the use case trusts (`FRD-309`).

        This step has no lesser version of itself: passing the original through would send exactly
        what it exists to withhold. So a failure **blocks by default** (`FRD-125`); `on_failure:
        allow` is the operator's recorded choice. Only the **user's own text** is rewritten — the
        system instruction is the use case's, and a redactor must not edit it.
        """
        candidates = await self._models_for(config, declaration_of, permits)
        if not candidates.found:
            why = candidates.why_none("no model is available to redact with")
            return self._pii_failure(config, why, None)

        original = request.last_user_text()

        async def ask(model: str, provider: Any) -> Redaction:
            return await LlmRedactor(
                provider,
                model,
                config.get("instruction"),
                await self._thinking_for(model, declaration_of),
            ).rewrite(original)

        answered = await self._asked(candidates.found, ask)
        # `candidates.found` is not empty, so the chain answered with something.
        assert answered is not None
        model, result = answered
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
        permits: StepPermits | None = None,
    ) -> tuple[Classification, str | None]:
        """The verdict, from the first of this step's models that could be reached — and, where the
        heuristic gave it in place of an `llm` step's model, why.

        An `llm` step whose models none resolve falls back to the **heuristic** — a downgrade of
        the method, not of the verdict, so a mistyped model name cannot take a use case's traffic
        down, and the decision says the heuristic answered. One that resolves and cannot be reached
        leaves its verdict *undetermined*, which the step decides on (`FRD-125`).
        """
        text = _scanned_text(request, config.get("scope", ScanScope.USER))
        if config.get("mode") == "llm":

            async def ask(model: str, provider: Any) -> Classification:
                return await LlmInjectionClassifier(
                    provider,
                    model,
                    config.get("instruction"),
                    await self._thinking_for(model, declaration_of),
                ).classify_text(text)

            candidates = await self._models_for(config, declaration_of, permits)
            answered = await self._asked(candidates.found, ask)
            if answered is not None:
                return answered[1], None
            heuristic = await self._heuristic(config).classify_text(text)
            return heuristic, candidates.why_none(_NO_CLASSIFIER)
        return await self._heuristic(config).classify_text(text), None

    def _heuristic(self, config: dict[str, Any]) -> InjectionClassifier:
        """The pattern classifier: the `heuristic` mode, and where an `llm` step with no reachable
        model lands. Never a model call, so it cannot fail the way a chain can. Only a real `false`
        turns the built-in phrasings off."""
        extras = tuple(
            pattern for pattern in config.get("patterns") or () if isinstance(pattern, str)
        )
        return HeuristicInjectionClassifier(
            extras, use_builtins=config.get("use_builtins", True) is not False
        )

    async def _route(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None = None,
        permits: StepPermits | None = None,
    ) -> _Routed:
        """The category, the model to use, what asking cost (billed even when nothing matched,
        `FRD-125`), and what the model said (for the dry run's screen)."""
        categories: list[dict[str, str]] = [
            category for category in config.get("categories") or () if isinstance(category, dict)
        ]
        default_model = config.get("default_model")
        if not categories:
            return _Routed(None, default_model)

        async def ask(model: str, provider: Any) -> Routing:
            return await LlmCategoryRouter(
                provider, model, categories, await self._thinking_for(model, declaration_of)
            ).classify_text(_system_and_user(request))

        candidates = await self._models_for(config, declaration_of, permits)
        answered = await self._asked(candidates.found, ask)
        if answered is None:
            return _Routed(None, default_model, unasked=candidates.why_none(_NO_CLASSIFIER))
        model, routing = answered
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

    async def _models_for(
        self,
        config: dict[str, Any],
        declaration_of: DeclarationOf | None,
        permits: StepPermits | None = None,
    ) -> _Candidates:
        """The models this step may ask, in order, each with the adapter that serves it.

        Its own `model` first, then its own `fallback_models` (`FRD-302` FR-7). A name no provider
        serves is left out rather than tried — that is the "quietly do less" the single-model
        version had, and the chain is what now gives the step somewhere else to go. A name the
        request's conditions refuse is left out too, and **kept with its reason**: a step that
        degrades because its model was withdrawn has to say so, or it reads as a typo. Duplicates
        are dropped, so a chain that repeats the step's own model does not pay for it twice.
        """
        named = [config.get("model"), *(config.get("fallback_models") or [])]
        found: list[tuple[str, Any]] = []
        withheld: list[str] = []
        seen: set[str] = set()
        for name in named:
            model = str(name) if name else ""
            if not model or model in seen:
                continue
            seen.add(model)
            provider = await self._provider_for(model, declaration_of)
            if provider is None:
                continue
            refusal = await permits(model) if permits is not None else None
            if refusal is not None:
                withheld.append(f"{model}: {refusal}")
                continue
            found.append((model, provider))
        return _Candidates(found, withheld)

    async def _asked[Answer: Reachable](
        self,
        candidates: list[tuple[str, Any]],
        ask: Callable[[str, Any], Awaitable[Answer]],
    ) -> tuple[str, Answer] | None:
        """Ask this step's models in order; the first one **reached** answers (`FRD-302` FR-8).

        The answer comes back with the model that gave it, because that is the name the decision
        row and the dry run's screen carry — for a fallback it is not the model written first.

        ``None`` only where the step has no candidate at all, which is the case each step already
        had a safe direction for. When every candidate was tried and none could be reached, the
        **last** answer is returned: a chain that is exhausted has to decide exactly what one
        unreachable model decided, or adding a fallback would change what a blocking filter does.
        """
        answered: tuple[str, Answer] | None = None
        for model, provider in candidates:
            answer = await ask(model, provider)
            answered = (model, answer)
            if answer.reached:
                break
        return answered

    async def _provider_for(self, model: str, declaration_of: DeclarationOf | None) -> Any:
        """The adapter serving ``model``, asking the catalogue when configuration does not know it
        (`FRD-507`). Every caller reads "nothing" as "quietly do less", so both lookups matter.

        ``model`` is a name, never empty: `_models_for` drops the empty ones, which is where "this
        step names no model" is decided. The guard that used to be here as well was unreachable
        from the moment the chain took over, and an unreachable guard is a line no test can hold.
        """
        direct = self._registry.provider_for(model)
        if direct is not None or declaration_of is None:
            return direct
        declaration = await declaration_of(model)
        # The publisher too: one platform can host two dialects, and on Vertex the provider alone
        # identifies neither.
        return self._registry.provider_for(model, declaration.provider, declaration.publisher)

    async def _thinking_for(self, model: str, declaration_of: DeclarationOf | None) -> Any:
        """What to tell ``model`` about thinking, from the catalogue.

        Without a resolver the classifier asks for off: a reasoning model sent nothing thinks and
        spends its one-word allowance doing so (`FRD-125`).
        """
        if declaration_of is None:
            return Thinking(mode=ThinkingMode.DISABLED)
        return for_a_classifier(await declaration_of(model))
