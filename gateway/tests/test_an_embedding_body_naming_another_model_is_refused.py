"""An embedding body that names another model than the URL is refused, wherever it names it.

The URL chooses the model, and every control before dispatch — release, approval, rate limit,
budget — was applied to that name. A `model` in the body never selects anything; one that disagrees
says the caller believes another model answers, and a `200` would hand them its vector under the
wrong name, with an audit row naming the URL's model (`FRD-124`). Google's batch request carries the
model twice — once for the whole batch and once per entry — and the entries were compared while the
batch's own field was parsed and never looked at.
"""

from __future__ import annotations

from typing import Any

import pytest

URL = "/v1beta/models/mock-1"
TEXT = {"parts": [{"text": "x"}]}


@pytest.mark.parametrize(
    ("verb", "body"),
    [
        ("embedContent", {"model": "models/other-1", "content": TEXT}),
        ("batchEmbedContents", {"requests": [{"model": "models/other-1", "content": TEXT}]}),
        ("batchEmbedContents", {"model": "models/other-1", "requests": [{"content": TEXT}]}),
        ("batchEmbedContents", {"model": "other-1", "requests": [{"content": TEXT}]}),
        (
            "batchEmbedContents",
            {"model": "models/other-1", "requests": [{"model": "models/mock-1", "content": TEXT}]},
        ),
    ],
)
def test_a_body_naming_another_model_is_refused_by_name(
    client: Any, verb: str, body: dict[str, Any]
) -> None:
    response = client.post(f"{URL}:{verb}", json=body)

    assert response.status_code == 400, (response.status_code, response.text[:200])
    message = response.json()["error"]["message"]
    assert "other-1" in message and "'mock-1'" in message


@pytest.mark.parametrize(
    "body",
    [
        {"requests": [{"content": TEXT}]},
        {"model": "models/mock-1", "requests": [{"content": TEXT}]},
        {"model": "mock-1", "requests": [{"model": "models/mock-1", "content": TEXT}]},
    ],
)
def test_a_body_naming_the_addressed_model_or_none_is_served(
    client: Any, body: dict[str, Any]
) -> None:
    response = client.post(f"{URL}:batchEmbedContents", json=body)

    assert response.status_code == 200, (response.status_code, response.text[:200])
    assert len(response.json()["embeddings"]) == 1
