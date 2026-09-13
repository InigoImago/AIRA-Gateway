"""Which exceptions count as refusals, and how each maps onto a status and an audit outcome."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from aira_gateway.anomalies.suspensions import Suspended
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.audit import Outcome
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.core.schema import SchemaRejected
from aira_gateway.embedding import EmbeddingRejected
from aira_gateway.pipeline.dispatch import NoCapableModel
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.thinking import ThinkingRejected
from aira_gateway.upstreams.base import AmbiguousModel, DialectUnsupported, UpstreamError

#: Every exception a surface must treat as a refusal rather than an unhandled error. Listed once,
#: so a new control cannot be caught by one surface and escape the other.
REFUSALS = (
    Suspended,
    AttachmentRejected,
    ThinkingRejected,
    SchemaRejected,
    EmbeddingRejected,
    RateLimited,
    BudgetExceeded,
    PipelineRejected,
    NoCapableModel,
    UpstreamError,
    # A model with nowhere to be sent — no region catalogued, or one residency forbids — is what a
    # chain skips a candidate for (`dispatch_with_fallback`); a direct verb refuses it the same way.
    AmbiguousModel,
    RegionNotAllowed,
    # A declaration the model's wire format cannot express is an operator-fixable refusal, not a
    # 500: the audit row must say the catalogue is wrong, not that the gateway broke.
    DialectUnsupported,
    GeminiHTTPError,
)

#: Upstream statuses passed through to the caller, with their Google status name.
UPSTREAM_STATUS_MAP: dict[int, tuple[int, str]] = {
    429: (429, "RESOURCE_EXHAUSTED"),
    503: (503, "UNAVAILABLE"),
    504: (504, "DEADLINE_EXCEEDED"),
}

#: An upstream **400** means the body we built from this deployment's catalogue is one the model
#: rejects — a configuration fault an operator can fix, so not `502 UNAVAILABLE`. Only 400: a 401
#: or 403 concerns *our* credentials, stays a generic 502, and keeps its detail in the log.
UPSTREAM_REFUSED = (400, "FAILED_PRECONDITION")

#: A `GeminiHTTPError`'s status, as an audit outcome. Anything unmapped is an invalid request.
_REFUSAL_OUTCOMES: dict[int, Outcome] = {
    404: Outcome.MODEL_NOT_FOUND,
    400: Outcome.INVALID_REQUEST,
    403: Outcome.BLOCKED_BY_PIPELINE,
}


def upstream_status(status_code: int | None) -> tuple[int, str]:
    mapped = UPSTREAM_STATUS_MAP.get(status_code or 0)
    if mapped is not None:
        return mapped
    if status_code == 400:
        return UPSTREAM_REFUSED
    return (502, "UNAVAILABLE")


def upstream_error(exc: UpstreamError) -> JSONResponse:
    """An upstream failure as a client-facing response.

    Shared rather than per surface: which upstream statuses pass through is a fact about the
    upstream, and one outage must not look like two different problems depending on the URL.
    """
    code, status = upstream_status(exc.status_code)
    return _error(code, exc.message, status)


def refusal_outcome(exc: Exception) -> Outcome:
    # Before `RateLimited`: "stopped on purpose" and "going too fast" are different answers.
    if isinstance(exc, Suspended):
        return Outcome.SUSPENDED
    if isinstance(exc, AttachmentRejected | ThinkingRejected | SchemaRejected | EmbeddingRejected):
        return Outcome.INVALID_REQUEST
    if isinstance(exc, NoCapableModel | AmbiguousModel | RegionNotAllowed):
        return Outcome.NO_CAPABLE_MODEL
    if isinstance(exc, RateLimited):
        return Outcome.RATE_LIMITED
    if isinstance(exc, BudgetExceeded):
        return Outcome.BUDGET_EXCEEDED
    if isinstance(exc, PipelineRejected):
        return Outcome.BLOCKED_BY_PIPELINE
    if isinstance(exc, UpstreamError):
        return Outcome.UPSTREAM_ERROR
    if isinstance(exc, GeminiHTTPError):
        return _REFUSAL_OUTCOMES.get(exc.code, Outcome.INVALID_REQUEST)
    return Outcome.INVALID_REQUEST
