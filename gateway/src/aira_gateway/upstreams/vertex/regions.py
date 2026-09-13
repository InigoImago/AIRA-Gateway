"""Where a Vertex model runs, and regional failover across those places (`FRD-609`).

Dialect-free, so both Vertex adapters walk one loop. Residency is **not** checked here:
`VertexTransport.url` checks every region as it is addressed, and the loop learns that a region is
not permitted by being told (`RegionNotAllowed`), so the rule keeps exactly one owner.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aira_gateway.core.canonical import CanonicalChunk
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.upstreams.base import AmbiguousModel, UpstreamError
from aira_gateway.upstreams.vertex.transport import VertexTransport

#: Upstream statuses that mean **try the next region**, and nothing else. A `404` (not deployed
#: here), `429` (no quota here) or `5xx` (unwell here) is a fact about a *place*; a `400` or
#: `401`/`403` is a fact about the *request*, identical in every region. A content refusal arrives
#: as a `200` and is the model's answer — asking another region would be shopping for a verdict.
REGION_FAILOVER_STATUSES = frozenset({404, 408, 429, 500, 502, 503, 504})

#: One dialect's stream reader: a parsed SSE event in, a canonical chunk (or nothing) out.
ChunkReader = Callable[[dict[str, Any]], CanonicalChunk | None]


@dataclass(frozen=True, slots=True)
class VertexModel:
    """A model this deployment reaches: where it runs, whose API it speaks, what it is called."""

    region: str
    publisher: str
    name: str

    @classmethod
    def parse(cls, spec: str) -> VertexModel:
        """``region/publisher/model`` — the three things the URL and the dialect choice need."""
        parts = spec.split("/", 2)
        if len(parts) != 3 or not all(part.strip() for part in parts):
            raise ValueError(
                f"'{spec}' is not a Vertex model spec. Expected 'region/publisher/model', "
                "e.g. 'eu/anthropic/claude-sonnet-4-5@20250929'."
            )
        return cls(parts[0].strip(), parts[1].strip(), parts[2].strip())


def _targets(
    models: dict[str, VertexModel],
    publisher: str,
    model: str,
    addressing: dict[str, Any],
) -> tuple[tuple[str, str], ...]:
    """Every `(region, publisher)` this model may be tried at, **in order**.

    A model named in `AIRA_VERTEX_MODELS` has exactly one. Otherwise the catalogue's addressing
    lists them, the first preferred — which is what makes cataloguing a Vertex model enough to
    serve it. A model with neither is refused by name: a guess about residency is the one guess
    this product may not make.
    """
    configured = models.get(model)
    if configured is not None:
        return ((configured.region, configured.publisher),)
    regions = _declared_regions(addressing)
    if not regions:
        raise AmbiguousModel(
            f"'{model}' is catalogued for this platform and says no region. Vertex addresses a "
            "model by region, so there is nothing to send it to — set the region on the model in "
            "the catalogue, or name it in AIRA_VERTEX_MODELS."
        )
    return tuple((region, publisher) for region in regions)


def _declared_regions(addressing: dict[str, Any]) -> tuple[str, ...]:
    """The catalogue's regions, either spelling, in order, without duplicates.

    The same normalisation as `ModelDeclaration.regions`, duplicated rather than imported so the
    adapter layer does not depend on the read-model (`ADR-0011`);
    `test_the_two_readers_of_a_region_list_agree` holds the two together.
    """
    block = addressing or {}
    raw = block.get("regions")
    if raw is None:
        single = block.get("region")
        raw = [single] if isinstance(single, str) else []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    seen: dict[str, None] = {}
    for region in raw:
        if isinstance(region, str) and region.strip():
            seen.setdefault(region.strip(), None)
    return tuple(seen)


async def _across_regions[T](
    targets: tuple[tuple[str, str], ...],
    attempt: Callable[[str, str], Awaitable[T]],
) -> T:
    """Try each region in order, moving on only for a failure that is about the **place**.

    `RegionNotAllowed` (residency does not permit it) and a status in `REGION_FAILOVER_STATUSES`
    move on; anything else — a malformed request, a bad credential, an answer — is identical in
    every region and propagates. When every region fails, the **last** failure is raised, so the
    caller learns where the chain ended rather than that nothing was tried.
    """
    last: Exception | None = None
    for region, publisher in targets:
        try:
            return await attempt(region, publisher)
        except RegionNotAllowed as exc:
            last = exc
        except UpstreamError as exc:
            if exc.status_code not in REGION_FAILOVER_STATUSES:
                raise
            last = exc
    if last is not None:
        raise last
    # Unreachable through `_targets`, which refuses an empty list by name.
    raise AmbiguousModel("No region was available to address this model.")


async def _open_stream(
    transport: VertexTransport,
    url: str,
    body: dict[str, Any],
    reader: Callable[[], ChunkReader],
) -> AsyncIterator[CanonicalChunk]:
    """Open a streamed call and return an iterator over its chunks.

    Split from the iteration on purpose — the split **is** the failover boundary. Everything that
    can go wrong about a region goes wrong while opening, before any byte reaches the caller; once
    this returns, the stream is committed. ``reader`` is called once per attempt, so a stateful
    reader never carries half a tool call into another region's response. The stream is closed by
    the iterator's ``finally``.
    """
    context = transport.stream(url, body)
    response = await context.__aenter__()
    read = reader()

    async def chunks() -> AsyncIterator[CanonicalChunk]:
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = read(json.loads(line[len("data: ") :]))
                if chunk is not None:
                    yield chunk
        finally:
            await context.__aexit__(None, None, None)

    return chunks()
