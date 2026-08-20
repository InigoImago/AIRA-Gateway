"""What does this credential actually put within reach (`FRD-507` stage C)?

The question behind it: _"warum können wir es denn nicht automatisieren? man wählt google, dann hat
man die liste, die man auswählen kann und es wird nach und nach importiert"_ — and the answer is
that nothing prevented it except that nobody had asked the vendor.

Three lists, and the tests here are mostly about keeping them apart: what a vendor **offers**, what
this gateway **serves**, and what the catalog **permits**. Collapsing any two of them produces a
screen that is confidently wrong rather than empty.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.upstreams.base import (
    OfferedModel,
    ProviderRegistry,
    UpstreamError,
    UpstreamModel,
)
from aira_gateway.upstreams.gemini import GeminiUpstream

GLOBAL_ADMIN = Principal(subject="root", method="oidc", roles=("global-admin",))
IT_SECURITY = Principal(subject="sec", method="oidc", roles=("it-security",))
IT_STEUERUNG = Principal(subject="gov", method="oidc", roles=("it-steuerung",))
NOBODY = Principal(subject="user", method="oidc", roles=())


class _Offering:
    """An adapter that can be asked what its vendor offers, and serves nothing configured.

    Deliberately both at once: since cataloguing became enough to serve a model, that is the
    ordinary shape of a working Google AI Studio deployment, and it is the shape every consumer
    that walked `registry.models()` could not see at all.
    """

    serves_provider = "vendor-x"
    provenance = ("vendor-x", "somebody", "europe-west1")
    enumerates = True

    def models(self) -> list[UpstreamModel]:
        return []

    async def available_models(self) -> list[OfferedModel]:
        return [OfferedModel(name="one", display_name="One", max_output_tokens=99)]


class _Mute:
    """A platform with no listing worth asking for."""

    serves_provider = "vendor-y"

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("m", "m", (), "vendor-y", "pub", "eu")]


class _Broken:
    serves_provider = "vendor-z"
    provenance = ("vendor-z", "pub", "eu")
    enumerates = True

    def models(self) -> list[UpstreamModel]:
        return []

    async def available_models(self) -> list[OfferedModel]:
        raise UpstreamError("https://api.example/v1beta/models?key=super-secret-value", 403)


class _OneOfTwoDialects:
    """One of the two adapters an EU Vertex deployment registers under a single provider name."""

    def __init__(self, publisher: str) -> None:
        self._publisher = publisher

    enumerates = True

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("m-" + self._publisher, "m", (), "vertex", self._publisher, "eu")]

    async def available_models(self) -> list[OfferedModel]:
        return [OfferedModel(name="whatever-this-dialect-serves")]


def _client(principal: Principal, *providers: Any) -> TestClient:
    app = create_app(GatewaySettings(auth_required=False))
    app.dependency_overrides[require_principal] = lambda: principal
    if providers:
        app.state.providers = ProviderRegistry(list(providers))
    return TestClient(app)


# == who may ask ==================================================================================


def test_only_a_global_administrator_may_ask_a_provider_what_it_offers() -> None:
    """The same role that may catalogue, because this listing is only useful to somebody who can
    act on it — and `CATALOG_ROLES` is where both planes read that from, so the console's viewset
    and this endpoint cannot answer differently.

    IT Security is the interesting refusal: it is *not* the incident question, so reusing
    `may_act_on_incidents` here would have handed a model declaration to the role PRD §154 gives
    oversight and no writes."""
    for principal in (IT_SECURITY, IT_STEUERUNG, NOBODY):
        response = _client(principal, _Offering()).get("/v1beta/providers")
        assert response.status_code == 403, principal.roles
        assert "Global Administrator" in response.json()["error"]["message"]


def test_a_global_administrator_sees_the_configured_providers() -> None:
    response = _client(GLOBAL_ADMIN, _Offering(), _Mute()).get("/v1beta/providers")

    assert response.status_code == 200
    entries = {entry["name"]: entry for entry in response.json()["providers"]}
    assert entries["vendor-x"]["canEnumerate"] is True
    assert entries["vendor-x"]["region"] == "europe-west1"
    # Read off the adapter's own models when it declares no tuple — the provenance is per model
    # (`FRD-115` FR-10) and this is where it already lives.
    assert entries["vendor-y"]["region"] == "eu"
    assert entries["vendor-y"]["canEnumerate"] is False


def test_a_provider_is_named_the_way_a_reader_would_recognise_it() -> None:
    """`generative-language` beside `local` names neither vendor — reported from the running
    console. The **name** stays the identifier (it goes in the catalog, on every audit row and into
    routing); the label is what a picker shows, and it comes from the adapter rather than from a
    map in the console, which would be a second vocabulary restated in TypeScript."""
    studio = GeminiUpstream("k", [], httpx.AsyncClient())
    response = _client(GLOBAL_ADMIN, studio, _Mute()).get("/v1beta/providers")

    entries = {entry["name"]: entry for entry in response.json()["providers"]}
    assert entries["generative-language"]["label"] == "Google AI Studio"
    # An adapter that declares none is unlabelled, not unnamed: the bare name is exactly what was
    # on screen before labels existed.
    assert entries["vendor-y"]["label"] == "vendor-y"


def test_a_provider_reachable_only_through_the_catalog_says_so() -> None:
    """`cataloguedIsEnough` is the field that decides whether an import produces a working model
    or a convincing decoration. An adapter that owns its provider name serves whatever the catalog
    names (stage B); one that does not needs the model in the gateway's configuration as well, and
    an administrator who is not told that finds out from a caller."""
    response = _client(GLOBAL_ADMIN, _Offering(), _OneOfTwoDialects("google")).get(
        "/v1beta/providers"
    )

    entries = {entry["name"]: entry for entry in response.json()["providers"]}
    assert entries["vendor-x"]["cataloguedIsEnough"] is True
    assert entries["vertex"]["cataloguedIsEnough"] is False


def test_two_adapters_under_one_provider_name_are_one_entry_that_cannot_be_asked() -> None:
    """An EU Vertex deployment registers **two** adapters — Gemini and Anthropic, one platform,
    one credential, two dialects — and both stamp `vertex`. Keying by name and keeping the last
    would describe the provider with whichever registered second, and offer a listing that answers
    for one dialect while claiming to answer for the platform."""
    client = _client(GLOBAL_ADMIN, _OneOfTwoDialects("google"), _OneOfTwoDialects("anthropic"))

    listing = client.get("/v1beta/providers").json()["providers"]
    assert [entry["name"] for entry in listing] == ["vertex"]
    assert listing[0]["adapters"] == 2
    assert listing[0]["canEnumerate"] is False

    offerings = client.get("/v1beta/providers/vertex/offerings")
    assert offerings.status_code == 501


def test_a_provider_with_no_listing_says_so_rather_than_offering_nothing() -> None:
    """`501`, not an empty list. "This platform cannot be asked" and "your credential reaches
    nothing" are different facts, and a picker that renders both as an empty dropdown sends an
    administrator looking for a broken key that is perfectly fine."""
    response = _client(GLOBAL_ADMIN, _Mute()).get("/v1beta/providers/vendor-y/offerings")

    assert response.status_code == 501
    assert "cannot be asked" in response.json()["error"]["message"]


def test_an_unknown_provider_is_a_404_naming_it() -> None:
    response = _client(GLOBAL_ADMIN, _Offering()).get("/v1beta/providers/typo/offerings")

    assert response.status_code == 404
    assert "typo" in response.json()["error"]["message"]


def test_the_upstreams_own_words_are_not_repeated_back() -> None:
    """`FRD-506`'s rule, and it earns its keep on exactly this adapter family: the Generative
    Language API takes its credential **in the URL**, so an error mentioning the request it
    refused mentions the key."""
    response = _client(GLOBAL_ADMIN, _Broken()).get("/v1beta/providers/vendor-z/offerings")

    assert response.status_code == 502
    assert "super-secret-value" not in response.text
    assert "credential or endpoint" in response.json()["error"]["message"]


def test_the_offering_is_carried_whole() -> None:
    """Every capability the vendor did not state travels as `null`.

    The property an eager serialiser breaks: `False` is a *statement* that a model cannot do
    something, and a form pre-filled from it records the vendor's silence as the administrator's
    decision (`FRD-114` FR-7)."""
    response = _client(GLOBAL_ADMIN, _Offering()).get("/v1beta/providers/vendor-x/offerings")

    assert response.status_code == 200
    assert response.json()["models"] == [
        {
            "name": "one",
            "displayName": "One",
            "description": "",
            "maxOutputTokens": 99,
            "canGenerate": None,
            "canEmbed": None,
            "canCachePrompts": None,
            "thinking": None,
        }
    ]


# == what Google actually returns =================================================================


def _google(handler: Any) -> GeminiUpstream:
    client = httpx.AsyncClient(
        base_url="https://api.test/v1beta", transport=httpx.MockTransport(handler)
    )
    return GeminiUpstream("secret-key", [], client)


def _entry(name: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": f"models/{name}",
        "displayName": name.title(),
        "outputTokenLimit": 65536,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
        **extra,
    }


async def test_the_listing_is_read_to_its_last_page() -> None:
    """50-odd entries arrive in pages. A listing that stopped at the first one would leave a model
    missing from the picker with **nothing on screen saying anything was cut off** — the same
    silence as a truncated fallback chain, and the administrator concludes their key does not
    include the model they are looking for."""
    pages = {
        "": {"models": [_entry("a")], "nextPageToken": "second"},
        "second": {"models": [_entry("b")]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json=pages[request.url.params.get("pageToken", "")])

    offered = await _google(handler).available_models()

    assert [model.name for model in offered] == ["a", "b"]


async def test_a_runaway_page_token_cannot_hold_the_request_open() -> None:
    """The loop is driven by a value the *vendor* controls. Bounded, because an unbounded remote
    loop is not a slow response, it is one that never comes."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [_entry("a")], "nextPageToken": "again"})

    assert len(await _google(handler).available_models()) < 100


