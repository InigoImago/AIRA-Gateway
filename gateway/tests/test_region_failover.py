"""A model in several regions, tried in order (`FRD-609`).

The requirement, from the owner: *"in the catalogue I would like to be able to enter several
regions, and they should also be checkable — whether the model is reachable, and the same for the
thinking methods."*

What makes this more than a list is **which failures move to the next region**, and that is the
whole of what these tests pin. Three kinds of answer:

- a fact about the **place** — not deployed here, no quota here, unwell here — where somewhere else
  may answer;
- a fact about the **request** — malformed, bad credential — identical in every region, so retrying
  spends the caller's time arriving at the same refusal;
- a fact about the **content** — the model answered and refused — where asking a second region is
  shopping for a verdict.

And one boundary that cannot be crossed: a stream that has sent a byte is committed.
"""

from __future__ import annotations

from typing import Any

import pytest

from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.vertex.adapters import (
    REGION_FAILOVER_STATUSES,
    VertexGeminiAdapter,
    _across_regions,
)

pytestmark = pytest.mark.anyio

TARGETS = (("europe-west1", "google"), ("europe-west4", "google"))


def _request(regions: list[str]) -> CanonicalRequest:
    return CanonicalRequest(
        model="gemini-2.5-pro",
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        addressing={"regions": regions},
    )


# == which failures move on =======================================================================


async def test_the_first_region_that_answers_wins_and_the_rest_are_not_asked() -> None:
    asked: list[str] = []

    async def attempt(region: str, publisher: str) -> str:
        asked.append(region)
        return f"answered in {region}"

    assert await _across_regions(TARGETS, attempt) == "answered in europe-west1"
    assert asked == ["europe-west1"]


@pytest.mark.parametrize("status", sorted(REGION_FAILOVER_STATUSES))
async def test_a_failure_about_the_place_moves_to_the_next_region(status: int) -> None:
    """404 not deployed here · 408/504 slow here · 429 no quota here · 5xx unwell here."""
    asked: list[str] = []

    async def attempt(region: str, publisher: str) -> str:
        asked.append(region)
        if region == "europe-west1":
            raise UpstreamError(f"Vertex upstream returned {status}.", status)
        return "answered"

    assert await _across_regions(TARGETS, attempt) == "answered"
    assert asked == ["europe-west1", "europe-west4"]


@pytest.mark.parametrize("status", [400, 401, 403, 422])
async def test_a_failure_about_the_request_is_not_retried_anywhere(status: int) -> None:
    """**The half that matters for the caller's time.** A malformed body and a bad credential are
    identical in every region; walking a chain of three would triple the wait before the same
    refusal — and on a `403`, would triple the failed-auth count somebody is alerting on."""
    asked: list[str] = []

    async def attempt(region: str, publisher: str) -> str:
        asked.append(region)
        raise UpstreamError(f"Vertex upstream returned {status}.", status)

    with pytest.raises(UpstreamError):
        await _across_regions(TARGETS, attempt)
    assert asked == ["europe-west1"]


async def test_a_region_the_policy_forbids_is_stepped_over_rather_than_failing() -> None:
    """**Residency keeps exactly one owner.** The loop holds no copy of the allow-list: it learns
    that a region is not permitted by addressing it and being told, which is why a model catalogued
    in `europe-west1, global` works unchanged on an installation that permits only the first — and
    why widening or narrowing `AIRA_ALLOWED_REGIONS` needs no second edit anywhere."""
    asked: list[str] = []

    async def attempt(region: str, publisher: str) -> str:
        asked.append(region)
        if region == "europe-west1":
            raise RegionNotAllowed("Region 'europe-west1' is not in the allowed set.")
        return "answered"

    assert await _across_regions(TARGETS, attempt) == "answered"
    assert asked == ["europe-west1", "europe-west4"]


async def test_every_region_forbidden_raises_the_residency_refusal() -> None:
    """Not a generic failure. A caller — and the operator reading the audit row — needs to see that
    the model is catalogued somewhere this installation may not process."""

    async def attempt(region: str, publisher: str) -> str:
        raise RegionNotAllowed(f"Region '{region}' is not in the allowed set.")

    with pytest.raises(RegionNotAllowed) as caught:
        await _across_regions(TARGETS, attempt)
    # The **last** one, so the message names where the chain ended rather than where it began.
    assert "europe-west4" in str(caught.value)


async def test_the_last_failure_is_raised_not_the_first() -> None:
    """A caller reading *"429 in europe-west4"* learns the chain was walked and where it ended;
    the first failure would suggest nothing after it was tried."""

    async def attempt(region: str, publisher: str) -> str:
        raise UpstreamError(f"no quota in {region}", 429)

    with pytest.raises(UpstreamError) as caught:
        await _across_regions(TARGETS, attempt)
    assert "europe-west4" in str(caught.value)


# == what the audit row records ===================================================================


