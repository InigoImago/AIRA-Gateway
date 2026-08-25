"""What the gateway does with a model declaration (FRD-114).

The requirement everything here turns on is FR-7: **an undeclared model gets the baseline, and
nothing more.** The tempting default is the opposite — accept everything and let the provider
complain — and it is wrong for the same reason "unpriced is not free" is wrong. Absence of
information is not permission.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_common.models import BASELINE_CAPABILITIES, Capability, Hosting
from aira_gateway.app import create_app
from aira_gateway.catalog import ModelCatalog
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


def _app(**settings: Any):  # noqa: ANN201
    return create_app(GatewaySettings(auth_required=False, log_queue_size=0, **settings))


async def _declare(app, model: str, **fields: Any) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=model, **fields))
        await session.commit()


# -- FR-7: undeclared means the baseline, and nothing more -------------------------------


async def test_a_model_absent_from_the_catalog_gets_the_baseline() -> None:
    """Nothing must regress: generation and embedding worked before this FRD and still do."""
    app = _app()
    with TestClient(app):
        declaration = await ModelCatalog(app.state.db_sessionmaker).declaration("nowhere-1")

    assert declaration.declared is False
    assert declaration.capabilities == BASELINE_CAPABILITIES
    assert declaration.can(Capability.GENERATE)
    assert declaration.can(Capability.EMBED)


async def test_an_undeclared_model_may_not_do_anything_beyond_the_baseline() -> None:
    """This is the requirement. An undeclared model would otherwise accept a 32 768-token
    thinking budget that the pre-dispatch reservation has nothing to estimate against."""
    app = _app()
    with TestClient(app):
        declaration = await ModelCatalog(app.state.db_sessionmaker).declaration("nowhere-1")

    assert not declaration.can(Capability.THINKING)
    assert not declaration.can(Capability.STRUCTURED_OUTPUT)
    assert not declaration.can(Capability.ATTACHMENTS)
    assert declaration.media_types == frozenset()


async def test_a_row_with_only_a_price_is_still_undeclared() -> None:
    """Every installation that predates this FRD looks like this, and generation must keep
    working for all of them."""
    app = _app()
    with TestClient(app):
        await _declare(
            app, "priced-1", input_price_per_million_nanos=1, output_price_per_million_nanos=2
        )
        declaration = await ModelCatalog(app.state.db_sessionmaker).declaration("priced-1")

    assert declaration.declared is False
    assert declaration.can(Capability.GENERATE)
    assert not declaration.can(Capability.THINKING)


async def test_a_declared_model_gets_exactly_what_it_declares() -> None:
    app = _app()
    with TestClient(app):
        await _declare(app, "claude-1", capabilities=["generate", "thinking"])
        declaration = await ModelCatalog(app.state.db_sessionmaker).declaration("claude-1")

    assert declaration.declared is True
    assert declaration.can(Capability.GENERATE)
    assert declaration.can(Capability.THINKING)
    # Declared *and* narrower than the baseline: embedding is not in the list, so it is refused.
    assert not declaration.can(Capability.EMBED)


async def test_an_unknown_capability_in_the_read_model_is_dropped() -> None:
    """A Management release that adds a capability must not stop an older gateway applying the
    rest of the event — and dropping is the fail-closed direction, since an unrecognised
    capability is one this gateway could not enforce anyway."""
    app = _app()
    with TestClient(app):
        await _declare(app, "future-1", capabilities=["generate", "telepathy"])
        declaration = await ModelCatalog(app.state.db_sessionmaker).declaration("future-1")

    assert declaration.capabilities == frozenset({Capability.GENERATE})


# -- output caps ---------------------------------------------------------------------------


async def test_a_request_above_the_declared_output_cap_is_refused() -> None:
    """Refused here rather than passed on for the provider to reject differently: the same
    mistake would otherwise produce a different error per vendor."""
    app = _app()
    with TestClient(app) as client:
        # The catalog and the provider registry are separate authorities (FRD-114 §5.2): the
        # registry answers "can an adapter reach this", the catalog "what may it be asked to do".
        await _declare(app, "mock-1", capabilities=["generate"], max_output_tokens=1024)
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={**_BODY, "generationConfig": {"maxOutputTokens": 4096}},
        )

    assert response.status_code == 400
    assert "1024" in response.json()["error"]["message"]


async def test_a_request_at_the_declared_cap_is_accepted() -> None:
    app = _app()
    with TestClient(app) as client:
        await _declare(app, "mock-1", capabilities=["generate"], max_output_tokens=1024)
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={**_BODY, "generationConfig": {"maxOutputTokens": 1024}},
        )

    assert response.status_code == 200


async def test_an_undeclared_cap_bounds_nothing() -> None:
    """A model nobody has described must not start refusing requests that worked yesterday."""
    app = _app()
    with TestClient(app) as client:
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={**_BODY, "generationConfig": {"maxOutputTokens": 999_999}},
        )

    assert response.status_code == 200


def test_the_model_default_is_used_when_the_caller_sets_no_cap() -> None:
    """Anthropic **requires** ``max_tokens`` (`FRD-119` §5.3), so a caller who omits it would
    otherwise get a vendor error about a field they never set."""
    from aira_gateway.catalog import ModelDeclaration

    declaration = ModelDeclaration(name="m", default_max_output_tokens=4096)

    assert declaration.output_cap(None) == 4096
    assert declaration.output_cap(512) == 512, "the caller's own bound must win"


# -- capabilities, enforced ------------------------------------------------------------------


async def test_embedding_a_model_that_declares_no_embedding_is_refused_before_dispatch() -> None:
    """With cross-vendor routing a chain can send an embedding to a model with no embedding
    endpoint at all (Anthropic has none). The useful error names the model, and it arrives before
    an adapter raises deep in the stack (`FRD-113` FR-6a)."""
    app = _app()
    with TestClient(app) as client:
        await _declare(app, "mock-1", capabilities=["generate"])
        response = client.post(
            "/v1beta/models/mock-1:embedContent",
            json={"content": {"parts": [{"text": "hi"}]}},
        )

    assert response.status_code == 400
    assert "does not support embedding" in response.json()["error"]["message"]
    assert "mock-1" in response.json()["error"]["message"]


async def test_generation_on_an_embedding_only_model_is_refused() -> None:
    app = _app()
    with TestClient(app) as client:
        await _declare(app, "mock-1", capabilities=["embed"])
        response = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert response.status_code == 400
    assert "does not support generation" in response.json()["error"]["message"]


async def test_a_capability_refusal_is_recorded_in_the_audit_trail() -> None:
    """The refusal has to be reviewable like any other (FRD-122)."""
    from sqlalchemy import select

    from aira_gateway.db.models import RequestLog

    app = _app()
    with TestClient(app) as client:
        await _declare(app, "mock-1", capabilities=["embed"])
        client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert [row.outcome for row in rows] == ["invalid_request"]


# -- deprecation warns, it does not block ------------------------------------------------------


async def test_a_deprecated_model_still_answers_and_says_so() -> None:
    """Blocking is what `FRD-307`'s revocation is for. Conflating the two removes the ability to
    announce a retirement before performing one."""
    app = _app()
    with TestClient(app) as client:
        await _declare(app, "mock-1", capabilities=["generate"], deprecated=True)
        response = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert response.status_code == 200
    assert "deprecated" in response.headers["Warning"]
    assert "mock-1" in response.headers["Warning"]


async def test_a_current_model_carries_no_warning() -> None:
    app = _app()
    with TestClient(app) as client:
        await _declare(app, "mock-1", capabilities=["generate"])
        response = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert "Warning" not in response.headers


# -- hosting ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hosting", "expected"),
    [(Hosting.SELF_DEPLOYED, True), (Hosting.MANAGED, False), ("", False)],
)
async def test_hosting_is_readable_because_the_two_fail_differently(
    hosting: str, expected: bool
) -> None:
    """A self-deployed endpoint cold-starts for minutes and answers 429 for capacity rather than
    quota, so the dispatch timeout and the readiness probe read this (`ADR-0012` §5)."""
    app = _app()
    with TestClient(app):
        await _declare(app, "nemotron-1", capabilities=["generate"], hosting=str(hosting))
        declaration = await ModelCatalog(app.state.db_sessionmaker).declaration("nemotron-1")

    assert declaration.is_self_deployed is expected


# -- distribution ---------------------------------------------------------------------------


async def test_an_older_payload_applies_prices_without_blanking_a_declaration() -> None:
    """A rolling deploy runs an older Management alongside a newer gateway. Its event carries the
    FRD-403 fields only, and applying it must not erase a declaration somebody made — while an
    event that *does* carry the field with a null is the same event saying "no longer declared"."""
    from aira_gateway.consumer.apply import apply_event

    app = _app()
    with TestClient(app):
        await _declare(app, "mock-1", capabilities=["generate", "thinking"], max_output_tokens=8192)

        async with app.state.db_sessionmaker() as session:
            await apply_event(
                session,
                "model.upserted",
                {
                    "name": "mock-1",
                    "display_name": "Mock",
                    "input_price_per_million": "1",
                    "output_price_per_million": "2",
                },
            )
            await session.commit()

        declaration = await ModelCatalog(app.state.db_sessionmaker).declaration("mock-1")

    assert declaration.can(Capability.THINKING), "an older payload erased a declaration"
    assert declaration.max_output_tokens == 8192


async def test_a_payload_that_carries_a_null_clears_the_declaration() -> None:
    """The counterpart: withdrawing a capability has to actually withdraw it."""
    from aira_gateway.consumer.apply import apply_event

    app = _app()
    with TestClient(app):
        await _declare(app, "mock-1", capabilities=["generate", "thinking"], max_output_tokens=8192)

        async with app.state.db_sessionmaker() as session:
            await apply_event(
                session,
                "model.upserted",
                {"name": "mock-1", "capabilities": ["generate"], "max_output_tokens": None},
            )
            await session.commit()

        declaration = await ModelCatalog(app.state.db_sessionmaker).declaration("mock-1")

    assert not declaration.can(Capability.THINKING)
    assert declaration.max_output_tokens is None


async def test_a_declaration_reaches_the_model_list() -> None:
    """FR-8's payoff: a client can discover what a model may be asked to do, and see when nobody
    has said.

    The undeclared model is **put there by this test**. It used to be whatever else the registry
    happened to serve, which on a developer's machine meant a Gemini upstream conjured out of a
    `.env` nobody had read — so the assertion held everywhere the project was written and had
    nothing to stand on in CI, where the mock serves exactly one model. A test that needs two
    models says so.
    """
    from aira_gateway.upstreams.base import ProviderRegistry
    from aira_gateway.upstreams.mock import MockProvider

    app = _app()
    app.state.providers = ProviderRegistry([MockProvider("mock-1", "mock-undeclared")])
    with TestClient(app) as client:
        await _declare(app, "mock-1", capabilities=["generate", "thinking"], max_output_tokens=4096)
        listed = {
            m["name"].removeprefix("models/"): m
            for m in client.get("/v1beta/models").json()["models"]
        }

    described = listed["mock-1"]
    assert described["airaCapabilities"] == ["generate", "thinking"]
    assert described["airaMaxOutputTokens"] == 4096
    assert described["airaDeclared"] is True

    # Every other model the registry serves is undeclared, and says so — surfaced the same way
    # an unpriced one is, because an undeclared model quietly does less than the list suggests.
    others = [m for name, m in listed.items() if name != "mock-1"]
    assert others, "the registry serves nothing but the declared model, so nothing is proved"
    assert all(m["airaDeclared"] is False for m in others)


async def test_the_model_list_publishes_the_two_limits_google_publishes() -> None:
    """`FRD-132` §11.

    A client sizes a conversation against `inputTokenLimit`, and every assistant's *"12% of the
    context used"* is that number underneath. AIRA published the output half under an **invented**
    name — `airaMaxOutputTokens`, beside the standard one — and the input half not at all, so a
    client written against Google read nothing. Measured: OpenCode resolved
    `limit: {context: 0, output: 0}` and showed a gauge stuck at 0%.

    The extension stays beside the standard field, carrying the same figure: withdrawing a field a
    caller has been reading since `FRD-114` is not a tidy-up a compatibility surface gets to do.
    """
    from aira_gateway.upstreams.base import ProviderRegistry
    from aira_gateway.upstreams.mock import MockProvider

    app = _app()
    app.state.providers = ProviderRegistry([MockProvider("mock-1")])
    with TestClient(app) as client:
        await _declare(app, "mock-1", context_window=40960, max_output_tokens=4096)
        listed = client.get("/v1beta/models").json()["models"][0]

    assert listed["inputTokenLimit"] == 40960
    assert listed["outputTokenLimit"] == 4096
    assert listed["airaMaxOutputTokens"] == 4096


async def test_a_limit_nobody_declared_is_absent_rather_than_zero() -> None:
    """**The whole reason this is worth a test.**

    Google omits a limit it has no figure for. A zero is not "unknown" to a client — it is a full
    context window, and a gauge that divides by it either never moves or reads as complete. So an
    undeclared model must carry no key at all, and `model_dump()` without `exclude_none` would have
    sent `null`, which some clients coerce to the same zero.
    """
    from aira_gateway.upstreams.base import ProviderRegistry
    from aira_gateway.upstreams.mock import MockProvider

    app = _app()
    app.state.providers = ProviderRegistry([MockProvider("mock-1")])
    with TestClient(app) as client:
        listed = client.get("/v1beta/models").json()["models"][0]

    assert "inputTokenLimit" not in listed
    assert "outputTokenLimit" not in listed


async def test_a_catalogued_model_is_served_without_being_configured_too() -> None:
    """**The second list, removed** (`FRD-507`).

    A model had to be named twice: in the adapter's configuration so it would be offered, and in
    the catalog so `FRD-307` would permit it. The catalog is already the authority on what may be
    served — a row names its provider, and that is enough to know who serves it. So an
    administrator catalogues a model and it works, rather than catalogues it and then edits an
    environment variable and restarts.

    Asserted through the registry, which is where the two lists met.
    """
    from aira_gateway.upstreams.base import ProviderRegistry
    from aira_gateway.upstreams.mock import MockProvider

    class _Namespaced(MockProvider):
        serves_provider = "make-believe"

    registry = ProviderRegistry([_Namespaced("configured-1")])

    # Named nowhere in configuration…
    assert registry.provider_for("never-configured") is None
    # …but catalogued under a provider this adapter owns.
    assert registry.provider_for("never-configured", "make-believe") is not None
    # And an unrelated provider still resolves to nothing, rather than to whoever is first.
    assert registry.provider_for("never-configured", "somebody-else") is None


def test_two_adapters_cannot_claim_one_provider() -> None:
    """`ADR-0011`'s rule one level down: an ambiguous routing table refuses to boot. Two adapters
    owning the same provider name would decide a catalogued model's region and credential by
    registration order — silently, and differently after a restart."""
    from aira_gateway.upstreams.base import AmbiguousModel, ProviderRegistry
    from aira_gateway.upstreams.mock import MockProvider

    class _One(MockProvider):
        serves_provider = "contested"

    class _Two(MockProvider):
        serves_provider = "contested"

    with pytest.raises(AmbiguousModel, match="contested"):
        ProviderRegistry([_One("a"), _Two("b")])


def test_a_catalogued_model_still_records_where_it_ran() -> None:
    """**The regression that sent this feature back once.**

    Provenance is read from the registry, and a model resolved through the catalog has no entry
    there — so the first working version wrote `provider` and `region` **empty** onto the audit
    row. That is worse than the second list the feature removes: `FRD-115`'s point is that "the
    configuration says EU" is a claim and "this request went to `eu`" is evidence, and a blank
    column is neither. The adapter that owns the provider answers instead.
    """
    from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel
    from aira_gateway.upstreams.mock import MockProvider

    class _Somewhere(MockProvider):
        serves_provider = "elsewhere"

        def models(self) -> list[UpstreamModel]:
            return [
                UpstreamModel(
                    "configured", "configured", ("generateContent",), "elsewhere", "acme", "eu-west"
                )
            ]

    registry = ProviderRegistry([_Somewhere()])

    assert registry.provenance_for("elsewhere") == ("elsewhere", "acme", "eu-west")
    assert registry.provenance_for("nobody") is None


def test_an_adapter_that_serves_no_configured_model_still_states_where_it_is() -> None:
    """With an empty configured list there is no `UpstreamModel` to read provenance from, and that
    is exactly the arrangement this feature makes ordinary. An adapter says it once, as a property
    of itself."""
    from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel
    from aira_gateway.upstreams.mock import MockProvider

    class _Empty(MockProvider):
        serves_provider = "bare"
        provenance = ("bare", "acme", "global")

        def models(self) -> list[UpstreamModel]:
            return []

    assert ProviderRegistry([_Empty()]).provenance_for("bare") == ("bare", "acme", "global")