async def test_the_models_prefix_is_stripped() -> None:
    """Google says `models/gemini-flash-latest`; every other layer here — the catalog, the audit
    row, the caller's own request — uses the bare name. A prefixed entry reaching the catalog is a
    declaration no request can ever match, and `FRD-307` would then refuse the model as
    uncatalogued while the console shows it plainly catalogued."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [_entry("gemini-flash-latest")]})

    assert (await _google(handler).available_models())[0].name == "gemini-flash-latest"


@pytest.mark.parametrize(
    ("methods", "expected"),
    [
        (["generateContent"], {"can_generate": True}),
        (["embedContent"], {"can_embed": True}),
        (
            ["generateContent", "createCachedContent"],
            {"can_generate": True, "can_cache_prompts": True},
        ),
        (["countTokens"], {}),
    ],
)
async def test_the_method_list_is_the_capability(
    methods: list[str], expected: dict[str, bool]
) -> None:
    """These three are facts rather than claims: the API returns 404 for a method it does not
    list, so a verb missing from an exhaustive list really is a "no" — which is why they may be
    pre-filled while `tools` and `structured_output` may not.

    `createCachedContent` is how prompt caching appears. The word "caching" is nowhere in the
    response, so an implementation reading the obvious field finds nothing and declares no caching
    for a model that has it (`FRD-133`)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"models": [_entry("m", supportedGenerationMethods=methods)]}
        )

    model = (await _google(handler).available_models())[0]
    for field in ("can_generate", "can_embed", "can_cache_prompts"):
        assert getattr(model, field) is expected.get(field, False), field


