"""Validating an embedding request against the model that will serve it (FRD-113).

Three caller-visible options — batch, task type, dimensionality — each refused rather than
approximated when the model does not offer it, because each fails *silently* if got wrong: the
wrong task type retrieves measurably worse, an unsupported batch split into single calls bypasses
the rate limit (§5.3), and the wrong dimensionality does not fit the consumer's index.

Metering lives with the caller (``units`` on the pre-dispatch gate); this module only decides
whether the request is answerable at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from aira_common.models import Capability
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import CanonicalEmbeddingRequest

#: A closed set: the whole value of the field is that a wrong one fails loudly
#: rather than producing quietly worse vectors, and a passthrough string cannot do that.
TASK_TYPES: frozenset[str] = frozenset(
    {
        "RETRIEVAL_QUERY",
        "RETRIEVAL_DOCUMENT",
        "SEMANTIC_SIMILARITY",
        "CLASSIFICATION",
        "CLUSTERING",
        "CODE_RETRIEVAL_QUERY",
        "QUESTION_ANSWERING",
        "FACT_VERIFICATION",
    }
)

#: The predecessor's default, kept so a migrating caller that sends nothing gets what it got.
DEFAULT_TASK_TYPE = "RETRIEVAL_QUERY"

INVALID_EMBEDDING_TASK_TYPE = "INVALID_EMBEDDING_TASK_TYPE"
EMBEDDING_AGGREGATION_NOT_SUPPORTED = "EMBEDDING_AGGREGATION_NOT_SUPPORTED"
NO_EMBEDDING_CAPABILITIES = "NO_EMBEDDING_CAPABILITIES"
INVALID_EMBEDDING_DIMENSIONS = "INVALID_EMBEDDING_DIMENSIONS"
EMBEDDING_BOUND_EXCEEDED = "EMBEDDING_BOUND_EXCEEDED"
EMPTY_EMBEDDING_INPUT = "EMPTY_EMBEDDING_INPUT"


class EmbeddingRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class EmbeddingBounds:
    """FR-5: chosen together with the rate limits.

    A batch bound larger than any configured bucket makes large batches fail permanently, so the
    default is modest and the refusal names which of the two said no.
    """

    max_batch: int = 256
    max_total_chars: int = 1_000_000


def validate(
    request: CanonicalEmbeddingRequest,
    declaration: ModelDeclaration,
    bounds: EmbeddingBounds | None = None,
    *,
    default_task_type: str | None = None,
) -> CanonicalEmbeddingRequest:
    """Return the request with defaults applied, or raise :class:`EmbeddingRejected`.

    ``default_task_type`` is a *surface's* compatibility default (KIRA passes ``RETRIEVAL_QUERY``,
    Gemini nothing), applied only where the model declares it: a named type we cannot verify is
    refused, an implicit one is simply not sent.
    """
    bounds = bounds or EmbeddingBounds()

    if not declaration.can(Capability.EMBED):
        # Refused before dispatch: Anthropic models have no embedding endpoint, and a cross-vendor
        # chain can send an embedding to one (`FRD-113` FR-6a).
        raise EmbeddingRejected(
            NO_EMBEDDING_CAPABILITIES,
            f"Model '{declaration.name}' does not support embedding.",
        )

    texts = request.texts
    if not texts or any(not text.strip() for text in texts):
        # An empty string, an empty list, or a list containing one — accidental no-op billing.
        raise EmbeddingRejected(
            EMPTY_EMBEDDING_INPUT, "Embedding input must be a non-empty text, or a list of them."
        )
    if len(texts) > bounds.max_batch:
        raise EmbeddingRejected(
            EMBEDDING_BOUND_EXCEEDED,
            f"A batch of {len(texts)} exceeds the {bounds.max_batch} texts this gateway accepts "
            "in one request.",
        )
    total = sum(len(text) for text in texts)
    if total > bounds.max_total_chars:
        raise EmbeddingRejected(
            EMBEDDING_BOUND_EXCEEDED,
            f"The batch totals {total} characters, above the {bounds.max_total_chars} accepted.",
        )

    if len(texts) > 1 and not declaration.supports_batch:
        raise EmbeddingRejected(
            EMBEDDING_AGGREGATION_NOT_SUPPORTED,
            f"Model '{declaration.name}' does not accept a list of texts. Send them one at a "
            "time, or use a model whose catalog entry declares batch support.",
        )

    task_type = _task_type(request.task_type, declaration, default_task_type)
    dimensions = _dimensions(request.dimensions, declaration)
    return request.model_copy(update={"task_type": task_type, "dimensions": dimensions})


def _task_type(
    requested: str | None, declaration: ModelDeclaration, default: str | None
) -> str | None:
    declared = declaration.embedding_task_types
    if requested is None:
        # The surface's default, **only where the model declares it** — never a guess on the
        # caller's behalf, so nobody's existing vectors move.
        return default if default is not None and default in declared else None

    normalised = requested.strip().upper()
    if normalised not in TASK_TYPES:
        raise EmbeddingRejected(
            INVALID_EMBEDDING_TASK_TYPE,
            f"'{requested}' is not an embedding task type. Known: {sorted(TASK_TYPES)}.",
        )
    if not declared:
        # Unknown is not permission: refused naming the catalog, not sent upstream to be ignored.
        raise EmbeddingRejected(
            INVALID_EMBEDDING_TASK_TYPE,
            f"The model catalog declares no embedding task types for '{declaration.name}', so "
            f"'{normalised}' cannot be honoured. Declaring them is a catalog edit.",
        )
    if normalised not in declared:
        raise EmbeddingRejected(
            INVALID_EMBEDDING_TASK_TYPE,
            f"Model '{declaration.name}' does not support the task type '{normalised}'. "
            f"It declares {sorted(declared)}.",
        )
    return normalised


def _dimensions(requested: int | None, declaration: ModelDeclaration) -> int | None:
    declared = declaration.embedding_dimensions
    if requested is None:
        return declaration.default_dimensions
    if not declared:
        raise EmbeddingRejected(
            INVALID_EMBEDDING_DIMENSIONS,
            f"The model catalog declares no output dimensionality for '{declaration.name}', so "
            f"{requested} cannot be requested.",
        )
    if requested not in declared:
        raise EmbeddingRejected(
            INVALID_EMBEDDING_DIMENSIONS,
            f"Model '{declaration.name}' produces vectors of {sorted(declared)} components, "
            f"not {requested}.",
        )
    return requested


def estimated_tokens(request: CanonicalEmbeddingRequest) -> int:
    """What the batch is expected to cost in input tokens.

    Knowable up front — the input is the whole request. Four characters per token errs in the safe
    direction for short texts, which is what a reservation wants.
    """
    return max(1, sum(len(text) for text in request.texts) // 4)
