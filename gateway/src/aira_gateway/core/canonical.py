"""Provider-agnostic canonical request/response schema (FRD-100, FRD-110).

Every API surface (Gemini now, KIRA per `ADR-0010`) maps to/from these models, and upstream
providers speak only canonical. This is the single point the whole gateway agrees on.

A message is an **ordered list of parts**: text, and inline binary data with a declared media type.
Order is preserved end to end, because "this image, then this question" and "this question, then
this image" are different prompts.

``text=`` still constructs a message and ``.text`` still reads one — the great majority of the code
legitimately wants "what does this message say", and keeping that working is what made `FRD-110` a
change to one file rather than to twenty. It is also the one thing to be careful about: ``.text``
used to be *total* and is now **lossy**. Anything that decides or persists on it must be reviewed
rather than merely compiled, which is what `FRD-110` FR-9 (the pipeline's blind spot) is about.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, computed_field, model_validator


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    MODEL = "model"


class TextPart(BaseModel):
    text: str


class DataPart(BaseModel):
    """Inline binary content — a document or an image.

    ``data`` is decoded bytes: base64 is a wire concern and does not belong in the canonical
    model, where it would invite two representations of the same thing.
    """

    media_type: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)


CanonicalPart = TextPart | DataPart


class CanonicalMessage(BaseModel):
    role: Role
    parts: list[CanonicalPart] = []

    @model_validator(mode="before")
    @classmethod
    def _accept_plain_text(cls, data: Any) -> Any:
        """Allow ``CanonicalMessage(role=..., text="…")``.

        Not nostalgia: a text-only message is still the overwhelmingly common case, and a
        constructor that forced every caller to wrap one string in a list would add ceremony to
        the path that matters most while changing nothing about it.
        """
        if isinstance(data, dict) and "text" in data and "parts" not in data:
            data = {**data, "parts": [{"text": data.pop("text")}]}
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def text(self) -> str:
        """What this message *says* — the text parts, concatenated.

        Deliberately excludes attachments, and that exclusion is the whole point of `FRD-110`
        FR-9: the injection filter and the routing classifier read this, so they see the prompt
        and **not** the document. A prompt injection inside a PDF is invisible to them, which is
        stated in the pipeline builder rather than left to be discovered.
        """
        return "".join(part.text for part in self.parts if isinstance(part, TextPart))

    @property
    def attachments(self) -> list[DataPart]:
        return [part for part in self.parts if isinstance(part, DataPart)]


class CanonicalRequest(BaseModel):
    model: str
    messages: list[CanonicalMessage]
    temperature: float | None = None
    max_output_tokens: int | None = None

    def last_user_text(self) -> str:
        for message in reversed(self.messages):
            if message.role is Role.USER:
                return message.text
        return self.messages[-1].text if self.messages else ""

    @property
    def attachments(self) -> list[DataPart]:
        """Every attachment in the request, in order."""
        return [part for message in self.messages for part in message.attachments]

    @property
    def media_types(self) -> frozenset[str]:
        """The distinct media types this request carries. What a model must be able to read."""
        return frozenset(part.media_type for part in self.attachments)


class CanonicalUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class CanonicalResponse(BaseModel):
    model: str
    text: str
    finish_reason: str = "stop"
    usage: CanonicalUsage


class CanonicalChunk(BaseModel):
    """A streaming delta. The final chunk carries ``finish_reason`` and ``usage``."""

    text_delta: str
    finish_reason: str | None = None
    usage: CanonicalUsage | None = None