async def test_a_listing_with_no_method_list_at_all_states_nothing() -> None:
    """The distinction the parametrised case above cannot make: an **empty** method list says the
    model supports nothing, an **absent** one says the vendor did not answer the question. Both
    would serialise to `false` under a `bool()`, and the second would put a decision nobody made
    into the form."""

    def handler(_request: httpx.Request) -> httpx.Response:
        entry = _entry("quiet")
        del entry["supportedGenerationMethods"]
        return httpx.Response(200, json={"models": [entry, _entry("empty", **{})]})

    quiet = (await _google(handler).available_models())[0]
    assert quiet.can_generate is None
    assert quiet.can_embed is None


async def test_a_vendor_that_says_nothing_about_thinking_has_not_said_no() -> None:
    """`None`, never `False`. `FRD-114` FR-7 in one field: absence of information is not a
    declaration, and a form pre-filled with an unticked box records the vendor's silence as the
    administrator's decision."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"models": [_entry("quiet"), _entry("loud", thinking=True)]}
        )

    quiet, loud = await _google(handler).available_models()
    assert quiet.thinking is None
    assert loud.thinking is True


async def test_an_output_limit_is_a_number_or_it_is_nothing() -> None:
    """A vendor that answers `0`, or a string, has declined to say — and a zero copied into the
    catalog is an output cap no request can satisfy."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    _entry("zero", outputTokenLimit=0),
                    _entry("text", outputTokenLimit="65536"),
                    _entry("real"),
                ]
            },
        )

    zero, text, real = await _google(handler).available_models()
    assert zero.max_output_tokens is None
    assert text.max_output_tokens is None
    assert real.max_output_tokens == 65536


