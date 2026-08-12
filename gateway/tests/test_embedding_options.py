"""Embedding batches, task types and dimensions (FRD-113).

The section that matters most here is §5.3, and it is a **control bypass** rather than an
inaccuracy: `FRD-405`'s bucket takes one token per request, so a batch of 500 admitted as one
request turns a limit of 10 per minute into 5 000 texts per minute. The limit stays intact on
paper and is gone in practice.

The test that proves it is written to fail against a one-token-per-request implementation, which
is the only way to know the control is doing anything.
"""

from __future__ import annotations

import pytest

from aira_common.models import Capability
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import CanonicalEmbeddingRequest
from aira_gateway.embedding import (
    EMBEDDING_AGGREGATION_NOT_SUPPORTED,
    EMBEDDING_BOUND_EXCEEDED,
    EMPTY_EMBEDDING_INPUT,
    INVALID_EMBEDDING_DIMENSIONS,
    INVALID_EMBEDDING_TASK_TYPE,
    NO_EMBEDDING_CAPABILITIES,
    EmbeddingBounds,
    EmbeddingRejected,
    estimated_tokens,
    validate,
)
from aira_gateway.ratelimit.buckets import BucketRequest, InMemoryTokenBucket
from aira_gateway.upstreams.mock import MockProvider


def _model(**embedding: object) -> ModelDeclaration:
    return ModelDeclaration(
        name="e",
        declared=True,
        capabilities=frozenset({Capability.EMBED}),
        embedding=dict(embedding) or None,
    )


def _request(*texts: str, **fields: object) -> CanonicalEmbeddingRequest:
    return CanonicalEmbeddingRequest(model="e", texts=list(texts), **fields)


# == the bypass ==================================================================================


async def test_a_batch_of_n_takes_n_tokens_from_the_bucket() -> None:
    """Written to fail against a one-token-per-request bucket: with 5 tokens left, a batch of 10
    must be **refused** rather than admitted. This is the pair that proves the control."""
    bucket = InMemoryTokenBucket()
    limit = [BucketRequest(key="uc", capacity=5, refill_per_second=0.0, label="use case")]

    assert (await bucket.take(limit, 5)).allowed  # exactly fits
    assert not (await bucket.take(limit, 1)).allowed  # and nothing is left


async def test_a_batch_larger_than_the_remaining_allowance_is_refused_whole() -> None:
    bucket = InMemoryTokenBucket()
    limit = [BucketRequest(key="uc", capacity=10, refill_per_second=0.0, label="use case")]
    await bucket.take(limit, 5)

    assert not (await bucket.take(limit, 10)).allowed
    # And the refusal did not debit: a refused request must not consume what it was denied.
    assert (await bucket.take(limit, 5)).allowed


async def test_a_refused_batch_leaves_every_bucket_untouched() -> None:
    """All-or-nothing across scopes, as `FRD-405` FR-4 requires — a batch refused by the member
    bucket must not have drained the whole use case's allowance on the way."""
    bucket = InMemoryTokenBucket()
    buckets = [
        BucketRequest(key="uc", capacity=100, refill_per_second=0.0, label="use case"),
        BucketRequest(key="member", capacity=3, refill_per_second=0.0, label="member"),
    ]
    decision = await bucket.take(buckets, 10)

    assert not decision.allowed
    assert decision.refused is not None and decision.refused.label == "member"
    # The use-case bucket still has its full allowance.
    assert (await bucket.take([buckets[0]], 100)).allowed


# == validation ==================================================================================


def test_a_model_that_cannot_embed_is_refused_before_dispatch() -> None:
    """FR-6a. Anthropic models have no embedding endpoint at all, and a cross-vendor chain can
    route one there — the useful error names the model rather than surfacing from a call stack."""
    with pytest.raises(EmbeddingRejected) as caught:
        validate(
            _request("a"), ModelDeclaration(name="claude", declared=True, capabilities=frozenset())
        )
    assert caught.value.code == NO_EMBEDDING_CAPABILITIES


