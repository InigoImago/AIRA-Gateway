"""The task written into the text, for a model that takes no `task_type` (`FRD-146` FR-7).

`gemini-embedding-2` refuses the `task_type` field; Google documents a prompt structure instead,
one per task, and the predecessor embeds every text that way. An index built through it holds these
vectors, so this surface writes the same prompts — and only this surface: the Gemini interface sends
what its caller wrote, and its vectors do not move.
"""

from __future__ import annotations

from aira_gateway.catalog import ModelDeclaration

#: The models whose task goes into the text, by the start of the name the platform knows them by.
PROMPTED_MODELS = ("gemini-embedding-2",)

#: Google's structure for each task type. A document has no title on this surface's contract, which
#: Google's structure spells `none`.
PROMPTS: dict[str, str] = {
    "RETRIEVAL_QUERY": "task: search result | query: {text}",
    "RETRIEVAL_DOCUMENT": "title: none | text: {text}",
    "QUESTION_ANSWERING": "task: question answering | query: {text}",
    "FACT_VERIFICATION": "task: fact checking | query: {text}",
    "CODE_RETRIEVAL_QUERY": "task: code retrieval | query: {text}",
    "CLASSIFICATION": "task: classification | query: {text}",
    "CLUSTERING": "task: clustering | query: {text}",
    "SEMANTIC_SIMILARITY": "task: sentence similarity | query: {text}",
}


def is_prompted(declaration: ModelDeclaration) -> bool:
    """Whether ``declaration``'s model takes its task in the text — decided by the name the platform
    knows it by, so an alias of `gemini-embedding-2` is embedded like the model it is."""
    return declaration.wire_name.startswith(PROMPTED_MODELS)


def prompted(task_type: str, text: str) -> str:
    """``text`` with ``task_type``'s prompt around it."""
    return PROMPTS[task_type].format(text=text)
