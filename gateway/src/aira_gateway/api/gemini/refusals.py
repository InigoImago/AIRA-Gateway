"""Every refusal in `serving.REFUSALS`, in the Gemini error envelope.

Shared by the surface's routes and by `pipeline:dryRun`, which answers in the same envelope and
must give the same status for the same refusal.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from aira_gateway.anomalies.suspensions import Suspended
from aira_gateway.api.gemini.errors import GeminiHTTPError, gemini_error_response
from aira_gateway.api.serving import refusal_metadata, refusal_reason, upstream_answer
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.core.schema import SchemaRejected
from aira_gateway.embedding import EmbeddingRejected
from aira_gateway.explaining import REASON_HEADER, explain
from aira_gateway.pipeline.dispatch import NoCapableModel
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.thinking import ThinkingRejected
from aira_gateway.upstreams.base import AmbiguousModel, DialectUnsupported, UpstreamError


def refusal_response(exc: Exception, request: Request | None = None) -> JSONResponse:
    """A refusal in Google's envelope, saying which control refused (`FRD-628` FR-3).

    The status and the message are what they always were — a client's retry logic keys on the one
    and its people read the other. What is added is Google's own `ErrorInfo` in `details` and the
    `X-AIRA-Reason` header, and with ``request`` the request's id in the `ErrorInfo`, so an SDK's
    exception carries what finds the request in the console.
    """
    code, message, status, headers = _parts(exc)
    reason = refusal_reason(exc)
    metadata = refusal_metadata(exc)
    if request is not None:
        request_id = getattr(request.state, "request_id", None)
        if request_id:
            metadata["request_id"] = str(request_id)
        explain(request.state, REASON_HEADER, reason)
    return gemini_error_response(
        code, message, status, headers=headers, reason=reason, metadata=metadata
    )


def _parts(exc: Exception) -> tuple[int, str, str, dict[str, str] | None]:
    """Status, message, Google status word and headers of one refusal."""
    if isinstance(exc, AttachmentRejected | SchemaRejected):
        return 400, str(exc), "INVALID_ARGUMENT", None
    if isinstance(exc, ThinkingRejected | EmbeddingRejected):
        # Google's envelope has no field for the contract's code, so it travels in the message;
        # the KIRA surface renders it as the code (`FRD-107`).
        return 400, f"{exc.code}: {exc.message}", "INVALID_ARGUMENT", None
    if isinstance(exc, NoCapableModel | AmbiguousModel | RegionNotAllowed):
        # Every candidate was excluded, or the model has no place to be sent: a configuration
        # somebody can fix, not an outage.
        return 400, str(exc), "FAILED_PRECONDITION", None
    if isinstance(exc, Suspended | RateLimited):
        # 429 for a suspension too: the caller is stopped temporarily, and a 403 would send them
        # to fix permissions they have.
        return 429, exc.message, "RESOURCE_EXHAUSTED", {"Retry-After": exc.retry_after}
    if isinstance(exc, BudgetExceeded):
        return 429, exc.message, "RESOURCE_EXHAUSTED", None
    if isinstance(exc, PipelineRejected):
        return exc.code, exc.message, exc.status, None
    if isinstance(exc, DialectUnsupported):
        # The catalogue claims something this model's wire format cannot say — a declaration
        # somebody can correct, not a gateway failure.
        return 400, str(exc), "FAILED_PRECONDITION", None
    if isinstance(exc, UpstreamError):
        # Shared with the other surface: which upstream statuses pass through is a fact about the
        # upstream, and one outage must not look like two different problems by URL.
        code, status = upstream_answer(exc)
        return code, exc.message, status, None
    assert isinstance(exc, GeminiHTTPError)
    return exc.code, exc.message, exc.status, exc.headers