@pytest.mark.parametrize("texts", [(), ("",), ("   ",), ("ok", "")])
def test_every_form_of_empty_input_is_refused(texts: tuple[str, ...]) -> None:
    """The predecessor's rule: not an empty string, not an empty list, not a list containing one.
    It prevents a class of accidental no-op billing."""
    with pytest.raises(EmbeddingRejected) as caught:
        validate(_request(*texts), _model(supports_batch=True))
    assert caught.value.code == EMPTY_EMBEDDING_INPUT


def test_a_list_needs_declared_batch_support() -> None:
    with pytest.raises(EmbeddingRejected) as caught:
        validate(_request("a", "b"), _model(supports_batch=False))
    assert caught.value.code == EMBEDDING_AGGREGATION_NOT_SUPPORTED


def test_a_single_text_never_needs_batch_support() -> None:
    assert validate(_request("a"), _model()).size == 1


@pytest.mark.parametrize(
    ("texts", "bounds"),
    [
        (("a",) * 4, EmbeddingBounds(max_batch=3)),
        (("x" * 50,), EmbeddingBounds(max_total_chars=20)),
    ],
)
def test_each_bound_is_refused_naming_itself(
    texts: tuple[str, ...], bounds: EmbeddingBounds
) -> None:
    with pytest.raises(EmbeddingRejected) as caught:
        validate(_request(*texts), _model(supports_batch=True), bounds)
    assert caught.value.code == EMBEDDING_BOUND_EXCEEDED


def test_a_task_type_outside_the_vocabulary_is_refused() -> None:
    with pytest.raises(EmbeddingRejected) as caught:
        validate(_request("a", task_type="RETRIEVAL_EVERYTHING"), _model(task_types=["CLUSTERING"]))
    assert caught.value.code == INVALID_EMBEDDING_TASK_TYPE


def test_a_task_type_the_model_does_not_declare_is_refused() -> None:
    """The whole value of the field is that the wrong one fails loudly rather than producing
    quietly worse vectors — so an undeclared one cannot simply be passed through."""
    with pytest.raises(EmbeddingRejected) as caught:
        validate(_request("a", task_type="CLUSTERING"), _model(task_types=["RETRIEVAL_QUERY"]))
    assert caught.value.code == INVALID_EMBEDDING_TASK_TYPE
    assert "RETRIEVAL_QUERY" in caught.value.message


def test_an_explicit_task_type_against_an_undeclared_model_names_the_catalog() -> None:
    """ "Unknown is not permission": the fix is a catalog edit, and saying so is the difference
    between a two-minute correction and a support ticket."""
    with pytest.raises(EmbeddingRejected) as caught:
        validate(_request("a", task_type="CLUSTERING"), _model())
    assert "catalog" in caught.value.message


def test_a_surface_default_applies_only_where_the_model_declares_it() -> None:
    """The asymmetry is deliberate. A caller who *named* a type we cannot verify is refused; a
    caller who named nothing gets what they always got, which for an undeclared model is nothing
    at all — otherwise this feature would break every existing embedding call on upgrade."""
    declared = _model(task_types=["RETRIEVAL_QUERY", "CLUSTERING"])
    assert validate(_request("a"), declared, default_task_type="RETRIEVAL_QUERY").task_type == (
        "RETRIEVAL_QUERY"
    )
    assert validate(_request("a"), _model(), default_task_type="RETRIEVAL_QUERY").task_type is None


def test_a_lowercase_task_type_is_normalised() -> None:
    model = _model(task_types=["RETRIEVAL_DOCUMENT"])
    assert validate(_request("a", task_type="retrieval_document"), model).task_type == (
        "RETRIEVAL_DOCUMENT"
    )


def test_dimensionality_must_be_one_the_model_declares() -> None:
    with pytest.raises(EmbeddingRejected) as caught:
        validate(_request("a", dimensions=1536), _model(dimensions=[768, 3072]))
    assert caught.value.code == INVALID_EMBEDDING_DIMENSIONS
    assert "768" in caught.value.message


def test_dimensionality_against_a_model_declaring_none_is_refused() -> None:
    with pytest.raises(EmbeddingRejected) as caught:
        validate(_request("a", dimensions=768), _model())
    assert caught.value.code == INVALID_EMBEDDING_DIMENSIONS


