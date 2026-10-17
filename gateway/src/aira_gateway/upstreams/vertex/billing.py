"""What a model costs **this installation**, read from its Google billing account (`FRD-509`).

A list price is what Google publishes; the bill is what the billing account's contract says, and
Claude on a regional endpoint, a committed-use discount or a negotiated rate all make the two
differ. Google's Cloud Billing **Pricing API** answers per billing account — each SKU's list price
and, where a contract applies, its contract price, in the currency asked for:

    GET /v1beta/billingAccounts/{account}/services            which services the account sees
    GET /v1beta/billingAccounts/{account}/skus?filter=…        a service's SKUs (filter by service
                                                               is the only filter there is)
    GET /v1beta/billingAccounts/{account}/skus/{sku}/price     one SKU's price

A SKU is named in prose ("Gemini 2.5 Flash Input Text Tokens"), not by model id, so this module
matches: the SKU's name, up to the first word that says *what is billed*, must be the model's own
name, word for word — `gemini 2.5 flash` is not `gemini 2.5 flash lite`. What is billed decides
the field (input, output, cached input, cache write); SKUs for batch, long context, audio, images,
video, storage, tuning and grounding are not a per-token text price and are left out. The match is
**shown**, not only used: every price carries the SKU it came from, so whoever saves it can see
which line of the bill it is.

The identity is the one the gateway already presents to Google, which needs `roles/billing.viewer`
on the billing account. An API key cannot read a billing account.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx

from aira_common.tokens import TokenSource, TokenUnavailable
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.diagnosis import from_response, from_transport
from aira_gateway.upstreams.diagnosis import of as diagnosis_of

BILLING_HOST = "https://cloudbilling.googleapis.com/v1beta"

#: The largest page the API serves; a service's SKUs fit in one or two.
PAGE_SIZE = 5000

#: How long one account's services and SKUs are held. They change when Google launches a model;
#: the prices themselves are asked for each time.
SKU_CACHE_SECONDS = 6 * 3600

#: The services a model's SKUs can live under: Vertex AI itself, and the partner models Model
#: Garden bills under services of their own.
SERVICE_WORDS = ("vertex ai", "claude", "anthropic", "model garden", "gemini", "glm", "deepseek")

#: The words that end a SKU's model name and say what is billed. The first one found is where the
#: model's name stops.
KIND_WORDS = (
    "input",
    "output",
    "cached",
    "cache",
    "caching",
    "prompt",
    "context",
    "predictions",
    "tokens",
    "text",
)

#: A SKU naming any of these is not the per-token text price of a request.
NOT_A_TOKEN_PRICE = (
    "batch",
    "long",
    ">",
    "audio",
    "image",
    "video",
    "storage",
    "tuning",
    "tuned",
    "grounding",
    "search",
    "provisioned",
    "throughput",
    "priority",
    "flex",
)

#: Prefixes a SKU name may carry before the model's own name.
PREFIXES = ("vertex ai", "generative ai", "model garden", "anthropic")

PER_MILLION = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class Sku:
    sku_id: str
    display_name: str
    regions: tuple[str, ...]
    is_global: bool


@dataclass(frozen=True, slots=True)
class FoundPrice:
    """One field of a model's price, and the line of the bill it is."""

    per_million: str
    currency: str
    sku_id: str
    sku_name: str
    region: str
    #: Whether a contract price applied, rather than the list price.
    contract: bool

    def as_payload(self) -> dict[str, Any]:
        return {
            "perMillion": self.per_million,
            "currency": self.currency,
            "skuId": self.sku_id,
            "skuName": self.sku_name,
            "region": self.region,
            "contract": self.contract,
        }


@dataclass(slots=True)
class _Held:
    skus: list[Sku]
    at: float


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)*|>", text.lower())


def model_phrase(model: str) -> list[str]:
    """The model's own name as a SKU spells it: `claude-sonnet-4-5@20250929` is `claude sonnet 4.5`,
    `gemini-2.5-flash` is `gemini 2.5 flash`."""
    name = model.split("@", 1)[0].split("/")[-1].lower()
    # A version written with hyphens (`4-5`) is written with a dot on the bill (`4.5`).
    name = re.sub(r"(?<=\d)-(?=\d)", ".", name)
    # `-maas` says how Model Garden serves an open model, not which model it is.
    return [word for word in _words(name.replace("-", " ")) if word != "maas"]


