"""Headers that say what happened to a request (`FRD-628`).

Headers rather than body fields: a Gemini SDK validates the body it parses and ignores a header it
does not know, and a migrating KIRA client changes a base URL and nothing else — so what is new
about an answer travels where no standard client looks unless it means to.
"""

from __future__ import annotations

from typing import Any

#: The header naming the request a response belongs to (`FRD-628` FR-1): the audit row's id, which
#: the console finds a request by. On every response, refusals included.
REQUEST_ID_HEADER = "x-aira-request-id"

#: Which control refused a request, as one word from the audit's outcomes (`FRD-628` FR-3) — on
#: every refusal, both surfaces, so a client that cannot parse an envelope can still tell a pipeline
#: block from a malformed request.
REASON_HEADER = "x-aira-reason"
#: The model that answered, and what the pipeline did to the request (`FRD-628` FR-2).
MODEL_HEADER = "x-aira-model"
PIPELINE_HEADER = "x-aira-pipeline"

#: Where the shared layer keeps the request's `AuditTrail`, for the headers below to be read from.
TRAIL_STATE = "aira_trail"

#: Characters a pipeline header's values keep; anything else — a space in a routing category, a
#: comma, an equals sign — becomes `_`, so `step=action` pairs can always be split again.
_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/@+-")

#: Decisions that are not a pipeline step's: the dispatch chain says itself in `X-AIRA-Model`, and a
#: notice is already in the answer it was added to.
_NOT_A_STEP = frozenset({"dispatch", "notice"})

#: Where a route leaves the headers that explain a response (`X-AIRA-Model`, `X-AIRA-Pipeline`,
#: `X-AIRA-Reason`), for the middleware to add when the response starts — a streamed response's
#: headers are sent before its body is produced, and the route may not hold the response object.
EXPLAINING_HEADERS = "aira_headers"


def explain(state: object, name: str, value: str | None) -> None:
    """Add one explaining header to the response this request will get (`FRD-628`). ``None``
    withholds it, including what the trail would have said (`withhold`)."""
    headers = getattr(state, EXPLAINING_HEADERS, None)
    if headers is None:
        headers = {}
        setattr(state, EXPLAINING_HEADERS, headers)
    headers[name.lower()] = value


def withhold(state: object, name: str) -> None:
    """Send no ``name`` header on this response, whatever the trail says when it starts.

    For a stream whose headers go out before any model answered (`ADR-0046`): the trail then holds
    the model asked first, and a fallback may answer instead — a header naming it would be a claim
    the answer can contradict.
    """
    explain(state, name, None)


def header_value(value: str) -> bytes:
    """A header value as HTTP may carry it: latin-1, no line breaks — a model name from a
    catalogue is not trusted to be either."""
    return value.replace("\r", " ").replace("\n", " ").encode("latin-1", errors="replace")


def _token(value: object) -> str:
    return "".join(c if c in _SAFE else "_" for c in str(value)) or "_"


def step_verdict(decision: dict[str, Any]) -> str:
    """One pipeline decision as a header's `step=action`: what the step did, in one word."""
    if "from" in decision and "to" in decision:
        return f"{_token(decision['from'])}>{_token(decision['to'])}"
    action = str(decision.get("action") or "")
    if decision.get("step") == "injection_filter" and action not in {"blocked"}:
        return "flagged" if decision.get("flagged") else "passed"
    return _token(action or "ran")


def answer_headers(trail: Any, status: int) -> dict[str, str]:
    """`X-AIRA-Model` and `X-AIRA-Pipeline` for a request's response (`FRD-628` FR-2).

    The model only for an answer — a refusal has none — and the pipeline whenever a step ran: a
    block is one of the things the header says.
    """
    headers: dict[str, str] = {}
    if status < 400 and getattr(trail, "model", None):
        headers[MODEL_HEADER] = str(trail.model)
    steps = [
        f"{_token(decision.get('step'))}={step_verdict(decision)}"
        for decision in getattr(trail, "decisions", None) or []
        if isinstance(decision, dict) and decision.get("step") not in _NOT_A_STEP
    ]
    if steps:
        headers[PIPELINE_HEADER] = ", ".join(steps)
    return headers
