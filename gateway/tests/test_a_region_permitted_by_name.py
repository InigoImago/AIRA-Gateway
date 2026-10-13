"""A region permitted for named models alone (`FRD-611`, `AIRA_ALLOWED_MODEL_REGIONS`).

`global` names no region and guarantees none, so it is in no default. Agent Platform's open models —
GLM, DeepSeek — answer there and nowhere else (`FRD-115` FR-2a). An installation that decides to use
them should be able to say so **about them**: widening the list would permit `global` for every
model a catalogue entry could later place there, and the list would stop meaning what it says.

The rule has one owner, `residency.check_region`, and every place that measures a model against the
policy asks it with the model's name: the startup check, the transport, the dispatch condition.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.pipeline.dispatch import NoCapableModel, dispatch_with_fallback
from aira_gateway.requirements import RegionAllowed, permits
from aira_gateway.residency import RegionNotAllowed, check_region, parse_model_regions
from aira_gateway.upstreams.base import ProviderRegistry
from aira_gateway.upstreams.mock import MockProvider

EU = ("eu", "europe-west1")


def test_the_setting_reads_regions_and_the_models_they_are_permitted_for() -> None:
    assert parse_model_regions("") == {}
    assert parse_model_regions(" global = glm-4.7-maas , deepseek-v3.2-maas ; us-central1=m ") == {
        "glm-4.7-maas": frozenset({"global"}),
        "deepseek-v3.2-maas": frozenset({"global"}),
        "m": frozenset({"us-central1"}),
    }
    # One model in two entries is permitted both.
    assert parse_model_regions("global=m;us-central1=m") == {
        "m": frozenset({"global", "us-central1"})
    }


@pytest.mark.parametrize("entry", ["global", "=glm-4.7-maas", "global=", "global= , "])
def test_an_entry_that_names_no_region_or_no_model_is_refused_by_name(entry: str) -> None:
    with pytest.raises(ValueError, match="does not name a region and its models"):
        parse_model_regions(entry)


def test_a_malformed_setting_stops_the_gateway_rather_than_refusing_the_model() -> None:
    """Read per request, a typo here would refuse the very model somebody meant to permit, with a
    residency message that sends them to look for a policy they believe they set."""
    with pytest.raises(ValidationError, match="AIRA_ALLOWED_MODEL_REGIONS"):
        GatewaySettings(allowed_model_regions="global")


def test_the_permission_is_for_the_named_model_and_the_named_region() -> None:
    permitted = parse_model_regions("global=glm-4.7-maas")

    check_region("global", EU, model="glm-4.7-maas", for_models=permitted)
    check_region("eu", EU, model="glm-4.7-maas", for_models=permitted)
    with pytest.raises(RegionNotAllowed, match="'gemini-3.5-flash' by name"):
        check_region("global", EU, model="gemini-3.5-flash", for_models=permitted)
    with pytest.raises(RegionNotAllowed):
        check_region("us-central1", EU, model="glm-4.7-maas", for_models=permitted)


def test_a_whole_endpoint_is_never_permitted_by_a_models_name() -> None:
    """An OpenAI server, a Foundry resource and AI Studio are checked as **places** that serve many
    models — without a model name — so a permission naming one model cannot open one of them."""
    permitted = parse_model_regions("global=glm-4.7-maas")

    with pytest.raises(RegionNotAllowed):
        check_region("global", EU, for_models=permitted)


def _request(model: str) -> CanonicalRequest:
    return CanonicalRequest(model=model, messages=[CanonicalMessage(role=Role.USER, text="hi")])


class _AtGlobal(MockProvider):
    """A configured model at `global`, as `AIRA_VERTEX_MODELS` declares one."""

    def models(self) -> list:  # type: ignore[override]
        return [
            model.__class__(
                model.name, model.version, model.supported_methods, "vertex", "zai-org", "global"
            )
            for model in super().models()
        ]


async def test_the_dispatch_condition_asks_the_same_rule_with_the_models_name() -> None:
    """The chain's per-hop check is the policy's own rule, not a second copy — a copy is how a
    permission by name would work at startup and be refused at request time."""
    registry = ProviderRegistry([_AtGlobal("glm-4.7-maas")])
    permitted = parse_model_regions("global=glm-4.7-maas")

    dispatched = await dispatch_with_fallback(
        registry,
        _request("glm-4.7-maas"),
        (),
        permits=permits([RegionAllowed(registry, EU, permitted)]),
    )
    assert dispatched.response.model == "glm-4.7-maas"

    with pytest.raises(NoCapableModel, match="'global'"):
        await dispatch_with_fallback(
            registry,
            _request("glm-4.7-maas"),
            (),
            permits=permits([RegionAllowed(registry, EU)]),
        )
