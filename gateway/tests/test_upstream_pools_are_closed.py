"""What `create_app` opens, the lifespan closes (2026-08-15).

Found by listing the two against each other rather than by anything failing. `create_app` builds one
`httpx.AsyncClient` per configured provider — Vertex, Google AI Studio, Foundry, and one per
OpenAI-dialect server — and the lifespan closed the database engine, the counter store, the audit
writer and the readiness probe. Every connection pool stayed open, with its own TLS context, until
the process died: a redeploy leaks sockets, and the hermetic suite, which builds an application per
test, accumulates them by the hundred.

Nothing failed, which is the point — a leak is a defect whose only symptom is somebody else's.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel


class _Adapter:
    """An adapter that owns a pool and says so, like every real one."""

    is_test_double = True

    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = 0

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(self.name, self.name, ("generateContent",), provider=self.name)]

    async def aclose(self) -> None:
        self.closed += 1


class _Poolless:
    """One that owns nothing — the mock, and any adapter written before this existed."""

    is_test_double = True

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("none-1", "none-1", ("generateContent",), provider="none")]


class _Stubborn(_Adapter):
    async def aclose(self) -> None:
        self.closed += 1
        raise RuntimeError("the socket had already gone")


async def test_every_adapter_that_owns_a_pool_is_closed() -> None:
    adapters = [_Adapter("a"), _Adapter("b")]

    await ProviderRegistry(list(adapters)).aclose()

    assert [adapter.closed for adapter in adapters] == [1, 1]


async def test_an_adapter_with_no_pool_is_simply_skipped() -> None:
    """`aclose` is optional, so an adapter that holds nothing needs no ceremony to say so."""
    await ProviderRegistry([_Poolless(), _Adapter("a")]).aclose()


async def test_one_adapter_failing_does_not_leave_the_others_open() -> None:
    """This runs inside shutdown, where the useful outcome is that the **rest** of the teardown
    still happens — the same reasoning `RequestLogWriter._write_remaining` records. A pool that
    cannot be closed is a socket the operating system reclaims anyway; a teardown that stops half
    way leaves the database engine and the counter store behind as well."""
    stubborn, ordinary = _Stubborn("bad"), _Adapter("good")

    await ProviderRegistry([stubborn, ordinary]).aclose()

    assert stubborn.closed == 1
    assert ordinary.closed == 1, "the failure stopped the teardown"


async def test_the_application_closes_its_providers_on_shutdown(monkeypatch: Any) -> None:
    """The wire, not the ends. Both halves existed for the mock provider and nothing joined them —
    which is the shape this repository names first in `docs/LESSONS.md`."""
    from fastapi.testclient import TestClient

    from aira_gateway.app import create_app
    from aira_gateway.config import GatewaySettings

    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    adapter = _Adapter("watched")
    app.state.providers = ProviderRegistry([adapter])

    with TestClient(app):
        pass  # entering and leaving runs the lifespan, which is what is under test

    assert adapter.closed == 1
