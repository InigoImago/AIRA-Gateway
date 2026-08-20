import pytest

from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
)
from aira_gateway.pipeline.dispatch import NoCapableModel, Routing, dispatch_with_fallback
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel


class _Provider:
    #: A test double, like `MockProvider` (`FRD-307`): it serves invented models, so the
    #: catalogue-and-approve requirement does not apply to it.
    is_test_double = True

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self._name = name
        self._fail = fail
        #: The `addressing` each call arrived with — see the addressing test at the end.
        self.addressed_as: list[dict[str, object]] = []

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(self._name, self._name, ("generateContent",))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.addressed_as.append(dict(request.addressing))
        if self._fail:
            raise UpstreamError(f"{self._name} down", status_code=503)
        return CanonicalResponse(
            model=request.model,
            text=f"[{request.model}]",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


def _request(model: str) -> CanonicalRequest:
    return CanonicalRequest(model=model, messages=[CanonicalMessage(role=Role.USER, text="hi")])


async def test_primary_success() -> None:
    registry = ProviderRegistry([_Provider("a"), _Provider("b")])
    response = (await dispatch_with_fallback(registry, _request("a"), ("b",))).response
    assert response.model == "a"


async def test_falls_back_when_primary_fails() -> None:
    registry = ProviderRegistry([_Provider("a", fail=True), _Provider("b")])
    response = (await dispatch_with_fallback(registry, _request("a"), ("b",))).response
    assert response.model == "b"


async def test_skips_unknown_fallback_model() -> None:
    registry = ProviderRegistry([_Provider("a", fail=True), _Provider("c")])
    response = (await dispatch_with_fallback(registry, _request("a"), ("ghost", "c"))).response
    assert response.model == "c"


async def test_all_fail_raises_last_error() -> None:
    registry = ProviderRegistry([_Provider("a", fail=True), _Provider("b", fail=True)])
    with pytest.raises(UpstreamError, match="b down"):
        await dispatch_with_fallback(registry, _request("a"), ("b",))


async def test_a_chain_with_nothing_to_offer_is_not_reported_as_an_outage() -> None:
    """This used to raise ``UpstreamError``, which the route mapped to a 502 — so a configuration
    mistake read as "the provider is down" and sent whoever looked at it to the wrong place.

    "Every candidate was excluded" is something an operator can fix; an outage is not.
    """
    registry = ProviderRegistry([_Provider("a")])

    with pytest.raises(NoCapableModel) as caught:
        await dispatch_with_fallback(registry, _request("ghost"), ())

    assert not isinstance(caught.value, UpstreamError)
    assert "ghost" in str(caught.value), "the failure has to name what it could not use"


async def test_a_tried_upstream_that_failed_is_still_an_outage() -> None:
    """The counterpart: a provider that was actually called and failed is an outage, and the
    caller may usefully retry. Collapsing both into one error would lose that."""
    registry = ProviderRegistry([_Provider("a", fail=True)])

    with pytest.raises(UpstreamError):
        await dispatch_with_fallback(registry, _request("a"), ())


# == the address travels with the model ==========================================================

#: What the catalogue says about reaching each of these, as `declared_routing` would answer.
_CATALOGUE = {
    "a": Routing(addressing={"regions": ["europe-west1"]}),
    "b": Routing(addressing={"regions": ["europe-west4"]}),
}


async def _catalogued(model: str) -> Routing:
    return _CATALOGUE.get(model, Routing())


async def test_a_fallback_is_addressed_where_the_catalogue_puts_it() -> None:
    """**The chain re-pointed the model name and left the platform address behind.**

    `model_copy(update={"model": model})` changed one of the two things that say where a request
    goes. `addressing` is filled once, before the chain, from the *routed* model's declaration —
    so every hop after the first carried the primary's address.

    Invisible in every dialect where a model name is the whole address, and on Vertex it is the
    region list: a fallback catalogued in `europe-west4` was addressed at `europe-west1`, answered
    *not deployed here*, and the failover loop then walked the primary's remaining regions, all
    equally wrong.
    """
    spare = _Provider("b")
    registry = ProviderRegistry([_Provider("a", fail=True), spare])
    request = CanonicalRequest(
        model="a",
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        addressing={"regions": ["europe-west1"]},
    )

    await dispatch_with_fallback(registry, request, ("b",), routing_of=_catalogued)

    assert spare.addressed_as == [{"regions": ["europe-west4"]}]


async def test_a_catalogued_fallback_is_addressable_behind_a_primary_that_has_no_address() -> None:
    """The same defect in the direction that refuses rather than misroutes.

    A primary whose name is its whole address carries `addressing={}`, and the fallback inherited
    the emptiness — so a Vertex model the catalogue names a region for was refused by name with
    *"catalogued for this platform and says no region"*, which the catalogue contradicts.
    """
    spare = _Provider("b")
    registry = ProviderRegistry([_Provider("a", fail=True), spare])

    await dispatch_with_fallback(registry, _request("a"), ("b",), routing_of=_catalogued)

    assert spare.addressed_as == [{"regions": ["europe-west4"]}]


async def test_a_chain_told_nothing_about_routing_keeps_the_address_it_was_given() -> None:
    """The narrowing this fix must not do. With no `routing_of` there is nothing to look an address
    up in, and the request's own is the only answer — a caller that resolved it itself keeps it."""
    spare = _Provider("b")
    registry = ProviderRegistry([_Provider("a", fail=True), spare])
    request = CanonicalRequest(
        model="a",
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        addressing={"regions": ["europe-west1"]},
    )

    await dispatch_with_fallback(registry, request, ("b",))

    assert spare.addressed_as == [{"regions": ["europe-west1"]}]
