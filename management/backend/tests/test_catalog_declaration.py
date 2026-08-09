"""The catalog must not be able to hold a declaration that cannot work (FRD-114 FR-3, FR-4).

The catalog is a **runtime authority**: what it says decides whether a request is accepted. So a
self-contradictory declaration has to be refused where it is written. Discovering it where it is
enforced means a vendor error message for every request against that model, and nobody looking at
the catalog would see anything wrong.
"""

from __future__ import annotations

from typing import Any

import pytest
from aira_management.apps.catalog.models import Model
from aira_management.apps.catalog.validation import validate_declaration

from .test_catalog import BASE, _client, _user

pytestmark = pytest.mark.django_db


def _declaration(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "capabilities": ["generate"],
        "hosting": "managed",
        "thinking": None,
        "embedding": None,
        "attachments": None,
        "max_output_tokens": None,
        "default_max_output_tokens": None,
    }
    base.update(over)
    return base


# -- the rule with teeth ------------------------------------------------------------------


def test_a_thinking_budget_at_or_above_the_output_cap_is_refused() -> None:
    """Anthropic draws thinking tokens from ``max_tokens`` (`FRD-119` §5.4), so this declaration
    describes a model that could never answer — and the catalog is where that has to be caught."""
    errors = validate_declaration(
        _declaration(
            max_output_tokens=8192,
            thinking={"modes": ["limited"], "max_tokens": 8192},
        )
    )
    assert any("below max_output_tokens" in error for error in errors)


def test_a_thinking_budget_below_the_output_cap_is_accepted() -> None:
    assert (
        validate_declaration(
            _declaration(
                max_output_tokens=8192,
                thinking={"modes": ["limited"], "min_tokens": 128, "max_tokens": 4096},
            )
        )
        == []
    )


# -- internal consistency ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("block", "fragment"),
    [
        ({"modes": []}, "non-empty list"),
        ({"modes": ["telepathy"]}, "Unknown thinking modes"),
        ({"modes": ["limited"]}, "required when 'limited' is offered"),
        ({"modes": ["auto"], "min_tokens": 4096, "max_tokens": 128}, "must not exceed"),
        ({"modes": ["auto"], "default": {"mode": "high"}}, "one of the declared modes"),
        ({"modes": ["auto"], "levels": {"telepathy": 10}}, "unknown mode"),
        ({"modes": ["auto"], "levels": {"auto": 0}}, "positive integer"),
    ],
)
def test_an_inconsistent_thinking_block_is_refused(block: dict[str, Any], fragment: str) -> None:
    errors = validate_declaration(_declaration(thinking=block))
    assert any(fragment in error for error in errors), errors


@pytest.mark.parametrize(
    ("block", "fragment"),
    [
        ({"task_types": [""]}, "non-empty strings"),
        ({"dimensions": []}, "non-empty list"),
        ({"dimensions": [768, -1]}, "positive integer"),
        ({"dimensions": [768], "default": 3072}, "one of the declared dimensions"),
    ],
)
def test_an_inconsistent_embedding_block_is_refused(block: dict[str, Any], fragment: str) -> None:
    errors = validate_declaration(_declaration(embedding=block))
    assert any(fragment in error for error in errors), errors


def test_an_attachment_declaration_needs_media_types_and_positive_estimates() -> None:
    assert any(
        "not a media type" in error
        for error in validate_declaration(_declaration(attachments={"media_types": {"pdf": {}}}))
    )
    assert any(
        "positive integer" in error
        for error in validate_declaration(
            _declaration(attachments={"media_types": {"application/pdf": {"tokens": 0}}})
        )
    )


def test_a_valid_attachment_declaration_is_accepted() -> None:
    assert (
        validate_declaration(
            _declaration(
                capabilities=["generate", "attachments"],
                attachments={"media_types": {"application/pdf": {"tokens": 2000}}},
            )
        )
        == []
    )


def test_an_unknown_capability_is_refused() -> None:
    errors = validate_declaration(_declaration(capabilities=["generate", "telepathy"]))
    assert any("Unknown capabilities" in error for error in errors)


def test_an_unknown_hosting_value_is_refused() -> None:
    assert validate_declaration(_declaration(hosting="somebody-elses-computer"))