def sku_phrase(display_name: str) -> list[str]:
    """The SKU's name up to the first word that says what is billed, without a known prefix."""
    text = display_name.lower()
    for prefix in PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].lstrip(" :-")
    words = _words(text)
    for index, word in enumerate(words):
        if word in KIND_WORDS:
            return words[:index]
    return words


def kind_of(display_name: str) -> str | None:
    """Which price field a SKU is, or `None` when it is not a per-token text price."""
    words = set(_words(display_name))
    lowered = display_name.lower()
    if any(word in words or word in lowered for word in NOT_A_TOKEN_PRICE if word != ">"):
        return None
    if ">" in lowered:
        return None
    if words & {"cache", "caching", "cached"} and words & {"write", "writes", "creation"}:
        return "cache_write"
    if words & {"cached", "caching", "cache"}:
        return "cached_input"
    if "output" in words:
        return "output"
    if "input" in words:
        return "input"
    return None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except InvalidOperation, ValueError:
        return None


def _money(money: Any) -> Decimal | None:
    """`google.type.Money` as a number: `units` whole units and `nanos` billionths."""
    if not isinstance(money, dict):
        return None
    units = _decimal(money.get("units", 0) or 0)
    nanos = _decimal(money.get("nanos", 0) or 0)
    if units is None or nanos is None:
        return None
    return units + nanos / Decimal(1_000_000_000)


def per_million(price: dict[str, Any]) -> tuple[Decimal, bool] | None:
    """A SKU's base-tier price per million tokens, and whether it is a contract price.

    ``None`` where the unit is not tokens — an older model billed per thousand characters is not a
    token price, and converting it would be a guess.
    """
    rate = price.get("rate") if isinstance(price, dict) else None
    if not isinstance(rate, dict):
        return None
    unit = rate.get("unitInfo") or {}
    described = f"{unit.get('unit', '')} {unit.get('unitDescription', '')}".lower()
    if "token" not in described:
        return None
    quantity = _decimal((unit.get("unitQuantity") or {}).get("value", 1)) or Decimal(1)
    if quantity <= 0:
        return None
    tiers = [tier for tier in rate.get("tiers") or [] if isinstance(tier, dict)]
    base = next(
        (
            tier
            for tier in tiers
            if (_decimal((tier.get("startAmount") or {}).get("value", 0)) or 0) == 0
        ),
        None,
    )
    if base is None:
        return None
    contract = _money(base.get("contractPrice"))
    amount = contract if contract is not None else _money(base.get("listPrice"))
    if amount is None or amount < 0:
        return None
    return (amount / quantity * PER_MILLION, contract is not None)


def _sku(raw: dict[str, Any]) -> Sku | None:
    sku_id = raw.get("skuId")
    name = raw.get("displayName")
    if not isinstance(sku_id, str) or not isinstance(name, str):
        return None
    geo = raw.get("geoTaxonomy") or {}
    regions: list[str] = []
    regional = (geo.get("regionalMetadata") or {}).get("region") or {}
    if regional.get("region"):
        regions.append(str(regional["region"]))
    for entry in (geo.get("multiRegionalMetadata") or {}).get("regions") or []:
        if isinstance(entry, dict) and entry.get("region"):
            regions.append(str(entry["region"]))
    return Sku(sku_id, name, tuple(regions), geo.get("type") == "TYPE_GLOBAL" or not regions)


def _choose(candidates: list[Sku], region: str) -> Sku | None:
    """The SKU for this region: its own first, then a multi-region holding it, then global."""
    if not candidates:
        return None
    exact = [sku for sku in candidates if sku.regions == (region,)]
    holding = [sku for sku in candidates if region and region in sku.regions]
    worldwide = [sku for sku in candidates if sku.is_global]
    for group in (exact, holding, worldwide):
        if group:
            return sorted(group, key=lambda sku: sku.sku_id)[0]
    return None


