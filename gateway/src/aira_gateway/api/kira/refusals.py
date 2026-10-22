"""Every refusal, in the predecessor's error vocabulary (`FRD-107`).

A migrating client's error handling switches on these codes, so every shared control's refusal
needs a KIRA code; without one it would fall through to a 500.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse

from aira_gateway.anomalies.suspensions import Suspended
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.kira import errors
from aira_gateway.api.serving import REFUSALS, NotAReranker, ToolsNotPermitted, upstream_status
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.core.schema import SchemaRejected
from aira_gateway.embedding import EmbeddingRejected
from aira_gateway.pipeline.dispatch import NoCapableModel
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.thinking import ThinkingRejected
from aira_gateway.upstreams.base import AmbiguousModel, DialectUnsupported, UpstreamError

#: The shared refusals plus this surface's own error type.
KIRA_REFUSALS = (*REFUSALS, errors.KiraError)

#: A shared control's HTTP status, in this surface's vocabulary. Anything unmapped is a validation
#: error, which is what a 4xx from a pre-dispatch check is.
KIRA_CODE_FOR = {
    403: errors.STANDARD_USER_PERMISSION_REQUIRED,
    429: errors.EXTERNAL_KI_API_TOO_MANY_REQUEST,
    502: errors.EXTERNAL_KI_API_ERROR,
}


def refusal_response(exc: Exception) -> JSONResponse:
    """A refusal in the predecessor's envelope. Anything that is not a refusal is a 500."""
    if isinstance(exc, errors.KiraError):
        return exc.to_response()
    if isinstance(exc, ThinkingRejected | EmbeddingRejected):
        # These carry the contract's own codes (`INVALID_THINKING_MODE`, …).
        return errors.kira_error_response(422, exc.code, exc.message)
    if isinstance(exc, AttachmentRejected | SchemaRejected):
        # 422, the predecessor's status for a type nobody accepts and a schema it cannot read: both
        # are a field of a well-formed body that is wrong (`FRD-146` FR-15).
        return errors.kira_error_response(422, errors.VALIDATION_ERROR, str(exc))
    if isinstance(exc, NotAReranker):
        # The contract's own code for a model that does not rank (`FRD-147` FR-4).
        return errors.kira_error_response(422, errors.NO_RERANKER_CAPABILITIES, exc.message)
    if isinstance(exc, ToolsNotPermitted):
        # The contract's code for a tool configuration it does not accept (`FRD-146` FR-6).
        return errors.kira_error_response(422, errors.INVALID_TOOL_CONFIGURATION, exc.message)
    if isinstance(exc, GeminiHTTPError) and exc.code == 404:
        # Every 404 of the shared layer is a model nothing serves — the same fact the numeric-id
        # lookup reports, so it answers the same way.
        return errors.kira_error_response(422, errors.MODEL_NOT_FOUND, exc.message)
    if isinstance(exc, GeminiHTTPError):
        return errors.kira_error_response(
            exc.code, KIRA_CODE_FOR.get(exc.code, errors.VALIDATION_ERROR), exc.message
        )
    if isinstance(exc, Suspended | RateLimited):
        # When to come back, as the Gemini surface says it: the same control, the same answer.
        return errors.kira_error_response(
            429,
            errors.EXTERNAL_KI_API_TOO_MANY_REQUEST,
            exc.message,
            headers={"Retry-After": exc.retry_after},
        )
    if isinstance(exc, BudgetExceeded):
        return errors.kira_error_response(429, errors.EXTERNAL_KI_API_TOO_MANY_REQUEST, exc.message)
    if isinstance(exc, PipelineRejected):
        return errors.kira_error_response(exc.code, errors.VALIDATION_ERROR, exc.message)
    if isinstance(exc, NoCapableModel | DialectUnsupported):
        # The model exists; the request as written cannot be served by it — no structured output,
        # no tools, not released, a declaration its wire format cannot say. `MODEL_NOT_FOUND`
        # would send the client looking for a different id.
        return errors.kira_error_response(400, errors.VALIDATION_ERROR, str(exc))
    if isinstance(exc, AmbiguousModel | RegionNotAllowed):
        # Nowhere this installation may send the model: the name serves nothing here, and the
        # predecessor's `MODEL_NOT_FOUND` is always `422`.
        return errors.kira_error_response(422, errors.MODEL_NOT_FOUND, str(exc))
    if isinstance(exc, UpstreamError):
        # **One status for every provider failure but one**, as the predecessor answers: `502`, and
        # `429` where the provider asked the caller to slow down, which is the one a client acts on
        # differently (`FRD-146` FR-16). What the provider said stays in the message.
        if upstream_status(exc.status_code)[0] == 429:
            return errors.kira_error_response(
                429, errors.EXTERNAL_KI_API_TOO_MANY_REQUEST, exc.message
            )
        return errors.kira_error_response(502, errors.EXTERNAL_KI_API_ERROR, exc.message)
    return errors.kira_error_response(500, errors.INTERNAL_SERVER_ERROR, "Internal server error.")
