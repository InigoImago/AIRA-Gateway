"""KIRA ⇄ canonical (FRD-107). Pure functions, free of FastAPI, like `api/gemini/mapping.py`.

Nothing is approximated: a field that cannot be honoured is refused here rather than dropped,
because a dropped field produces an answer that is wrong for a reason the caller cannot see. The
two exceptions are the predecessor's own and the owner's decision (`FRD-146` FR-14) — a thinking
count beside a mode that takes none, an attachment in a system instruction — and both are named in
the response header.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.api.kira import embedding_prompts, schemas, tool_schema
from aira_gateway.api.kira.errors import (
    EXTERNAL_KI_API_ERROR,
    INVALID_FUNCTION_RESPONSE,
    INVALID_TOOL_CONFIGURATION,
    VALIDATION_ERROR,
    KiraError,
)
from aira_gateway.attachments import Limits, check_media_type, check_signature, decode
from aira_gateway.core.canonical import (
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalPart,
    CanonicalRequest,
    CanonicalRerankRequest,
    CanonicalResponse,
    DataPart,
    RankedRecord,
    RerankRecord,
    Role,
    TextPart,
    Thinking,
    ToolCallPart,
    ToolDeclaration,
    ToolResultPart,
)
from aira_gateway.core.json_schema import parse_json_schema
from aira_gateway.core.schema import SchemaBounds, SchemaRejected
from aira_gateway.core.schema import parse as parse_schema
from aira_gateway.embedding import DEFAULT_TASK_TYPE
from aira_gateway.thinking import mode_from

#: What the predecessor puts between two text parts of one message: it sends them as one string.
#: Kept as separate parts, each adapter would render them differently ("HalloWelt" on one).
TEXT_PART_SEPARATOR = "\n"

#: Where a call's signature travels in ``opaque_metadata``, there and back — the predecessor's key,
#: Gemini's own field name. **The only key read.** The predecessor copies every key of
#: ``opaque_metadata`` onto the provider's part beside the call; here a caller's object never
#: becomes provider fields, or it could write any field of a part — a file reference among them.
SIGNATURE_KEY = "thoughtSignature"

#: A function name as every dialect this gateway reaches accepts one: Gemini's rule, the narrowest.
TOOL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]{0,63}")

#: How many functions one request may offer. Each is a schema the model reads with every turn and
#: the reservation counts; past this, a request is a catalogue rather than a conversation.
MAX_TOOLS = 128


def _parts(
    content: schemas.RequestContent, limits: Limits, offset: int, *, attachments: bool = True
) -> list[CanonicalPart]:
    """The predecessor's parts, in the predecessor's order (`FRD-107` FR-2, `FRD-146` FR-18).

    **Documents first, then the text**, as the predecessor builds a turn: every attachment in the
    order sent, then the texts joined into one (`TEXT_PART_SEPARATOR`). The same turn sent in the
    other order is a different prompt to the model, and a migrating client's prompts were written
    against this one. **Except in a turn with function parts**, which the predecessor keeps in the
    order sent: a call and its result are the conversation, and the text around them belongs where
    it was. There each *run* of text becomes one text. ``attachments=False`` leaves attachments out
    — the predecessor ignores one in a system instruction (`FRD-146` FR-14);
    `schemas.ignored_values` names it.
    """
    if content.kinds():
        return _in_order(content, limits, offset)
    documents: list[CanonicalPart] = []
    texts: list[str] = []
    for local, raw in enumerate(content.parts):
        if "text" in raw:
            # The type was checked when the request was parsed (`RequestContent`).
            texts.append(raw["text"])
        elif attachments:
            documents.append(_attachment(raw, limits, offset + local))
    return [*documents, *([TextPart(text=TEXT_PART_SEPARATOR.join(texts))] if texts else [])]


def _in_order(content: schemas.RequestContent, limits: Limits, offset: int) -> list[CanonicalPart]:
    """A turn with function parts, part by part; a run of text parts is one text."""
    parts: list[CanonicalPart] = []
    pending: list[str] = []

    def _flush() -> None:
        if pending:
            parts.append(TextPart(text=TEXT_PART_SEPARATOR.join(pending)))
            pending.clear()

    for local, raw in enumerate(content.parts):
        kind = raw.get("type")
        if kind == schemas.FUNCTION_CALL:
            _flush()
            metadata = raw.get("opaque_metadata") or {}
            signature = metadata.get(SIGNATURE_KEY)
            parts.append(
                ToolCallPart(
                    # A call without an id can be replayed and never answered, as in the
                    # predecessor; every dialect needs one, so it gets one nobody can name.
                    id=str(raw.get("id") or f"unanswerable-{uuid.uuid4().hex}"),
                    name=str(raw["name"]),
                    arguments=dict(raw.get("arguments") or {}),
                    signature=signature if isinstance(signature, str) else None,
                )
            )
        elif kind == schemas.FUNCTION_RESULT:
            _flush()
            result = raw["result"]
            if raw.get("error") is not None:
                # What running it reported, beside what it returned, as the predecessor sends it:
                # the model reads a failed call as failed rather than as an odd result.
                result = {"result": result, "error": raw["error"]}
            parts.append(
                ToolResultPart(
                    call_id=str(raw["call_id"]),
                    name=str(raw["name"]),
                    # Serialised once, here, as the Gemini surface does: dialects disagree on string
                    # versus object, and a text result is sent as the text it is.
                    content=result
                    if isinstance(result, str)
                    else json.dumps(result, ensure_ascii=False),
                )
            )
        elif "text" in raw:
            pending.append(raw["text"])
        else:
            _flush()
            parts.append(_attachment(raw, limits, offset + local))
    _flush()
    return parts


def _attachment(raw: dict[str, Any], limits: Limits, index: int) -> DataPart:
    """One attachment, through every check an attachment gets."""
    media_type = str(raw.get("mime_type") or raw.get("mimeType"))
    check_media_type(media_type, limits, index=index)
    data = decode(str(raw.get("data", "")), index=index)
    check_signature(media_type, data, index=index)
    return DataPart(media_type=media_type, data=data)


# == functions (FRD-146 FR-1 to FR-3) =============================================================


def tools_of(
    request: schemas.ChatRequest, bounds: SchemaBounds | None = None
) -> tuple[ToolDeclaration, ...]:
    """The functions offered, each checked as the predecessor checks a declaration.

    Every fault is `INVALID_TOOL_CONFIGURATION`, the code a migrating client switches on; a field of
    the wrong *type* is still the parser's `VALIDATION_ERROR`. Function parts in a conversation that
    offers no functions are refused too: a model is asked to continue a call it was never offered.
    """
    specs = request.tools or []
    if not specs:
        if _carries_function_parts(request):
            raise _tool_fault(
                "The conversation carries function parts, but the request declares no tools. "
                "Send the tools with every request of the conversation."
            )
        return ()
    if len(specs) > MAX_TOOLS:
        raise _tool_fault(
            f"{len(specs)} tools are more than the {MAX_TOOLS} one request may offer."
        )
    seen: set[str] = set()
    declarations: list[ToolDeclaration] = []
    for index, spec in enumerate(specs):
        if not TOOL_NAME.fullmatch(spec.name):
            raise _tool_fault(
                f"tools[{index}]: '{spec.name[:80]}' is not a function name: it starts with a "
                "letter or an underscore and has at most 64 letters, digits, '_', '.' or '-'."
            )
        if spec.name in seen:
            raise _tool_fault(f"tools[{index}]: '{spec.name}' is declared twice.")
        seen.add(spec.name)
        if not spec.description.strip():
            raise _tool_fault(
                f"tools[{index}]: the description of '{spec.name}' is blank. The model chooses a "
                "function by what its description says."
            )
        parameters = spec.parameters
        rule = tool_schema.fault(parameters)
        if rule:
            raise _tool_fault(f"tools[{index}]: '{spec.name}': {rule}.")
        if parameters.get("type") != "object":
            raise _tool_fault(
                f"tools[{index}]: the parameters of '{spec.name}' are a JSON Schema of type "
                f"'{parameters.get('type')}'; a function takes an object."
            )
        try:
            schema = parse_json_schema(parameters, bounds, what=f"The parameters of '{spec.name}'")
        except SchemaRejected as exc:
            raise _tool_fault(f"tools[{index}]: {exc}") from exc
        declarations.append(
            ToolDeclaration(
                name=spec.name, description=spec.description, parameters_json_schema=schema
            )
        )
    return tuple(declarations)


def check_results_answer_calls(messages: list[CanonicalMessage]) -> None:
    """Every function result answers a call of the model turn before it, by id and by name.

    `INVALID_FUNCTION_RESPONSE` otherwise (`FRD-146` FR-3): sent on, a result that answers nothing
    is refused by Anthropic and OpenAI as a malformed conversation, and by Gemini read as a result
    of whichever call has its name — an answer to the wrong question either way.
    """
    open_calls: dict[str, str] = {}
    for message in messages:
        if message.role is Role.MODEL:
            open_calls = {call.id: call.name for call in message.tool_calls}
            continue
        for result in message.tool_results:
            called = open_calls.pop(result.call_id, None)
            if called is None:
                raise KiraError(
                    422,
                    INVALID_FUNCTION_RESPONSE,
                    f"The function result '{result.call_id}' answers no function call of the "
                    "model turn before it.",
                )
            if called != result.name:
                raise KiraError(
                    422,
                    INVALID_FUNCTION_RESPONSE,
                    f"The function result '{result.call_id}' names '{result.name}', but that call "
                    f"was to '{called}'.",
                )


def check_function_parts(request: schemas.ChatRequest, tools: tuple[ToolDeclaration, ...]) -> None:
    """Each call and result as the predecessor checks it, oldest turn first (`FRD-146` FR-3).

    A call names a function by a function's name, one of those offered, and an id no other call
    in the conversation has; a result names the function it reports on. Whether a result answers
    a call is `check_results_answer_calls`, over the mapped conversation.
    """
    offered = {tool.name for tool in tools}
    ids: set[str] = set()
    contents = [*(turn.content for turn in request.conversation_history or []), request.request]
    for content in contents:
        for raw in content.parts:
            kind = raw.get("type")
            if kind == schemas.FUNCTION_CALL:
                name = str(raw["name"])
                if not TOOL_NAME.fullmatch(name):
                    raise _function_fault(f"The function call names '{name[:80]}', not a function.")
                if name not in offered:
                    raise _tool_fault(
                        f"The conversation carries a call to '{name}', which the request's tools "
                        "do not declare. Send the tools with every request of the conversation."
                    )
                call_id = raw.get("id")
                if call_id is not None:
                    if call_id in ids:
                        raise _function_fault(
                            f"Two function calls carry the id '{call_id}'; a result could answer "
                            "either."
                        )
                    ids.add(call_id)
            elif kind == schemas.FUNCTION_RESULT and not raw.get("name"):
                raise _function_fault(
                    f"The function result '{raw['call_id']}' names no function; it names the one "
                    "its call was to."
                )


def _function_fault(message: str) -> KiraError:
    return KiraError(422, INVALID_FUNCTION_RESPONSE, message)


def _carries_function_parts(request: schemas.ChatRequest) -> bool:
    contents = [request.request, *(turn.content for turn in request.conversation_history or [])]
    return any(content.kinds() for content in contents)


def _tool_fault(message: str) -> KiraError:
    return KiraError(422, INVALID_TOOL_CONFIGURATION, message)


def thinking_of(setting: schemas.ThinkingSetting | None) -> Thinking | None:
    """The predecessor's ``{mode, tokens}`` onto the canonical one (`FRD-111` §5.1).

    An unknown mode is refused by `thinking.mode_from` with the contract's own code — the same
    function the Gemini surface uses, so the two cannot disagree about a mode.
    """
    if setting is None:
        return None
    mode = mode_from(setting.mode)
    # A count belongs to `limited` alone. The predecessor ignores one beside any other mode, and so
    # does this surface (`FRD-146` FR-14): the request is served as the mode says.
    return Thinking(mode=mode, tokens=setting.tokens if mode == ThinkingMode.LIMITED else None)


def to_canonical(
    request: schemas.ChatRequest,
    model: str,
    limits: Limits | None = None,
    bounds: SchemaBounds | None = None,
) -> CanonicalRequest:
    """Map a KIRA chat request onto the canonical one.

    History arrives oldest-first and is placed before the current turn — the order every provider
    expects.
    """
    limits = limits or Limits()
    # The declarations first, then the conversation oldest-first, as the predecessor checks them:
    # a request with several faults is refused for the one a client of it would be told about.
    tools = tools_of(request, bounds)
    check_function_parts(request, tools)
    messages: list[CanonicalMessage] = []
    counted = 0

    if request.system_instruction is not None:
        parts = _parts(request.system_instruction, limits, counted, attachments=False)
        counted += len(parts)
        messages.append(CanonicalMessage(role=Role.SYSTEM, parts=parts))

    for turn in request.conversation_history or []:
        parts = _parts(turn.content, limits, counted)
        counted += len(parts)
        role = Role.MODEL if turn.role == "model" else Role.USER
        messages.append(CanonicalMessage(role=role, parts=parts))

    parts = _parts(request.request, limits, counted)
    messages.append(CanonicalMessage(role=Role.USER, parts=parts))
    check_results_answer_calls(messages)

    return CanonicalRequest(
        model=model,
        messages=messages,
        temperature=request.temperature,
        max_output_tokens=request.max_tokens,
        thinking=thinking_of(request.thinking),
        response_schema=(
            parse_schema(request.response_schema, bounds)
            if request.response_schema is not None
            else None
        ),
        tools=tools,
    )


def to_chat_response(response: CanonicalResponse) -> schemas.ChatResponse:
    """The answer in the predecessor's shape.

    - **No text is no part**: an empty answer is ``parts: []``, as the predecessor sends it, rather
      than one part saying nothing (`FRD-146` FR-17). A function call is a part after the text
      (FR-4).
    - **``token_output`` is the answer without the thinking**, as the predecessor counts it
      (`FRD-146` FR-13). The audit row and the budget keep the whole figure — the provider bills the
      thinking at the output rate — so only this field differs.
    """
    return schemas.ChatResponse(
        parts=[
            *([schemas.TextPart(text=response.text)] if response.text else []),
            *(function_call_part(call) for call in response.tool_calls),
        ],
        usage_data=schemas.UsageDataDto(
            token_input=response.usage.prompt_tokens,
            token_output=max(0, response.usage.completion_tokens - response.usage.reasoning_tokens),
        ),
    )


def check_answered(response: CanonicalResponse) -> None:
    """An answer with nothing in it — no text, no call, no output token — is the provider's
    failure, as the predecessor reports it: `502` naming why the model stopped.

    A prompt the provider blocked, or a model that spent its whole cap thinking, comes back as an
    empty answer; served as ``parts: []`` it reads as *the model had nothing to say*, and nobody
    learns that the prompt was refused or the cap too small. An answer that is empty but cost
    output tokens is still served as ``parts: []`` (`FRD-146` FR-17).
    """
    if response.text or response.tool_calls:
        return
    if response.usage.completion_tokens - response.usage.reasoning_tokens > 0:
        return
    raise KiraError(
        502,
        EXTERNAL_KI_API_ERROR,
        f"The model returned no answer (it stopped with '{response.finish_reason}').",
    )


def function_call_part(call: ToolCallPart) -> schemas.FunctionCallPart:
    """A call the model made, as the predecessor returns it; its signature rides in
    ``opaque_metadata`` and comes back with the call (`FRD-146` FR-4)."""
    return schemas.FunctionCallPart(
        id=call.id,
        name=call.name,
        arguments=call.arguments,
        opaque_metadata={SIGNATURE_KEY: call.signature} if call.signature else None,
    )


def function_call_event(call: ToolCallPart) -> dict[str, Any]:
    """The predecessor's stream event for one call, sent before `completed` (`FRD-146` FR-5)."""
    return {"status": "function_call", "data": function_call_part(call).model_dump()}


