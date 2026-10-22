"""Google's ranking request and answer, as its REST reference writes them (`FRD-147` FR-5).

Strict like the Gemini surface (`FRD-124`): this is Google's contract, and a field it defines that
is not modelled here would be dropped by a client that believes it was honoured.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


class RankingRecord(BaseModel):
    model_config = _STRICT
    id: Annotated[str, Field(min_length=1)]
    title: str = ""
    content: str = ""


class RankRequest(BaseModel):
    model_config = _STRICT
    #: Required here, though Google defaults it: which reranker answers is this installation's
    #: decision, made in the catalogue, not the provider's latest.
    model: Annotated[str, Field(min_length=1)]
    query: str
    records: list[RankingRecord]
    topN: Annotated[int, Field(gt=0)] | None = None
    ignoreRecordDetailsInResponse: bool = False


class RankedRecordOut(BaseModel):
    id: str
    score: float
    title: str | None = None
    content: str | None = None


class RankResponse(BaseModel):
    records: list[RankedRecordOut]
