"""Google's ranking request and answer, as its REST reference writes them (`FRD-147` FR-5).

Strict like the Gemini surface (`FRD-124`): this is Google's contract, and a field it defines that
is not modelled here would be dropped by a client that believes it was honoured.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_snake

#: As the Gemini surface reads Google's JSON (`FRD-155`): each field by its camelCase and its proto
#: name, and a refusal naming the camelCase one.
_STRICT = ConfigDict(
    extra="forbid", alias_generator=to_snake, populate_by_name=True, loc_by_alias=False
)


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
    #: The ranking configuration's resource name, which the Go client repeats from the path in the
    #: body. It selects nothing, as the path's does not (`routes`).
    rankingConfig: str | None = None
    #: Labels for the caller's own Google bill, which the Node client sends empty on every request.
    #: Empty is accepted; labels are refused by name (`_no_labels`).
    userLabels: dict[str, str] | None = None

    @field_validator("topN", mode="before")
    @classmethod
    def _no_limit_is_none(cls, value: object) -> object:
        """``0`` is proto3's default — every record — which is what an absent ``topN`` asks."""
        return None if value == 0 and not isinstance(value, bool) else value

    @model_validator(mode="after")
    def _no_labels(self) -> RankRequest:
        if self.userLabels:
            raise ValueError(
                "'userLabels' is not served: labels sort a Google bill, and this gateway ranks in "
                "its own Google project and bills the use case. Leave them out; the use case is "
                "the label here."
            )
        return self


class RankedRecordOut(BaseModel):
    id: str
    score: float
    title: str | None = None
    content: str | None = None


class RankResponse(BaseModel):
    records: list[RankedRecordOut]
