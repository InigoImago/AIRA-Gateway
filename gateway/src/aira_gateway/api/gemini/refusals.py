"""Every refusal in `serving.REFUSALS`, in the Gemini error envelope.

Shared by the surface's routes and by `pipeline:dryRun`, which answers in the same envelope and
must give the same status for the same refusal.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse

from aira_gateway.anomalies.suspensions import Suspended
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.api.serving import upstream_error
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.core.schema import SchemaRejected
from aira_gateway.embedding import EmbeddingRejected
from aira_gateway.pipeline.dispatch import NoCapableModel
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.thinking import ThinkingRejected
from aira_gateway.upstreams.base import DialectUnsupported, UpstreamError


def refusal_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, AttachmentRejected | SchemaRejected):
        return _error(400, str(exc), "INVALID_ARGUMENT")
    if isinstance(exc, ThinkingRejected | EmbeddingRejected):
        # Google's envelope has no field for the contract's code, so it travels in the message;
        # the KIRA surface renders it as the code (`FRD-107`).
        return _error(400, f"{exc.code}: {exc.message}", "INVALID_ARGUMENT")
    if isinstance(exc, NoCapableModel):
        # Every candidate was excluded: a configuration somebody can fix, not an outage.
        return _error(400, str(exc), "FAILED_PRECONDITION")
    if isinstance(exc, Suspended | RateLimited):
        # 429 for a suspension too: the caller is stopped temporarily, and a 403 would send them
        # to fix permissions they have.
        return _error(
            429, exc.message, "RESOURCE_EXHAUSTED", headers={"Retry-After": exc.retry_after}
        )
    if isinstance(exc, BudgetExceeded):
        return _error(429, exc.message, "RESOURCE_EXHAUSTED")
    if isinstance(exc, PipelineRejected):
        return _error(exc.code, exc.message, exc.status)
    if isinstance(exc, DialectUnsupported):
        # The catalogue claims something this model's wire format cannot say — a declaration
        # somebody can correct, not a gateway failure.
        return _error(400, str(exc), "FAILED_PRECONDITION")
    if isinstance(exc, UpstreamError):
        return upstream_error(exc)
    assert isinstance(exc, GeminiHTTPError)
    return exc.to_response()
