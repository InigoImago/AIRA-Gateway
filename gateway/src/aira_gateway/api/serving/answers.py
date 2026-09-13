"""What happens to an answer before it leaves: the notice, the structured-output check, headers.

`FRD-309`'s notice tells a caller their request was changed by the pipeline. It goes in front of
plain text only; a structured document, a tool call, or an empty answer cannot carry it, and the
audit row records which of those happened. Every non-streamed exit calls :func:`annotate`, every
stream uses :class:`StreamedNotice` — `test_notice_reaches_every_exit.py` enforces both.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.serving.prepare import Prepared
from aira_gateway.audit import AuditTrail
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse

WITHHELD_STRUCTURED = (
    "the answer is a document the caller parses, and a sentence would invalidate it"
)
WITHHELD_TOOL_CALL = "the answer is a tool call, which carries no text"
WITHHELD_NO_TEXT = "the answer carries no plain text to put it in front of"


def check_structured_result(canonical: CanonicalRequest, response: CanonicalResponse) -> None:
    """A schema-constrained answer that did not finish normally is not data (`FRD-112` FR-6).

    A document truncated at the output cap still looks like JSON up to where it stops, and a model
    that answered in prose produced no document at all; returning either as the requested shape
    hands someone else's application a broken or half-parsed result.
    """
    if canonical.response_schema is None or response.finish_reason == "stop":
        return
    raise GeminiHTTPError(
        502,
        f"The model did not return a complete document matching the requested schema "
        f"(it stopped with '{response.finish_reason}').",
        "FAILED_PRECONDITION",
    )


def deprecation_headers(declaration: ModelDeclaration) -> dict[str, str]:
    """A ``Warning`` header for a deprecated model (FRD-114 FR-5). It warns and never blocks:
    blocking is revocation (`FRD-307`), and conflating the two removes the announcement."""
    if not declaration.deprecated:
        return {}
    return {
        "Warning": f'299 - "Model {declaration.name} is deprecated and will be withdrawn."',
    }


def withheld_because(response: CanonicalResponse, *, structured: bool) -> str:
    """Why the notice cannot precede this answer, or ``""`` when it can.

    ``structured`` is a fact about the *request* (a ``responseSchema`` was sent): a JSON document is
    non-empty text without a tool call, so the response alone cannot tell it apart from prose.
    """
    if structured:
        return WITHHELD_STRUCTURED
    if response.tool_calls:
        return WITHHELD_TOOL_CALL
    if not response.text.strip():
        return WITHHELD_NO_TEXT
    return ""


def with_notices(
    response: CanonicalResponse, notices: tuple[str, ...], *, structured: bool = False
) -> CanonicalResponse:
    """Prepend the notices to a plain-text answer; see :func:`withheld_because` for the rule."""
    if not notices or withheld_because(response, structured=structured):
        return response
    return response.model_copy(update={"text": "\n\n".join([*notices, response.text])})


def notice_outcome(
    response: CanonicalResponse, notices: tuple[str, ...], *, structured: bool = False
) -> dict[str, Any] | None:
    """What the audit row says about the notice — including that it was **not** shown, and why."""
    if not notices:
        return None
    why = withheld_because(response, structured=structured)
    return {"step": "notice", "action": "withheld" if why else "shown", "why": why}


def annotate(
    canonical: CanonicalRequest | None,
    response: CanonicalResponse,
    prepared: Prepared,
    trail: AuditTrail,
) -> CanonicalResponse:
    """Apply the notice to a finished answer and record what became of it.

    Also records where the answer was produced: only the adapter knows which region served it, and
    this is the one site every non-streamed exit passes through.
    """
    if response.served_region:
        trail.served_region = response.served_region
    if not prepared.notices:
        return response
    structured = canonical is not None and canonical.response_schema is not None
    note = notice_outcome(response, prepared.notices, structured=structured)
    if note is not None:
        trail.decisions.append(note)
    return with_notices(response, prepared.notices, structured=structured)


class StreamedNotice:
    """The notice on a **streamed** answer: in front of the first text delta that arrives.

    Same rule as :func:`withheld_because`: a structured answer is refused up front, and a tool call
    or an answer without text never produces a delta, so nothing is shown. :meth:`outcome` records
    which case happened, exactly as a buffered answer does.
    """

    def __init__(self, notices: tuple[str, ...], *, structured: bool = False) -> None:
        self._notices = () if structured else tuple(notices)
        self._configured = tuple(notices)
        self._structured = structured
        self._shown = False

    def lead(self, text_delta: str) -> str:
        """The delta to send: the first non-empty one carries the notice, the rest are unchanged."""
        if not self._notices or not text_delta:
            return text_delta
        led = "\n\n".join([*self._notices, text_delta])
        self._notices = ()
        self._shown = True
        return led

    def outcome(self) -> dict[str, Any] | None:
        """What the audit row says about it, or ``None`` when there was no notice to show."""
        if not self._configured:
            return None
        if self._shown:
            return {"step": "notice", "action": "shown", "why": ""}
        why = WITHHELD_STRUCTURED if self._structured else WITHHELD_NO_TEXT
        return {"step": "notice", "action": "withheld", "why": why}