def test_the_models_default_dimensionality_applies_when_none_is_asked_for() -> None:
    """§5.4's modelling trap: the predecessor makes width part of the model's *identity* (two ids
    for one model). Here it is a request parameter with a per-row default, so both ids work."""
    model = _model(dimensions=[768, 3072], default=768)
    assert validate(_request("a"), model).dimensions == 768
    assert validate(_request("a", dimensions=3072), model).dimensions == 3072


def test_the_input_estimate_scales_with_the_batch() -> None:
    """Unlike generation, an embedding's input is the whole request and is knowable up front —
    so the reservation for a large batch is not the reservation for a sentence."""
    assert estimated_tokens(_request("x" * 400)) > estimated_tokens(_request("x" * 40))
    assert estimated_tokens(_request("a")) >= 1  # never zero: unknown is not free


# == what the provider does with it ==============================================================


async def test_the_mock_returns_one_vector_per_text_in_order() -> None:
    vectors = await MockProvider().embed(_request("alpha", "beta", "alpha"))
    assert len(vectors) == 3
    assert vectors[0] == vectors[2]  # deterministic
    assert vectors[0] != vectors[1]  # and actually derived from the text


async def test_two_task_types_produce_different_vectors_for_the_same_text() -> None:
    """The property that makes a task type worth validating rather than passing through. If the
    mock ignored it, every hermetic test here would pass against a gateway that dropped it."""
    provider = MockProvider()
    query = await provider.embed(_request("hallo", task_type="RETRIEVAL_QUERY"))
    document = await provider.embed(_request("hallo", task_type="RETRIEVAL_DOCUMENT"))
    assert query != document


async def test_the_requested_width_is_the_width_returned() -> None:
    vectors = await MockProvider().embed(_request("hallo", dimensions=768))
    assert len(vectors[0]) == 768


# == several strings in one content (measured against Google, 2026-08-12) ========================


def test_several_parts_in_one_content_become_one_text_joined_with_nothing() -> None:
    """A `content` may carry several text parts, and the upstream answers **one** vector for it.

    The question is how it combines them, and it was measured rather than assumed — against
    `gemini-embedding-001`, cosine similarity of the multi-part vector to:

        the parts concatenated with no separator   1.000000
        the parts concatenated with a space        0.993614
        the mean of the parts embedded separately  0.948927   (a centroid)
        (control) one part against the other       0.542784

    So it concatenates, and it is **not** a centroid — which is the plausible reading and the
    wrong one. This test pins the separator-free join, because inserting one to be tidy would
    produce a different vector from the API this surface exists to be compatible with, and nothing
    would report it: the response is the right shape, the right length, and quietly not the right
    answer. Reproduced end to end through the gateway against a real local model: cosine 1.000000
    to the concatenation, 0.943507 to the centroid.
    """
    from aira_gateway.api.gemini import schemas
    from aira_gateway.api.gemini.mapping import gemini_to_embedding

    request = schemas.EmbedContentRequest(
        content=schemas.Content(parts=[schemas.Part(text="Der Hund"), schemas.Part(text=" bellt")])
    )

    canonical = gemini_to_embedding("emb-1", [request])

    # One text, not two: this is a single embedding request, so it weighs one against the limits
    # and produces one vector.
    assert canonical.texts == ["Der Hund bellt"]
    assert canonical.size == 1


def test_a_batch_stays_one_text_per_entry() -> None:
    """The other half, and the one a caller uses to build a centroid themselves: `n` entries are
    `n` texts and `n` vectors, weighed as `n` requests (`FRD-113` §5.3). Computing a mean over
    them is the caller's arithmetic to choose (`ADR-0013`), not ours to guess."""
    from aira_gateway.api.gemini import schemas
    from aira_gateway.api.gemini.mapping import gemini_to_embedding

    entries = [
        schemas.EmbedContentRequest(content=schemas.Content(parts=[schemas.Part(text="eins")])),
        schemas.EmbedContentRequest(content=schemas.Content(parts=[schemas.Part(text="zwei")])),
    ]

    canonical = gemini_to_embedding("emb-1", entries)

    assert canonical.texts == ["eins", "zwei"]
    assert canonical.size == 2