async def test_the_answer_carries_the_region_that_produced_it() -> None:
    """`FRD-115` FR-10: *"the configuration says EU"* is a claim and *"this request went to `eu`"*
    is evidence. With a chain those stopped being the same sentence, and the audit row took the
    first — a confident wrong claim on every request that used a fallback."""

    class _SecondRegionAnswers:
        def __init__(self) -> None:
            self.posted: list[str] = []

        def url(self, *, region: str, publisher: str, model: str, method: str) -> str:
            return f"https://{region}/{model}:{method}"

        async def post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
            self.posted.append(url)
            if url.startswith("https://europe-west1"):
                raise UpstreamError("Vertex upstream returned 429.", 429)
            return {
                "candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]}}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            }

    transport = _SecondRegionAnswers()
    adapter = VertexGeminiAdapter(transport, [])  # type: ignore[arg-type]

    answer = await adapter.generate(_request(["europe-west1", "europe-west4"]))

    assert answer.served_region == "europe-west4"
    assert [url.split("/")[2] for url in transport.posted] == ["europe-west1", "europe-west4"]


async def test_one_region_still_reports_where_it_was_served() -> None:
    """The ordinary case has to carry it too, or the column would be populated only for the
    requests that failed over — which reads as *"everything else went to the default"*."""

    class _Answers:
        def url(self, *, region: str, publisher: str, model: str, method: str) -> str:
            return f"https://{region}/{model}:{method}"

        async def post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
            return {
                "candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]}}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            }

    adapter = VertexGeminiAdapter(_Answers(), [])  # type: ignore[arg-type]

    answer = await adapter.generate(_request(["europe-west3"]))

    assert answer.served_region == "europe-west3"


# == the two readers of a region list ============================================================


@pytest.mark.parametrize(
    ("addressing", "expected"),
    [
        ({"regions": ["europe-west1", "europe-west4"]}, ("europe-west1", "europe-west4")),
        ({"region": "europe-west3"}, ("europe-west3",)),
        # The list wins where a row somehow carries both: it is the current shape, and silently
        # preferring the older one would make a model with three regions behave as though it had
        # one.
        ({"region": "eu", "regions": ["europe-west1"]}, ("europe-west1",)),
        ({"regions": ["  eu  ", "", "eu", "   ", "europe-west1"]}, ("eu", "europe-west1")),
        ({"regions": "europe-west1"}, ("europe-west1",)),
        ({}, ()),
        ({"regions": [1, None, {}]}, ()),
        ({"region": 42}, ()),
    ],
)
def test_the_two_readers_of_a_region_list_agree(addressing: dict, expected: tuple) -> None:
    """**One format, read in two places, and the same cases prove both.**

    `ModelDeclaration.regions` is the read-model's reader and `_declared_regions` is the upstream
    layer's; they are deliberately not one function, because an adapter importing a shape from the
    catalogue is the dependency `ADR-0011` keeps out. What is shared is the *format*, and this is
    what stops the two drifting — a divergence would mean a model addressed in one region and
    recorded as being in another.

    The console has a third (`readRegions` in `models.ts`), tested against the same cases in
    `read-regions.spec.ts`.
    """
    from aira_gateway.catalog import ModelDeclaration
    from aira_gateway.upstreams.vertex.adapters import _declared_regions

    assert _declared_regions(addressing) == expected
    assert ModelDeclaration(name="m", addressing=addressing).regions == expected


# == what the audit row records, through the function that decides it =============================


class _Request:
    """Enough of a Starlette request for `provenance`: an app with state, and a state of its own."""

    def __init__(self, registry: Any, catalog: Any) -> None:
        self.app = type("_App", (), {"state": type("_S", (), {"providers": registry})()})()
        self.state = type("_S", (), {"catalog": catalog})()


class _ConfiguredInWest1:
    """A registry that knows this model as configured in `europe-west1` — and only that."""

    def get_model(self, model: str) -> Any:
        from aira_gateway.upstreams.base import UpstreamModel

        return UpstreamModel(model, model, ("generateContent",), "vertex", "google", "europe-west1")

    def provenance_for(self, provider: str) -> tuple[str, str, str] | None:
        return ("vertex", "google", "europe-west1")


async def test_the_audit_row_records_the_region_that_answered() -> None:
    """**The residency claim has to be evidence, not configuration** (`FRD-115` FR-10).

    Everything `provenance` knew before this came from *configuration*: which region a model is set
    up in. That was the same sentence as "where this request went" for exactly as long as a model
    had one region — and a failover chain makes it false in the worst possible direction, because
    the row would keep naming the **first** region confidently while the answer came from the
    second.

    A wrong residency claim is worse than a blank one: a blank column is neither a claim nor
    evidence, and a wrong one reads as evidence.
    """
    from aira_gateway.api.serving import provenance

    request = _Request(_ConfiguredInWest1(), None)

    # No adapter reported one — every dialect with a single place. The configured answer stands.
    assert await provenance(request, "gemini-2.5-pro") == ("vertex", "google", "europe-west1")

    # The chain fell through to the second region, and that is what the row must say.
    assert await provenance(request, "gemini-2.5-pro", "europe-west4") == (
        "vertex",
        "google",
        "europe-west4",
    )


async def test_a_catalogued_model_records_the_region_that_answered_too() -> None:
    """The other branch: a model resolved through the **catalogue** has no registry entry, so the
    provenance comes from the adapter that owns its provider — a static answer, and one that must
    still yield to what actually happened."""
    from aira_gateway.api.serving import provenance

    class _Unknown:
        def get_model(self, model: str) -> Any:
            return None

        def provenance_for(self, provider: str) -> tuple[str, str, str] | None:
            return ("vertex", "google", "europe-west1")

    class _Catalog:
        def per_request(self) -> Any:
            return self

        async def declaration(self, model: str) -> Any:
            from aira_gateway.catalog import ModelDeclaration

            return ModelDeclaration(name=model, provider="vertex")

    request = _Request(_Unknown(), _Catalog())

    assert await provenance(request, "gemini-2.5-pro", "eu") == ("vertex", "google", "eu")
