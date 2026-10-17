"""What a model costs this installation, from its Google billing account (`FRD-509`).

Hermetic: an `httpx.MockTransport` plays the Cloud Billing Pricing API (v1beta) in the shapes its
reference documents — services, SKUs with a `geoTaxonomy`, and prices whose base tier carries a
`listPrice` and, under a contract, a `contractPrice`. What these hold is the matching, because a
SKU is named in prose: the right line of the bill for each field, the region before a multi-region
before global, the contract before the list, and nothing taken that is not a per-token text price.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from aira_common.tokens import StaticTokenSource
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.vertex.billing import (
    BillingPrices,
    kind_of,
    model_phrase,
    per_million,
    sku_phrase,
)

ACCOUNT = "012345-567890-ABCDEF"
VERTEX = f"billingAccounts/{ACCOUNT}/services/C7E2-9256-1C43"
CLAUDE = f"billingAccounts/{ACCOUNT}/services/AAAA-BBBB-CCCC"


def _sku(sku_id: str, name: str, region: str | None = None, *regions: str) -> dict[str, Any]:
    if region:
        geo = {"type": "TYPE_REGIONAL", "regionalMetadata": {"region": {"region": region}}}
    elif regions:
        geo = {
            "type": "TYPE_MULTI_REGIONAL",
            "multiRegionalMetadata": {"regions": [{"region": r} for r in regions]},
        }
    else:
        geo = {"type": "TYPE_GLOBAL", "globalMetadata": {}}
    return {"skuId": sku_id, "displayName": name, "geoTaxonomy": geo}


def _price(
    units: int,
    nanos: int,
    *,
    contract: tuple[int, int] | None = None,
    per: int = 1_000_000,
    unit: str = "1 million tokens",
    currency: str = "EUR",
) -> dict[str, Any]:
    tier: dict[str, Any] = {
        "startAmount": {"value": "0"},
        "listPrice": {"currencyCode": currency, "units": str(units), "nanos": nanos},
    }
    if contract:
        tier["contractPrice"] = {
            "currencyCode": currency,
            "units": str(contract[0]),
            "nanos": contract[1],
        }
    return {
        "currencyCode": currency,
        "valueType": "rate",
        "rate": {
            "tiers": [tier, {"startAmount": {"value": "200000"}, "listPrice": {"units": "99"}}],
            "unitInfo": {
                "unit": "count",
                "unitDescription": unit,
                "unitQuantity": {"value": str(per)},
            },
        },
    }


SKUS = {
    VERTEX: [
        _sku("G-IN-EW1", "Gemini 2.5 Flash Input Text Tokens", "europe-west1"),
        _sku("G-IN-EU", "Gemini 2.5 Flash Input Text Tokens", None, "europe-west1", "europe-west4"),
        _sku("G-IN-GL", "Gemini 2.5 Flash Input Text Tokens"),
        _sku("G-OUT-GL", "Gemini 2.5 Flash Output Text Tokens"),
        _sku("G-CACHE", "Gemini 2.5 Flash Cached Input Text Tokens"),
        _sku("G-LITE-IN", "Gemini 2.5 Flash Lite Input Text Tokens", "europe-west1"),
        _sku("G-BATCH", "Gemini 2.5 Flash Batch Input Text Tokens", "europe-west1"),
        _sku("G-AUDIO", "Gemini 2.5 Flash Input Audio Tokens", "europe-west1"),
        _sku("G-LONG", "Gemini 2.5 Flash Input Text Tokens Long Context", "europe-west1"),
        _sku("OLD-CHARS", "Gemini 1.0 Pro Input Text Characters", "europe-west1"),
    ],
    CLAUDE: [
        _sku("C-IN", "Claude Sonnet 4.5 Input Tokens", "europe-west1"),
        _sku("C-OUT", "Claude Sonnet 4.5 Output Tokens", "europe-west1"),
        _sku("C-READ", "Claude Sonnet 4.5 Prompt Caching Read Tokens", "europe-west1"),
        _sku("C-WRITE", "Claude Sonnet 4.5 Prompt Caching Write Tokens", "europe-west1"),
    ],
}

PRICES = {
    "G-IN-EW1": _price(0, 280_000_000),
    "G-IN-EU": _price(0, 290_000_000),
    "G-IN-GL": _price(0, 300_000_000),
    "G-OUT-GL": _price(2, 300_000_000, contract=(2, 0)),
    "G-CACHE": _price(0, 28_000_000),
    "C-IN": _price(3, 300_000_000),
    "C-OUT": _price(16, 500_000_000),
    "C-READ": _price(0, 330_000_000),
    "C-WRITE": _price(4, 120_000_000),
    "OLD-CHARS": _price(0, 125_000, per=1000, unit="1 thousand characters"),
}


class Billing:
    """The Pricing API, recording what it was asked."""

    def __init__(self, skus: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.asked: list[httpx.Request] = []
        self.refusal: httpx.Response | None = None
        self.skus = SKUS if skus is None else skus

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if self.refusal is not None:
            return self.refusal
        path = request.url.path
        query = parse_qs(request.url.query.decode())
        if path.endswith("/services"):
            return httpx.Response(
                200,
                json={
                    "billingAccountServices": [
                        {"name": VERTEX, "displayName": "Vertex AI"},
                        {"name": CLAUDE, "displayName": "Claude Sonnet 4.5"},
                        {"name": "x/services/COMPUTE", "displayName": "Compute Engine"},
                    ]
                },
            )
        if path.endswith("/skus"):
            service = query["filter"][0].split('"')[1]
            listed = self.skus.get(service, [])
            # Two pages, so following `nextPageToken` is part of what is held.
            if not query.get("pageToken", [""])[0]:
                return httpx.Response(
                    200, json={"billingAccountSkus": listed[:2], "nextPageToken": "p2"}
                )
            return httpx.Response(200, json={"billingAccountSkus": listed[2:]})
        if path.endswith("/price"):
            sku = path.split("/skus/")[1].split("/")[0]
            return httpx.Response(200, json=PRICES[sku])
        return httpx.Response(404, json={})

    def lookup(self, currency: str = "EUR") -> BillingPrices:
        return BillingPrices(
            account=ACCOUNT,
            tokens=StaticTokenSource("t"),
            client=httpx.AsyncClient(transport=httpx.MockTransport(self)),
            currency=currency,
        )


# ---- FR-2: the matching --------------------------------------------------------------------------


def test_a_model_id_is_read_as_the_bill_spells_it() -> None:
    assert model_phrase("gemini-2.5-flash") == ["gemini", "2.5", "flash"]
    assert model_phrase("claude-sonnet-4-5@20250929") == ["claude", "sonnet", "4.5"]
    assert model_phrase("zai-org/glm-4.7-maas") == ["glm", "4.7"]


def test_a_sku_is_its_model_name_up_to_what_is_billed() -> None:
    assert sku_phrase("Gemini 2.5 Flash Input Text Tokens") == ["gemini", "2.5", "flash"]
    assert sku_phrase("Gemini 2.5 Flash Lite Input Text") == ["gemini", "2.5", "flash", "lite"]
    assert sku_phrase("Vertex AI: Gemini 2.5 Pro Output") == ["gemini", "2.5", "pro"]
    assert sku_phrase("Anthropic Claude Opus 4.1 Input Tokens") == ["claude", "opus", "4.1"]


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Gemini 2.5 Flash Input Text Tokens", "input"),
        ("Gemini 2.5 Flash Output Text Tokens", "output"),
        ("Gemini 2.5 Flash Cached Input Text Tokens", "cached_input"),
        ("Claude Sonnet 4.5 Prompt Caching Read Tokens", "cached_input"),
        ("Claude Sonnet 4.5 Prompt Caching Write Tokens", "cache_write"),
        ("Gemini 2.5 Flash Batch Input Text Tokens", None),
        ("Gemini 2.5 Flash Input Audio Tokens", None),
        ("Gemini 2.5 Flash Input Text Tokens Long Context", None),
        ("Gemini 2.5 Pro Input Text > 200K", None),
        ("Gemini 2.5 Flash Context Cache Storage", None),
        ("Gemini 2.5 Flash Grounding", None),
    ],
)
def test_what_a_sku_bills_decides_the_field(name: str, kind: str | None) -> None:
    assert kind_of(name) == kind


def test_the_base_tier_is_read_per_million_and_a_contract_wins() -> None:
    assert per_million(_price(0, 300_000_000)) == (Decimal("0.3"), False)
    amount, contract = per_million(_price(2, 300_000_000, contract=(2, 0)))  # type: ignore[misc]
    assert (amount, contract) == (2, True)
    thousand = per_million(_price(0, 1_000_000, per=1000, unit="1 thousand tokens"))
    assert thousand is not None and thousand[0] == 1


def test_a_price_not_per_token_is_not_converted() -> None:
    assert per_million(_price(0, 125_000, per=1000, unit="1 thousand characters")) is None
    assert per_million({"rate": {"tiers": [], "unitInfo": {"unit": "token"}}}) is None
    assert per_million({}) is None


# ---- FR-1: one model's prices, with the line of the bill each came from --------------------------


async def test_a_gemini_model_is_priced_from_its_own_skus_for_its_region() -> None:
    billing = Billing()

    answer = await billing.lookup().prices("gemini-2.5-flash", "europe-west1")

    prices = answer["prices"]
    assert prices["input"]["skuId"] == "G-IN-EW1", "the region's own SKU before the multi-region"
    assert prices["input"]["perMillion"] == "0.28"
    assert prices["output"] == {
        "perMillion": "2",
        "currency": "EUR",
        "skuId": "G-OUT-GL",
        "skuName": "Gemini 2.5 Flash Output Text Tokens",
        "region": "global",
        "contract": True,
    }
    assert prices["cached_input"]["perMillion"] == "0.028"
    assert answer["unpriced"] == {
        "cache_write": "no SKU on the account names this model and this price"
    }
    # Asked in the installation's currency, so Google converts, not this gateway.
    price_asks = [r for r in billing.asked if r.url.path.endswith("/price")]
    assert all(parse_qs(r.url.query.decode())["currencyCode"] == ["EUR"] for r in price_asks)
    assert all(r.headers["authorization"] == "Bearer t" for r in billing.asked)


async def test_a_longer_model_name_on_the_bill_is_another_model() -> None:
    """`Gemini 2.5 Flash Lite` contains `Gemini 2.5 Flash`, and costs a fraction of it."""
    billing = Billing(
        {VERTEX: [_sku("G-LITE-IN", "Gemini 2.5 Flash Lite Input Text", "europe-west1")]}
    )

    answer = await billing.lookup().prices("gemini-2.5-flash", "europe-west1")

    assert answer["prices"] == {}


async def test_a_multi_region_holding_the_region_comes_before_global() -> None:
    answer = await Billing().lookup().prices("gemini-2.5-flash", "europe-west4")

    assert answer["prices"]["input"]["skuId"] == "G-IN-EU"
    assert answer["prices"]["input"]["region"] == "europe-west1+europe-west4"


async def test_a_claude_model_is_priced_from_its_partner_service() -> None:
    answer = await Billing().lookup().prices("claude-sonnet-4-5@20250929", "europe-west1")

    assert {kind: p["skuId"] for kind, p in answer["prices"].items()} == {
        "input": "C-IN",
        "output": "C-OUT",
        "cached_input": "C-READ",
        "cache_write": "C-WRITE",
    }
    assert answer["prices"]["cache_write"]["perMillion"] == "4.12"


async def test_a_model_the_account_does_not_bill_is_unpriced_by_name() -> None:
    answer = await Billing().lookup().prices("gemini-9-ultra", "europe-west1")

    assert answer["prices"] == {}
    assert set(answer["unpriced"]) == {"input", "output", "cached_input", "cache_write"}


async def test_a_model_billed_per_character_is_said_to_be() -> None:
    answer = await Billing().lookup().prices("gemini-1.0-pro", "europe-west1")

    assert "not priced per token" in answer["unpriced"]["input"]


async def test_the_skus_are_held_and_the_prices_asked_each_time() -> None:
    billing = Billing()
    lookup = billing.lookup()

    await lookup.prices("gemini-2.5-flash", "europe-west1")
    listed = len([r for r in billing.asked if r.url.path.endswith("/skus")])
    await lookup.prices("gemini-2.5-flash", "europe-west1")

    assert len([r for r in billing.asked if r.url.path.endswith("/skus")]) == listed
    assert len([r for r in billing.asked if r.url.path.endswith("/price")]) == 6


async def test_a_refused_billing_account_names_the_role_to_grant() -> None:
    billing = Billing()
    billing.refusal = httpx.Response(
        403,
        json={
            "error": {
                "code": 403,
                "status": "PERMISSION_DENIED",
                "message": "denied",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "IAM_PERMISSION_DENIED",
                        "metadata": {"permission": "billing.accounts.getPricing"},
                    }
                ],
            }
        },
    )

    with pytest.raises(UpstreamError) as caught:
        await billing.lookup().prices("gemini-2.5-flash", "europe-west1")

    assert caught.value.diagnosis is not None
    assert "roles/billing.viewer" in caught.value.diagnosis.explanation


async def test_an_unreachable_billing_api_says_why() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    lookup = BillingPrices(
        account=ACCOUNT,
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(boom)),
        currency="EUR",
    )

    with pytest.raises(UpstreamError) as caught:
        await lookup.prices("gemini-2.5-flash", "")

    assert caught.value.diagnosis is not None
    assert caught.value.diagnosis.reason == "CONNECT_TIMEOUT"


# ---- FR-3: the endpoint --------------------------------------------------------------------------


def test_without_a_billing_account_the_endpoint_says_how_to_get_one() -> None:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app) as client:
        response = client.get("/v1beta/providers/mock/prices", params={"model": "m"})

    assert response.status_code == 501
    assert "AIRA_VERTEX_BILLING_ACCOUNT" in response.json()["error"]["message"]


def test_the_endpoint_answers_from_the_providers_billing_lookup() -> None:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    billing = Billing()
    with TestClient(app) as client:
        for upstream in app.state.providers.each():
            upstream.billing = billing.lookup()
        response = client.get(
            "/v1beta/providers/mock/prices",
            params={"model": "gemini-2.5-flash", "region": "europe-west1"},
        )
        billing.refusal = httpx.Response(403, json={})
        for upstream in app.state.providers.each():
            upstream.billing = billing.lookup()
        refused = client.get("/v1beta/providers/mock/prices", params={"model": "gemini-2.5-flash"})

    assert response.status_code == 200
    assert response.json()["prices"]["input"]["skuId"] == "G-IN-EW1"
    assert refused.status_code == 502
    assert "PERMISSION_DENIED" in refused.json()["error"]["message"]


def test_the_lookup_is_registered_for_a_google_identity_and_a_billing_account() -> None:
    from aira_gateway.upstreams.vertex import build_vertex_upstreams

    keyed = build_vertex_upstreams(
        GatewaySettings(vertex_project="p", vertex_api_key="k", vertex_billing_account=ACCOUNT)
    )
    identified = build_vertex_upstreams(
        GatewaySettings(
            vertex_project="p", vertex_metadata_server=True, vertex_billing_account=ACCOUNT
        )
    )

    assert all(upstream.billing is None for upstream in keyed), "an API key cannot read one"
    assert all(upstream.billing is not None for upstream in identified)
    assert identified[0].billing.account == ACCOUNT