def test_the_default_output_cap_may_not_exceed_the_maximum() -> None:
    errors = validate_declaration(
        _declaration(max_output_tokens=1024, default_max_output_tokens=4096)
    )
    assert any("must not exceed max_output_tokens" in error for error in errors)


# -- through the API ------------------------------------------------------------------------


def test_a_global_admin_declares_a_model_and_it_is_published(monkeypatch: Any) -> None:
    published: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "aira_management.apps.catalog.views.emit",
        lambda event, payload: published.append((event, payload)),
    )

    response = _client(_user("admin", "global-admin")).post(
        BASE,
        {
            "name": "claude-sonnet-4-5@20250929",
            "capabilities": ["generate", "thinking", "structured_output", "attachments"],
            "publisher": "anthropic",
            "platform": "vertex",
            "addressing": {"publisher_path": "publishers/anthropic"},
            "underlying_model": "claude-sonnet-4-5",
            "max_output_tokens": 64000,
            "default_max_output_tokens": 4096,
            "thinking": {"modes": ["auto", "limited"], "min_tokens": 1024, "max_tokens": 32000},
            "hosting": "managed",
            "deprecated": False,
        },
        format="json",
    )
    assert response.status_code == 201, response.json()

    model = Model.objects.get(name="claude-sonnet-4-5@20250929")
    assert model.is_declared is True
    assert model.publisher == "anthropic"

    # Everything validation needs has to travel with the event: the gateway reads its own
    # read-model on the request path and never calls Management (FR-8).
    event, payload = published[-1]
    assert event == "model.upserted"
    assert payload["capabilities"] == [
        "generate",
        "thinking",
        "structured_output",
        "attachments",
    ]
    assert payload["max_output_tokens"] == 64000
    assert payload["thinking"]["max_tokens"] == 32000
    assert payload["publisher"] == "anthropic"


def test_the_api_refuses_a_contradictory_declaration() -> None:
    response = _client(_user("admin", "global-admin")).post(
        BASE,
        {
            "name": "impossible-1",
            "capabilities": ["generate", "thinking"],
            "max_output_tokens": 4096,
            "thinking": {"modes": ["limited"], "max_tokens": 8192},
        },
        format="json",
    )
    assert response.status_code == 400
    assert Model.objects.filter(name="impossible-1").count() == 0


def test_a_patch_is_validated_against_what_the_row_already_holds() -> None:
    """A PATCH that touches only ``max_output_tokens`` must be checked against the thinking block
    it cannot see — otherwise the two halves are each valid and the row is not."""
    client = _client(_user("admin", "global-admin"))
    client.post(
        BASE,
        {
            "name": "patched-1",
            "capabilities": ["generate", "thinking"],
            "max_output_tokens": 64000,
            "thinking": {"modes": ["limited"], "max_tokens": 32000},
        },
        format="json",
    )

    response = client.patch(f"{BASE}patched-1/", {"max_output_tokens": 1024}, format="json")

    assert response.status_code == 400
    assert Model.objects.get(name="patched-1").max_output_tokens == 64000


def test_a_model_with_only_a_price_is_undeclared_rather_than_invalid() -> None:
    """Every existing installation looks like this. Undeclared is a valid state — the gateway
    reads it as the baseline capabilities and nothing more (FR-7)."""
    response = _client(_user("admin", "global-admin")).post(
        BASE,
        {"name": "legacy-1", "input_price_per_million": "1", "output_price_per_million": "2"},
        format="json",
    )
    assert response.status_code == 201
    assert Model.objects.get(name="legacy-1").is_declared is False


def test_somebody_without_a_global_role_may_not_declare_a_model() -> None:
    """A thinking maximum is a cost ceiling: whoever can raise it can make one request cost as
    much as a month. Same restriction as prices, with more direct leverage (FRD-114 §5.4).

    Named for the caller it now describes: `use-case-admin` was an organisation-wide role and is
    one no longer (`ADR-0017`), so the person this guards against is anybody whose authority is a
    use case rather than the installation.
    """
    response = _client(_user("ucadmin")).post(
        BASE, {"name": "sneaky-1", "capabilities": ["generate"]}, format="json"
    )
    assert response.status_code == 403
    assert Model.objects.filter(name="sneaky-1").count() == 0
