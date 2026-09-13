"""The router's classifier: which of the configured categories a request belongs to (FRD-300)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from aira_gateway.audit import ModelCall
from aira_gateway.core.canonical import Thinking
from aira_gateway.pipeline.classifiers.prompts import (
    ROUTER_INSTRUCTION,
    THINKING_OFF,
    classifier_request,
)
from aira_gateway.telemetry import model_call_span
from aira_gateway.upstreams.base import Upstream, UpstreamError


@dataclass(frozen=True, slots=True)
class Routing:
    """A category, what asking cost, and what the model replied.

    Named rather than a tuple so the reply cannot be unpacked in the wrong position. As with
    `Classification`, ``reply`` reaches the dry run's screen and never the audit trail.
    """

    category: str | None
    call: ModelCall | None = None
    reply: str = ""


class LlmCategoryRouter:
    """Classifies a request into one of the configured categories (or ``None``).

    ``None`` means "use the configured default model", not a refusal: an unrouted request still gets
    a valid answer from a model the use case chose.
    """

    def __init__(
        self,
        provider: Upstream,
        model: str,
        categories: list[dict[str, str]],
        thinking: Thinking | None = THINKING_OFF,
    ) -> None:
        self._provider = provider
        self._model = model
        self._categories = categories
        self._thinking = thinking

    async def classify_text(self, text: str) -> Routing:
        """The category, what asking for it cost, and what the model said."""
        listing = "\n".join(
            f"- {c.get('name', '')}: {c.get('description', '')}" for c in self._categories
        )
        request = classifier_request(
            self._model,
            ROUTER_INSTRUCTION.format(categories=listing),
            text,
            self._thinking,
        )
        try:
            with model_call_span(self._model, purpose="pipeline"):
                response = await self._provider.generate(request)
        except UpstreamError:
            return Routing(None)
        call = ModelCall(step="model_route", model=self._model, usage=response.usage)
        return Routing(self._matched(response.text), call, response.text)

    def _matched(self, reply: str) -> str | None:
        """Which category this reply names, or ``None`` if it does not name exactly one.

        Whole words, and **exactly one**. A substring test let `NONE` — the instruction's own word
        for "no category" — select a category named `one`, and let a reply naming two categories
        pick whichever the operator listed first. A reply naming two has not answered, so it gets
        the `default_model`, recorded as `no_category_matched`.
        """
        answer = reply.strip().upper()
        names = [str(category.get("name", "")) for category in self._categories]
        # The exact answer first: a model that did as it was told must not depend on a rule written
        # for one that did not.
        for name in names:
            if name and answer == name.upper():
                return name
        found = [
            name for name in names if name and re.search(rf"\b{re.escape(name.upper())}\b", answer)
        ]
        return found[0] if len(found) == 1 else None

    async def classify(self, text: str) -> str | None:
        """The category alone, for callers with nothing to bill."""
        return (await self.classify_text(text)).category
