"""The model reference: drafts of catalog entries from a public list (`FRD-508`).

What these hold: every draft the shipped copy carries is a declaration the catalog accepts; the
list's fields land in this catalog's vocabulary, with nothing it cannot serve; the reference writes
and approves nothing and is offered only to whoever writes the catalogue; and the shipped copy is
the answer unless a live list is configured — and still the answer, saying why, when that list
cannot be had.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from aira_management.apps.catalog import reference
from aira_management.apps.catalog.models import Model
from aira_management.apps.catalog.reference import normalize, snapshot
from aira_management.apps.catalog.validation import validate_declaration
from aira_management.config.runtime import get_settings
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .conftest import role_claims

URL = "/api/v1/model-reference/"


def _listing(**models: dict[str, Any]) -> dict[str, Any]:
    return {"google-vertex": {"models": models}}


def _entry(**over: Any) -> dict[str, Any]:
    return {
        "name": "Gemini X",
        "attachment": True,
        "reasoning": True,
        "reasoning_options": [{"type": "toggle"}, {"type": "budget_tokens", "min": 0, "max": 8192}],
        "tool_call": True,
        "structured_output": True,
        "modalities": {"input": ["text", "image", "pdf", "video"], "output": ["text"]},
        "limit": {"context": 1048576, "output": 65536},
        "cost": {"input": 0.3, "output": 2.5, "cache_read": 0.03},
        **over,
    }


# ---- FR-1: drafts in this catalog's vocabulary ---------------------------------------------------


def test_every_shipped_draft_is_a_declaration_the_catalog_accepts() -> None:
    """A draft the editor would refuse on save is research the administrator must redo."""
    shipped = snapshot.shipped()

    assert len(shipped.entries) > 20
    refused = {
        f"{entry['platform']}/{entry['name']}": problems
        for entry in shipped.entries
        if (problems := validate_declaration(entry["declaration"]))
    }
    assert refused == {}


def test_the_shipped_copy_says_where_and_when_it_comes_from() -> None:
    shipped = snapshot.shipped()

    assert shipped.source == "https://models.dev/api.json"
    assert len(shipped.as_of) == 10 and shipped.as_of[4] == "-"


def test_a_list_entry_becomes_the_editors_fields() -> None:
    (made,) = normalize.drafts(_listing(**{"gemini-x": _entry()}))

    assert made["platform"] == "vertex"
    assert (made["publisher"], made["name"], made["display_name"]) == (
        "google",
        "gemini-x",
        "Gemini X",
    )
    assert made["declaration"] == {
        "capabilities": [
            "generate",
            "tools",
            "structured_output",
            "attachments",
            "thinking",
            "prompt_caching",
        ],
        "context_window": 1048576,
        "max_output_tokens": 65536,
        "thinking": {"modes": ["disabled", "auto", "limited"], "max_tokens": 8192},
        # Video has no media type the gateway carries; it is left out, not invented.
        "attachments": {
            "media_types": {
                "image/png": None,
                "image/jpeg": None,
                "image/webp": None,
                "application/pdf": None,
            }
        },
    }
    assert made["prices"] == {
        "input": "0.3",
        "output": "2.5",
        "cached_input": "0.03",
        "cache_write": None,
    }


def test_effort_words_are_levels_and_none_is_disabled() -> None:
    entry = _entry(reasoning_options=[{"type": "effort", "values": ["none", "low", "high"]}])

    (made,) = normalize.drafts(_listing(m=entry))

    assert made["declaration"]["thinking"] == {"modes": ["disabled"], "levels": ["low", "high"]}


def test_a_budget_without_a_ceiling_takes_the_output_cap_and_says_so() -> None:
    entry = _entry(reasoning_options=[{"type": "budget_tokens", "min": 1024}])

    (made,) = normalize.drafts(_listing(m=entry))

    assert made["declaration"]["thinking"] == {
        "modes": ["limited"],
        "min_tokens": 1024,
        "max_tokens": 65535,
    }
    assert any("output cap" in note for note in made["notes"])


def test_a_floor_above_zero_drops_disabled() -> None:
    entry = _entry(
        reasoning_options=[{"type": "toggle"}, {"type": "budget_tokens", "min": 128, "max": 4096}]
    )

    (made,) = normalize.drafts(_listing(m=entry))

    assert "disabled" not in made["declaration"]["thinking"]["modes"]


def test_an_embedding_model_embeds_and_has_no_output_price() -> None:
    entry = _entry(limit={"context": 2048, "output": 1}, cost={"input": 0.15, "output": 0})

    (made,) = normalize.drafts(_listing(**{"gemini-embedding-001": entry}))

    assert made["declaration"]["capabilities"] == ["embed", "attachments"]
    assert made["declaration"]["max_output_tokens"] is None
    assert made["prices"]["output"] is None


def test_a_speech_model_speaks_and_its_audio_is_not_held_to_a_text_window() -> None:
    entry = _entry(
        modalities={"input": ["text"], "output": ["audio"]},
        limit={"context": 8192, "output": 16384},
        reasoning=False,
    )

    (made,) = normalize.drafts(_listing(m=entry))

    assert made["declaration"]["capabilities"][0] == "speech"
    assert made["declaration"]["max_output_tokens"] is None


def test_only_what_the_gateway_can_serve_is_offered() -> None:
    listing = {
        "google-vertex": {
            "models": {
                "gemini-x": _entry(),
                "zai-org/glm-5-maas": _entry(),
                "mistralai/codestral": _entry(),
                "claude-x@default": _entry(),
                "imagen-x": _entry(modalities={"input": ["text"], "output": ["image"]}),
            }
        },
        "google-vertex-anthropic": {"models": {"claude-x@default": _entry()}},
        "google": {"models": {"gemini-x": _entry()}},
        "openrouter": {"models": {"anything": _entry()}},
    }

    made = {(e["platform"], e["publisher"], e["name"]) for e in normalize.drafts(listing)}

    assert made == {
        ("vertex", "google", "gemini-x"),
        ("vertex", "zai-org", "glm-5-maas"),
        ("vertex", "anthropic", "claude-x"),
        ("generative-language", "google", "gemini-x"),
    }


def test_a_list_that_is_not_one_yields_nothing() -> None:
    assert normalize.drafts([]) == []
    assert normalize.drafts({"google-vertex": {"models": ["x"]}}) == []
    assert normalize.drafts({"google-vertex": {"models": {"x": "not an entry"}}}) == []


def test_a_price_that_is_not_an_amount_is_left_empty() -> None:
    entry = _entry(cost={"input": "lots", "output": -1, "cache_read": True})

    (made,) = normalize.drafts(_listing(m=entry))

    assert made["prices"] == {
        "input": None,
        "output": None,
        "cached_input": None,
        "cache_write": None,
    }


# ---- FR-2: shipped by default, live where configured ---------------------------------------------


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    reference.forget()
    get_settings.cache_clear()
    yield
    reference.forget()
    get_settings.cache_clear()


def _client_answering(*responses: httpx.Response) -> tuple[httpx.Client, list[httpx.Request]]:
    asked: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return httpx.Client(transport=httpx.MockTransport(handler)), asked


def test_without_a_url_nothing_is_asked() -> None:
    client, asked = _client_answering(httpx.Response(500))

    found = reference.current("", client=client)

    assert asked == []
    assert found.source == snapshot.shipped().source
    assert found.live_error == "", "nothing configured is not something that failed"


def test_a_live_list_is_asked_once_and_held() -> None:
    live = _listing(**{"gemini-live": _entry()})
    client, asked = _client_answering(httpx.Response(200, json=live))

    first = reference.current("https://mirror.example/api.json", client=client)
    second = reference.current("https://mirror.example/api.json", client=client)

    assert [entry["name"] for entry in first.entries] == ["gemini-live"]
    assert first.source == "https://mirror.example/api.json"
    assert second is first and len(asked) == 1


def test_an_unreachable_list_answers_with_the_shipped_copy_and_says_why() -> None:
    client, asked = _client_answering(httpx.Response(503))

    first = reference.current("https://mirror.example/api.json", client=client)
    again = reference.current("https://mirror.example/api.json", client=client)

    assert first.entries == snapshot.shipped().entries
    assert "HTTPStatusError" in first.live_error
    assert again.live_error and len(asked) == 1, "a failure is not repeated on every visit"


def test_a_live_answer_with_nothing_servable_is_not_used() -> None:
    client, _ = _client_answering(httpx.Response(200, json={"openrouter": {}}))

    found = reference.current("https://mirror.example/api.json", client=client)

    assert found.entries == snapshot.shipped().entries
    assert "ValueError" in found.live_error


# ---- FR-3: for whoever writes the catalogue, and it writes nothing -------------------------------


def _user(username: str, *roles: str) -> Any:
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    return user


def _client(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_the_reference_is_offered_to_whoever_writes_the_catalogue() -> None:
    response = _client(_user("admin", "global-admin")).get(URL)

    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "USD"
    assert body["source"] == "https://models.dev/api.json"
    assert body["live_error"] is None
    assert body["entries"] == json.loads(snapshot.SNAPSHOT_PATH.read_text())["entries"]
    assert Model.objects.count() == 0, "a reference writes nothing"


@pytest.mark.django_db
def test_anybody_else_is_refused() -> None:
    assert _client(_user("reader")).get(URL).status_code == 403
    assert APIClient().get(URL).status_code in (401, 403)


@pytest.mark.django_db
def test_the_route_is_not_a_model_called_reference() -> None:
    admin = _client(_user("admin", "global-admin"))

    assert admin.get("/api/v1/models/reference/").status_code == 404
    assert "entries" in admin.get(URL).json()


@pytest.mark.parametrize(
    ("options", "max_output", "thinking"),
    [
        ([], 100, None),
        (
            ["not an option", {"type": "effort", "values": ["low", "low", 3]}],
            100,
            {
                "modes": [],
                "levels": ["low"],
            },
        ),
        ([{"type": "budget_tokens", "max": 500}], 100, {"modes": ["limited"], "max_tokens": 99}),
        ([{"type": "budget_tokens"}], None, None),
        ([{"type": "something new"}], 100, None),
    ],
)
def test_reasoning_options_the_list_may_hold(
    options: list[Any], max_output: int | None, thinking: dict[str, Any] | None
) -> None:
    assert normalize._thinking(options, max_output, []) == thinking


def test_tiered_and_audio_prices_are_noted() -> None:
    entry = _entry(cost={"input": 1, "output": 2, "tiers": [{}], "input_audio": 3})

    (made,) = normalize.drafts(_listing(m=entry))

    assert len(made["notes"]) == 2


def test_an_oversized_live_answer_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snapshot, "MAX_LIVE_BYTES", 10)
    client, _ = _client_answering(httpx.Response(200, json=_listing(m=_entry())))

    found = reference.current("https://mirror.example/api.json", client=client)

    assert "ValueError" in found.live_error


def test_without_a_client_one_is_made_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[httpx.Client] = []
    real = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        made.append(
            real(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=_listing(m=_entry()))
                )
            )
        )
        return made[-1]

    monkeypatch.setattr(snapshot.httpx, "Client", client)

    found = reference.current("https://mirror.example/api.json")

    assert [entry["name"] for entry in found.entries] == ["m"]
    assert made[0].is_closed


# ---- FRD-509: the list price in the installation's currency, where a rate is set ----------------


@pytest.mark.django_db
def test_a_set_rate_is_offered_for_converting_the_list_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIRA_MODEL_REFERENCE_EXCHANGE_RATE", "0.92")
    monkeypatch.setenv("AIRA_CURRENCY", "EUR")
    get_settings.cache_clear()

    body = _client(_user("admin", "global-admin")).get(URL).json()

    assert body["conversion"] == {"from": "USD", "to": "EUR", "rate": "0.92"}


@pytest.mark.django_db
def test_without_a_rate_or_in_the_lists_own_currency_nothing_is_converted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _client(_user("admin", "global-admin"))
    monkeypatch.setenv("AIRA_CURRENCY", "EUR")
    get_settings.cache_clear()
    assert admin.get(URL).json()["conversion"] is None

    monkeypatch.setenv("AIRA_MODEL_REFERENCE_EXCHANGE_RATE", "0.92")
    monkeypatch.setenv("AIRA_CURRENCY", "USD")
    get_settings.cache_clear()
    assert admin.get(URL).json()["conversion"] is None


@pytest.mark.parametrize("rate", ["lots", "0", "-1", "NaN", "Infinity"])
def test_a_rate_that_is_not_a_positive_number_stops_the_start(
    monkeypatch: pytest.MonkeyPatch, rate: str
) -> None:
    from aira_management.config.app_settings import ManagementSettings

    monkeypatch.setenv("AIRA_MODEL_REFERENCE_EXCHANGE_RATE", rate)

    with pytest.raises(ValueError, match="AIRA_MODEL_REFERENCE_EXCHANGE_RATE"):
        ManagementSettings()