def _region_of(sku: Sku) -> str:
    if sku.is_global:
        return "global"
    return sku.regions[0] if len(sku.regions) == 1 else "+".join(sku.regions)


@dataclass
class BillingPrices:
    """Asks one billing account what a model costs it."""

    account: str
    tokens: TokenSource
    client: httpx.AsyncClient
    currency: str
    clock: Any = time.monotonic
    _held: _Held | None = field(default=None, init=False)

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            token = await self.tokens.token()
        except TokenUnavailable as exc:
            found = diagnosis_of(exc)
            said = f" Reason: {found.reason}." if found else ""
            raise UpstreamError(
                f"No Google token to read the billing account with.{said}", 503, found
            ) from exc
        try:
            response = await self.client.get(
                url, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            found = from_transport(exc)
            said = f" Reason: {found.reason}." if found else ""
            raise UpstreamError(
                f"The billing account could not be reached ({type(exc).__name__}).{said}",
                diagnosis=found,
            ) from exc
        if response.status_code != httpx.codes.OK:
            found = from_response(response)
            said = f" Reason: {found.reason}." if found else ""
            raise UpstreamError(
                f"The billing account answered {response.status_code}.{said}",
                response.status_code,
                found,
            )
        try:
            data = response.json()
        except ValueError:
            raise UpstreamError("The billing account answered with no JSON.", 502) from None
        return data if isinstance(data, dict) else {}

    async def _pages(self, url: str, key: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        token = ""
        while True:
            data = await self._get(url, {**params, "pageSize": PAGE_SIZE, "pageToken": token})
            found.extend(item for item in data.get(key) or [] if isinstance(item, dict))
            token = str(data.get("nextPageToken") or "")
            if not token:
                return found

    async def _skus(self) -> list[Sku]:
        now = self.clock()
        if self._held is not None and now - self._held.at < SKU_CACHE_SECONDS:
            return self._held.skus
        base = f"{BILLING_HOST}/billingAccounts/{quote(self.account, safe='-')}"
        services = await self._pages(f"{base}/services", "billingAccountServices", {})
        relevant = [
            str(service["name"])
            for service in services
            if isinstance(service.get("name"), str)
            and any(word in str(service.get("displayName", "")).lower() for word in SERVICE_WORDS)
        ]
        skus: list[Sku] = []
        for service in relevant:
            for raw in await self._pages(
                f"{base}/skus",
                "billingAccountSkus",
                {"filter": f'billingAccountService = "{service}"'},
            ):
                sku = _sku(raw)
                if sku is not None:
                    skus.append(sku)
        self._held = _Held(skus, now)
        return skus

    async def prices(self, model: str, region: str) -> dict[str, Any]:
        """Each price field this model has on the account, and the fields it has none for."""
        phrase = model_phrase(model)
        candidates: dict[str, list[Sku]] = {}
        for listed in await self._skus():
            if sku_phrase(listed.display_name) != phrase:
                continue
            kind = kind_of(listed.display_name)
            if kind is not None:
                candidates.setdefault(kind, []).append(listed)

        found: dict[str, Any] = {}
        unpriced: dict[str, str] = {}
        base = f"{BILLING_HOST}/billingAccounts/{quote(self.account, safe='-')}"
        for kind in ("input", "output", "cached_input", "cache_write"):
            sku = _choose(candidates.get(kind, []), region)
            if sku is None:
                unpriced[kind] = "no SKU on the account names this model and this price"
                continue
            price = await self._get(
                f"{base}/skus/{quote(sku.sku_id, safe='-')}/price", {"currencyCode": self.currency}
            )
            value = per_million(price)
            if value is None:
                unpriced[kind] = f"{sku.display_name} is not priced per token"
                continue
            amount, contract = value
            found[kind] = FoundPrice(
                per_million=format(amount.quantize(Decimal("0.000001")).normalize(), "f"),
                currency=str(price.get("currencyCode") or self.currency),
                sku_id=sku.sku_id,
                sku_name=sku.display_name,
                region=_region_of(sku),
                contract=contract,
            ).as_payload()
        return {
            "model": model,
            "region": region,
            "billingAccount": self.account,
            "currency": self.currency,
            "prices": found,
            "unpriced": unpriced,
        }
