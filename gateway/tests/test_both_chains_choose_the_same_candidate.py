"""The chain that returns an answer and the chain that returns a stream pick the same candidate.

**Why a comparison and not two suites.** `LESSONS.md` §7: *when two things are meant to be alike,
the test is the comparison — not two tests, one each.* Two tests, one each, is how
`:streamGenerateContent` came to apply **none** of the dispatch conditions while `:generateContent`
applied all of them: both were tested, each against its own expectations, and nothing asked whether
they agreed. `FRD-302` FR-6 gave the streamed verb the same chain, and this file is what keeps it
the same chain — a condition added to one, an error class caught in one, a skip reclassified in
one, all fail here.

The two cannot be the same function: one awaits a finished response, the other hands back an
iterator whose first chunk has been proven. They do share the walk that decides *who may be
called* (`_candidates`), and that is the half this compares — for the same registry, the same
chain and the same failure, the same choice, the same reasons, the same refusal.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
)
from aira_gateway.pipeline.dispatch import (
    NoCapableModel,
    dispatch_with_fallback,
    stream_with_fallback,
)
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

SOURCE = Path(__file__).resolve().parents[1] / "src" / "aira_gateway" / "pipeline" / "dispatch.py"
_USAGE = CanonicalUsage(prompt_tokens=1, completion_tokens=1)

#: How a candidate behaves, in both forms of the chain. One vocabulary, so a case cannot be set up
#: differently for the two — which is the way a comparison test quietly stops comparing.
ANSWERS = "answers"
FAILS = "fails"
NO_REGION = "no-region"


class _Candidate:
    """One model, failing (or not) the same way on both verbs."""

    def __init__(self, name: str, behaviour: str = ANSWERS) -> None:
        self._name = name
        self._behaviour = behaviour

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(
                self._name, self._name, ("generateContent", "streamGenerateContent"), "acme"
            )
        ]

    def _raise(self) -> None:
        if self._behaviour == FAILS:
            raise UpstreamError(f"{self._name} is unavailable", 503)
        if self._behaviour == NO_REGION:
            raise RegionNotAllowed(f"{self._name} is served from no permitted region")

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self._raise()
        return CanonicalResponse(model=self._name, text=f"by:{self._name}", usage=_USAGE)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        self._raise()
        yield CanonicalChunk(text_delta=f"by:{self._name}", usage=_USAGE, finish_reason="stop")

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


def _request(model: str) -> CanonicalRequest:
    return CanonicalRequest(model=model, messages=[CanonicalMessage(role=Role.USER, text="hello")])


def _refuses(*withheld: str) -> Any:
    """A condition that excludes the named models, in the words `permits` answers with."""

    async def permits(model: str) -> str | None:
        return "not released to this use case" if model in withheld else None

    return permits


#: Every shape a chain can be in, as `(candidates, chain, withheld)`.
#:
#: Each one is a case that behaved differently on the two verbs at some point in this project's
#: history, or that would if somebody edited one chain and not the other.
CASES: list[tuple[str, list[tuple[str, str]], tuple[str, ...], tuple[str, ...]]] = [
    ("the primary answers", [("a", ANSWERS), ("b", ANSWERS)], ("b",), ()),
    ("the primary is down", [("a", FAILS), ("b", ANSWERS)], ("b",), ()),
    ("both are down", [("a", FAILS), ("b", FAILS)], ("b",), ()),
    ("the primary is withheld", [("a", ANSWERS), ("b", ANSWERS)], ("b",), ("a",)),
    ("everything is withheld", [("a", ANSWERS), ("b", ANSWERS)], ("b",), ("a", "b")),
    ("the primary has no region", [("a", NO_REGION), ("b", ANSWERS)], ("b",), ()),
    ("no region anywhere", [("a", NO_REGION), ("b", NO_REGION)], ("b",), ()),
    ("there is no chain at all", [("a", FAILS)], (), ()),
    ("the chain names the primary again", [("a", FAILS), ("b", ANSWERS)], ("a", "b"), ()),
    ("the chain names a model nothing serves", [("a", FAILS), ("b", ANSWERS)], ("ghost", "b"), ()),
    ("the third one answers", [("a", FAILS), ("b", FAILS), ("c", ANSWERS)], ("b", "c"), ()),
    (
        "a withheld model sits between two outages",
        [("a", FAILS), ("b", ANSWERS), ("c", ANSWERS)],
        ("b", "c"),
        ("b",),
    ),
]


async def _outcome(
    chain_call: Any,
    candidates: list[tuple[str, str]],
    chain: tuple[str, ...],
    withheld: tuple[str, ...],
) -> dict[str, Any]:
    """What one form of the chain made of a case: the choice, the reasons, or the refusal.

    Reduced to plain data on purpose. Comparing a `Dispatched` with a `Streaming` would compare
    types, and what has to be equal is the **decision**.
    """
    registry = ProviderRegistry([_Candidate(name, how) for name, how in candidates])
    try:
        result = await chain_call(
            registry, _request(candidates[0][0]), chain, permits=_refuses(*withheld)
        )
    except (UpstreamError, NoCapableModel) as refusal:
        return {"refusal": type(refusal).__name__, "reason": str(refusal)}
    return {
        "model": result.model if hasattr(result, "model") else result.response.model,
        "index": result.candidate_index,
        "skipped": [(entry.model, entry.reason) for entry in result.skipped],
        "failed": [(entry.model, entry.reason) for entry in result.failed],
    }


@pytest.mark.parametrize(
    ("name", "candidates", "chain", "withheld"),
    [pytest.param(*case, id=case[0].replace(" ", "-")) for case in CASES],
)
async def test_both_chains_decide_the_same_thing(
    name: str,
    candidates: list[tuple[str, str]],
    chain: tuple[str, ...],
    withheld: tuple[str, ...],
) -> None:
    answered = await _outcome(dispatch_with_fallback, candidates, chain, withheld)
    streamed = await _outcome(stream_with_fallback, candidates, chain, withheld)

    assert answered == streamed, (
        f"the two forms of the chain disagree about {name!r}:\n"
        f"  :generateContent       {answered}\n"
        f"  :streamGenerateContent {streamed}\n"
        "A caller's governance and availability must not depend on which verb they used — that "
        "divergence is what `FRD-302` FR-6 was written to end."
    )


def test_the_cases_cover_the_answers_and_the_refusals() -> None:
    """A comparison of two empty answers is a pass. This asserts the matrix above actually
    produces both kinds of outcome, so the equality it checks is about something."""
    assert {how for _, candidates, _, _ in CASES for _, how in candidates} == {
        ANSWERS,
        FAILS,
        NO_REGION,
    }
    assert any(withheld for *_, withheld in CASES)


def test_neither_chain_builds_its_own_candidate_list() -> None:
    """The structural half, and the one that survives a behaviour nobody thought to parametrise.

    The agreement above holds because both walk `_candidates`. A second list — written inline,
    looking reasonable — is how the two drifted the first time: the conditions were asked in the
    chain, and the stream never entered it.
    """
    source = SOURCE.read_text()
    walks = source.count("async for index, model, provider, addressed in _candidates(")

    assert walks == 2, (
        f"{walks} of the two chains walk the shared candidate list. One that builds its own is "
        "one that will be given a condition the other has, or lose one the other keeps."
    )
    # And the walk is the only place the conditions are asked, so neither can be given its own.
    assert source.count("await permits(model)") == 1
