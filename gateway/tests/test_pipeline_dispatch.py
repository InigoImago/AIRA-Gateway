import pytest

from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
)
from aira_gateway.pipeline.dispatch import NoCapableModel, dispatch_with_fallback
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel


class _Provider:
    #: A test double, like `MockProvider` (`FRD-307`): it serves invented models, so the
    #: catalogue-and-approve requirement does not apply to it.
    is_test_double = True

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self._name = name
        self._fail = fail

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(self._name, self._name, ("generateContent",))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
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
