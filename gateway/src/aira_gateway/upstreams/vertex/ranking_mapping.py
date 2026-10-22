"""Google's ranking API, `rankingConfigs/…:rank` (`FRD-147` §5.2).

Not a Vertex method: the Discovery Engine serves it, at a location rather than a region, billed per
query of up to a hundred records. The body names the model; the URL names only the location.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.core.canonical import CanonicalRerankRequest, RankedRecord
from aira_gateway.upstreams.base import UpstreamError


def rank_body(request: CanonicalRerankRequest, model: str) -> dict[str, Any]:
    """``request`` as Google takes it. A record's empty title or content is left out, not sent
    empty: Google ranks a record by whichever of the two it has."""
    body: dict[str, Any] = {
        "model": model,
        "query": request.query,
        "records": [
            {
                "id": record.id,
                **({"title": record.title} if record.title else {}),
                **({"content": record.content} if record.content else {}),
            }
            for record in request.records
        ],
    }
    if request.top_n is not None:
        body["topN"] = request.top_n
    if request.ids_only:
        body["ignoreRecordDetailsInResponse"] = True
    return body


def ranked(data: dict[str, Any], request: CanonicalRerankRequest) -> list[RankedRecord]:
    """The records Google returned, in its order, each with its score.

    An id Google returns that was not sent is the provider's fault, not an answer: a caller would
    look up a record it never had.
    """
    sent = {record.id for record in request.records}
    records = data.get("records")
    if not isinstance(records, list):
        raise UpstreamError("Google answered the ranking request without records.", 502)
    answer: list[RankedRecord] = []
    for entry in records:
        if not isinstance(entry, dict) or str(entry.get("id")) not in sent:
            raise UpstreamError("Google ranked a record that was not sent.", 502)
        score = entry.get("score", 0.0)
        answer.append(
            RankedRecord(
                id=str(entry["id"]),
                score=float(score) if isinstance(score, int | float) else 0.0,
                title=entry.get("title") if isinstance(entry.get("title"), str) else None,
                content=entry.get("content") if isinstance(entry.get("content"), str) else None,
            )
        )
    return answer