def stored(payload: dict[str, Any]) -> dict[str, Any]:
    """An answer as it is **stored**: without the calls' ``opaque_metadata`` — model state the
    caller needs to continue, not what was said, and not kept with the answer
    (`ToolCallPart.signature`). The caller still receives it."""
    kept = copy.deepcopy(payload)
    for part in kept.get("parts") or []:
        if isinstance(part, dict):
            part.pop("opaque_metadata", None)
    return kept


def completed_event(response: CanonicalResponse) -> dict[str, Any]:
    """The predecessor's terminal SSE event."""
    return {"status": "completed", "data": to_chat_response(response).model_dump()}


def update_event(message: str) -> dict[str, Any]:
    return {"status": "update", "data": message}


def to_embedding(
    request: schemas.EmbeddingRequest, model: str, *, prompted: bool = False
) -> CanonicalEmbeddingRequest:
    """The predecessor's embedding request onto the canonical one.

    - **A list is one embedding**, as in the predecessor (confirmed from its source, `FRD-113`
      §11). Its texts are joined with nothing between them, which is how the provider combines a
      multi-part content; `TEXT_PART_SEPARATOR` is the *chat* rule and would change every vector.
    - **A model that takes its task in the text** (``prompted``, `embedding_prompts`) is sent each
      text as its own part, inside the task's prompt, as **one** embedding (`FRD-146` FR-7) — the
      predecessor always names a task, `RETRIEVAL_QUERY` when the caller does not. The task stays
      on the request, so the catalogue still decides whether the model takes it.
    - **A blank entry is refused**, because the join would otherwise absorb it without trace — as
      `VALIDATION_ERROR`, the predecessor's code, whether it is empty (`""`, `[]`, `[""]`) or only
      whitespace (`FRD-146` FR-15).
    - Otherwise the default task type is the route's to pass (it applies only where the model
      declares it), and dimensionality is part of a model's identity in the predecessor (one
      catalogue row per width) — so neither is set here.
    """
    entries = [request.text] if isinstance(request.text, str) else list(request.text)

    if not entries or any(not entry.strip() for entry in entries):
        blanks = [index for index, entry in enumerate(entries) if not entry.strip()]
        where = f" at position(s) {', '.join(str(i) for i in blanks)}" if blanks else ""
        message = f"Embedding input must be a non-empty text, or a list of them{where}."
        raise KiraError(422, VALIDATION_ERROR, message)

    if prompted:
        task = request.task_type or DEFAULT_TASK_TYPE
        return CanonicalEmbeddingRequest(
            model=model,
            texts=[embedding_prompts.prompted(task, entry) for entry in entries],
            task_type=task,
            aggregate=True,
            task_in_text=True,
        )
    return CanonicalEmbeddingRequest(
        model=model, texts=["".join(entries)], task_type=request.task_type
    )


def to_rerank(request: schemas.RerankRequest, model: str) -> CanonicalRerankRequest:
    """The predecessor's ranking request onto the canonical one (`FRD-147` FR-2)."""
    return CanonicalRerankRequest(
        model=model,
        query=request.query,
        records=[
            RerankRecord(id=record.id, title=record.title, content=record.content)
            for record in request.records
        ],
    )


def to_rerank_response(records: list[RankedRecord]) -> schemas.RerankResponse:
    """The ranked records in the provider's order, each with its score (`FRD-147` FR-3)."""
    return schemas.RerankResponse(
        records=[
            schemas.RerankedRecord(
                id=record.id, score=record.score, title=record.title, content=record.content
            )
            for record in records
        ]
    )
