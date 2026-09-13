"""Every refusal, in the predecessor's error vocabulary (`FRD-107`).

A migrating client's error handling switches on these codes, so every shared control's refusal
needs a KIRA code; without one it would fall through to a 500.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse

from aira_gateway.anomalies.suspensions import Suspended
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.kira import errors
from aira_gateway.api.serving import REFUSALS, upstream_status
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.core.schema import SchemaRejected
from aira_gateway.embedding import EmbeddingRejected
from aira_gateway.pipeline.dispatch import NoCapableModel
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.thinking import ThinkingRejected
from aira_gateway.upstreams.base import DialectUnsupported, UpstreamError

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
        return errors.kira_error_response(400, errors.VALIDATION_ERROR, str(exc))
    if isinstance(exc, GeminiHTTPError) and exc.code == 404:
        # Every 404 of the shared layer is a model nothing serves — the same fact the numeric-id
        # lookup reports, so it answers the same way.
        return errors.kira_error_response(422, errors.MODEL_NOT_FOUND, exc.message)
    if isinstance(exc, GeminiHTTPError):
        return errors.kira_error_response(
            exc.code, KIRA_CODE_FOR.get(exc.code, errors.VALIDATION_ERROR), exc.message
        )
    if isinstance(exc, Suspended | RateLimited | BudgetExceeded):
        return errors.kira_error_response(429, errors.EXTERNAL_KI_API_TOO_MANY_REQUEST, exc.message)
    if isinstance(exc, PipelineRejected):
        return errors.kira_error_response(exc.code, errors.VALIDATION_ERROR, exc.message)
    if isinstance(exc, NoCapableModel):
        return errors.kira_error_response(400, errors.MODEL_NOT_FOUND, str(exc))
    if isinstance(exc, DialectUnsupported):
        # The model exists; the request as written cannot be carried to it. `MODEL_NOT_FOUND`
        # would send the client looking for a different id.
        return errors.kira_error_response(400, errors.VALIDATION_ERROR, str(exc))
    if isinstance(exc, UpstreamError):
        code = upstream_status(exc.status_code)[0]
        name = (
            errors.EXTERNAL_KI_API_TOO_MANY_REQUEST if code == 429 else errors.EXTERNAL_KI_API_ERROR
        )
        return errors.kira_error_response(code, name, exc.message)
    return errors.kira_error_response(500, errors.INTERNAL_SERVER_ERROR, "Internal server error.")
