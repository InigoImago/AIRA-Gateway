"""Provider-agnostic canonical request/response schema (FRD-100).

Every API surface (Gemini now, OpenAI later per ADR-0005) maps to/from these models, and
upstream providers speak only canonical. This is the single point the whole gateway agrees on.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, computed_field


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    MODEL = "model"


class CanonicalMessage(BaseModel):
    role: Role
    text: str


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
