"""Validating an embedding request against the model that will serve it (FRD-113).

Three caller-visible options — batch, task type, dimensionality — and each is refused rather than
approximated when the model does not offer it, for the same reason throughout: every one of them
fails *silently* if got wrong.

- The wrong **task type** produces vectors that work, sit in the right space, and retrieve
  measurably worse. Nothing about the response shows it; the symptom is a search that is subtly
  bad months later.
- An unsupported **batch** embedded one text at a time would cost N requests of quota against a
  limit of one — the control bypass §5.3 is about, arriving through the back door of politeness.
- The wrong **dimensionality** does not fit the index the consumer already built.

Metering lives with the caller (``units`` on the pre-dispatch gate); what is here is the decision
about whether the request is answerable at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from aira_common.models import Capability
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import CanonicalEmbeddingRequest

#: `kira_api.md` §4.4. A closed set: the whole value of the field is that a wrong one fails loudly
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
    """FR-5, and the two figures have to be chosen together with the rate limits.

    A batch bound larger than any configured bucket makes large batches fail permanently — the
    request is admissible here and refused two lines later by a limit it can never satisfy. So the
    default is deliberately modest, and the refusal names which of the two said no.
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

    ``default_task_type`` is a *surface's* compatibility default — the KIRA surface passes the
    predecessor's ``RETRIEVAL_QUERY``, the Gemini surface passes nothing. It is applied only where
    the model declares it, which is the difference between a default and a request: a caller who
    named a type we cannot verify is refused, a caller who named nothing is served exactly as
    before. Refusing the *implicit* one would break every existing embedding call against a model
    nobody has declared task types for, which is most of them.
    """
    bounds = bounds or EmbeddingBounds()

    if not declaration.can(Capability.EMBED):
        # Refused here, before dispatch, rather than by an adapter raising deep in the stack:
        # Anthropic models have no embedding endpoint at all, and with cross-vendor routing a
        # chain can send an embedding to one of them (`FRD-113` FR-6a).
        raise EmbeddingRejected(
            NO_EMBEDDING_CAPABILITIES,
            f"Model '{declaration.name}' does not support embedding.",
        )

    texts = request.texts
    if not texts or any(not text.strip() for text in texts):
        # All three forms the predecessor refuses — an empty string, an empty list, and a list
        # containing an empty string. It prevents a class of accidental no-op billing.
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
        # The surface's default, and **only where the model declares it**. Sending a task type a
        # model has not declared would be guessing on the caller's behalf; sending none is what
        # every embedding AIRA serves today already does, so nobody's existing vectors move.
        return default if default is not None and default in declared else None

    normalised = requested.strip().upper()
    if normalised not in TASK_TYPES:
        raise EmbeddingRejected(
            INVALID_EMBEDDING_TASK_TYPE,
            f"'{requested}' is not an embedding task type. Known: {sorted(TASK_TYPES)}.",
        )
    if not declared:
        # "Unknown is not permission": a model nobody has declared task types for is served with
        # the default, and an explicit one is refused naming the catalog rather than sent upstream
        # to fail — or worse, accepted and quietly ignored.
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

    Unlike generation, this is knowable up front — the input is the whole request. Four characters
    per token is the usual coarse approximation and it is wrong in the safe direction for short
    texts, which is what a reservation wants.
    """
    return max(1, sum(len(text) for text in request.texts) // 4)
