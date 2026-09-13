"""Vertex's embedding verb, `:predict` (`FRD-115` §5.1).

Not the Generative Language API's pair: Vertex does not serve `batchEmbedContents` (404) and
refuses `embedContent` for an API key (401). `:predict` is the method it offers every embedding
model, with one body shape.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.core.canonical import CanonicalEmbeddingRequest
from aira_gateway.upstreams.base import UpstreamError


def predict_body(request: CanonicalEmbeddingRequest, text: str) -> dict[str, Any]:
    """One text as a `:predict` body.

    One instance per call, because `gemini-embedding-001` accepts one per request. ``autoTruncate``
    is off: Vertex's default embeds the beginning of an over-long text and answers 200 — a vector
    for part of the input, the silent degradation `FRD-124` refuses. The provider's 400 instead.
    """
    instance: dict[str, Any] = {"content": text}
    if request.task_type is not None:
        instance["task_type"] = request.task_type
    parameters: dict[str, Any] = {"autoTruncate": False}
    if request.dimensions is not None:
        parameters["outputDimensionality"] = request.dimensions
    return {"instances": [instance], "parameters": parameters}


def predict_tokens(data: dict[str, Any]) -> int | None:
    """The input tokens a `:predict` answer reports, or ``None`` when it reports none."""
    predictions = data.get("predictions") or []
    embeddings = (predictions[0].get("embeddings") or {}) if predictions else {}
    count = (embeddings.get("statistics") or {}).get("token_count")
    return int(count) if isinstance(count, int | float) else None


def predict_values(data: dict[str, Any]) -> list[float]:
    """The vector a `:predict` answer carries. An answer without one is the provider's fault."""
    predictions = data.get("predictions") or []
    values = (predictions[0].get("embeddings") or {}).get("values") if predictions else None
    if not values:
        raise UpstreamError("Vertex answered the embedding request without a vector.", 502)
    return [float(value) for value in values]
