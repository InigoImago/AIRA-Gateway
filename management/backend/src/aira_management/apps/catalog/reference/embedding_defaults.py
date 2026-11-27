"""What an embedding model takes, where its vendor says (`FRD-155`).

The list states that a model embeds, never which task types it reads, whether one request may carry
several texts or which widths it returns. The gateway refuses a task type the catalogue does not
declare rather than send it to be ignored — so a draft without them refused every embedding from
LangChain, which always names one, although the model takes them.

So a draft proposes what the vendor documents for **that model**, named one by one like the
thinking defaults: a vendor decides this per model. Where it documents that a model takes no task
type, none is proposed, and a request naming one stays refused: the vector would be the same as
without it, under a 200.
"""

from __future__ import annotations

from typing import Any

#: Google's task types (`TaskType`), in the order its reference lists them.
GOOGLE_TASK_TYPES = (
    "RETRIEVAL_QUERY",
    "RETRIEVAL_DOCUMENT",
    "SEMANTIC_SIMILARITY",
    "CLASSIFICATION",
    "CLUSTERING",
    "QUESTION_ANSWERING",
    "FACT_VERIFICATION",
    "CODE_RETRIEVAL_QUERY",
)

#: Per model, the embedding block its vendor documents. Several texts in one request are served on
#: either platform — the Gemini API's batch method, and one call per text on Vertex AI.
VENDOR_EMBEDDINGS: dict[str, dict[str, Any]] = {
    # Google, "Embeddings" (https://ai.google.dev/gemini-api/docs/embeddings) and "Choose an
    # embeddings task type" (https://docs.cloud.google.com/vertex-ai/generative-ai/docs/embeddings/
    # task-types): every task type; 3072 components unless fewer are asked for, and 768, 1536 and
    # 3072 the widths Google recommends.
    "gemini-embedding-001": {
        "task_types": list(GOOGLE_TASK_TYPES),
        "supports_batch": True,
        "dimensions": [768, 1536, 3072],
    },
    # Google, "Gemini Embedding 2" (https://docs.cloud.google.com/gemini-enterprise-agent-platform/
    # models/gemini/embedding-2): no task type — the task is written into the text instead — so
    # none is declared.
    "gemini-embedding-2": {"supports_batch": True},
}


def with_vendor_embedding(name: str, notes: list[str]) -> dict[str, Any] | None:
    """The embedding block the vendor documents for the model ``name``, or ``None`` where it
    documents none; ``notes`` says what was proposed."""
    block = VENDOR_EMBEDDINGS.get(name)
    if block is None:
        return None
    if block.get("task_types"):
        notes.append(
            "task types, batches and widths proposed — what Google documents for this model, so "
            "a client naming a task type is served"
        )
    else:
        notes.append(
            "batches proposed and no task type — Google documents the task written into the text "
            "for this model, so a request naming one is refused rather than sent to be ignored"
        )
    # A copy: the draft is handed on and may be changed, the table must not be.
    return {key: list(value) if isinstance(value, list) else value for key, value in block.items()}