async def test_the_credential_never_reaches_the_path() -> None:
    """It goes in the query, as it does for every other call this adapter makes."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"models": []})

    await _google(handler).available_models()
    assert "key=secret-key" in seen[0]


async def test_the_listing_is_also_the_cheap_reachability_question() -> None:
    """A **GET**, never a generation (`FRD-117` §5.2). This adapter had no probe at all until the
    listing existed, so `/readyz` and `FRD-506`'s check both reported it as unprobed — honestly,
    and with nothing behind the honesty."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [_entry("a"), _entry("b")]})

    assert "2 model(s)" in await _google(handler).ping()


# == the adapter that serves nothing configured ===================================================


def test_an_adapter_reached_only_through_the_catalog_is_still_registered() -> None:
    """The readiness probe walked `registry.models()`, so an adapter with an **empty** configured
    list was not probed at all — and that is precisely the shape stage B created and a working AI
    Studio deployment now has: the catalog names the models and configuration names none.

    `/readyz` then said nothing whatsoever about that upstream. Absent reads as "no such thing",
    which is the wrong half of `FRD-117`'s distinction between *we did not look* and *it is fine*.
    """
    registry = ProviderRegistry([_Offering()])

    assert registry.models() == []
    assert list(registry.by_name()) == ["vendor-x"]
    assert len(registry.each()) == 1


# == where this installation permits processing (`ADR-0012` §6) ==================================


def _regions(settings: str | None = None) -> Any:
    """The providers answer, from a gateway configured with `settings` as its allow-list."""
    app = create_app(
        GatewaySettings(auth_required=False, **({"allowed_regions": settings} if settings else {}))
    )
    app.dependency_overrides[require_principal] = lambda: GLOBAL_ADMIN
    app.state.providers = ProviderRegistry([_Offering()])
    return TestClient(app).get("/v1beta/providers").json()


def test_the_providers_answer_carries_the_regions_this_installation_permits() -> None:
    """**One list, published by its owner** rather than restated by the console.

    Residency is the gateway's policy and the gateway enforces it when it *addresses* a request —
    correct, and weeks after somebody catalogued the model, who then hears nothing until a caller
    gets a 4xx. The console can refuse at authoring time only if it knows the list, and it must not
    know it by holding a copy: a second answer to a residency question is how two planes come to
    disagree about what this installation is allowed to do.

    It rides on this answer rather than getting an endpoint, because the model editor uses both
    facts in the same breath — *which provider*, and *where*.
    """
    body = _regions("europe-west1,eu")

    assert body["allowedRegions"] == ["eu", "europe-west1"]
    # Beside the providers, not instead of them.
    assert [entry["name"] for entry in body["providers"]] == ["vendor-x"]


def test_an_unset_allow_list_publishes_the_eu_defaults_rather_than_nothing() -> None:
    """`parse_allowed` falls back to the EU regions of every supported cloud, because a residency
    constraint that has to be switched on is one that will be found switched off. The published
    list has to say the same thing, or a console reading it would offer no region at all on a
    deployment that permits several."""
    from aira_gateway.residency import DEFAULT_ALLOWED_REGIONS

    assert _regions()["allowedRegions"] == sorted(DEFAULT_ALLOWED_REGIONS)


def test_global_is_published_only_where_it_is_configured() -> None:
    """`global` names no region and guarantees none. It is not in the shipped default, and a
    deployment that adds it has said something specific — which the console must be able to see,
    because otherwise it would refuse a region this installation does permit."""
    assert "global" not in _regions()["allowedRegions"]
    assert "global" in _regions("global,europe-west1")["allowedRegions"]
