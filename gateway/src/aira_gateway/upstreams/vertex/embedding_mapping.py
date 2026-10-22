"""Vertex's two embedding verbs, `:predict` (`FRD-115` §5.1) and `:embedContent` (`FRD-146`).

Not the Generative Language API's pair: Vertex does not serve `batchEmbedContents` (404).
`:predict` is the method it offers the models that take a `task_type`, with one body shape.
`gemini-embedding-2` takes no `task_type` — Google documents the task written into the text
instead — and combines several parts of one content into one vector, which `:predict` cannot say;
its method is `:embedContent`, which refuses an API key (401) and needs the installation's identity.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.core.canonical import CanonicalEmbeddingRequest
from aira_gateway.upstreams.base import UpstreamError

#: The models embedded through `:embedContent`, by the start of the name Google knows them by.
EMBED_CONTENT_MODELS = ("gemini-embedding-2",)


def embeds_content(wire_model: str) -> bool:
    """Whether ``wire_model`` is embedded through `:embedContent` rather than `:predict`."""
    return wire_model.startswith(EMBED_CONTENT_MODELS)


def embed_content_body(request: CanonicalEmbeddingRequest, texts: list[str]) -> dict[str, Any]:
    """``texts`` as one `:embedContent` body: one part each, of one content, so one vector.

    ``autoTruncate`` is off for the reason `predict_body` gives. The task type goes in the config
    only where the caller named one and it is not already in the text (``task_in_text``): sending
    both would ask the model for the task twice.
    """
    config: dict[str, Any] = {"autoTruncate": False}
    if request.dimensions is not None:
        config["outputDimensionality"] = request.dimensions
    if request.task_type is not None and not request.task_in_text:
        config["taskType"] = request.task_type
    return {
        "content": {"parts": [{"text": text} for text in texts]},
        "embedContentConfig": config,
    }


def embed_content_values(data: dict[str, Any]) -> list[float]:
    """The vector an `:embedContent` answer carries; one without is the provider's fault."""
    values = (data.get("embedding") or {}).get("values")
    if not values:
        raise UpstreamError("Vertex answered the embedding request without a vector.", 502)
    return [float(value) for value in values]


def embed_content_tokens(data: dict[str, Any]) -> int | None:
    """The input tokens an `:embedContent` answer reports, or ``None`` when it reports none."""
    usage = data.get("usageMetadata") or {}
    count = usage.get("promptTokenCount", usage.get("totalTokenCount"))
    return int(count) if isinstance(count, int | float) and not isinstance(count, bool) else None


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
