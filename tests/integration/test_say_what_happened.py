"""What a request is told, and what its details say, on the live stack (`FRD-628`).

The hermetic suite builds the app in-process; this asks the containers the stack really runs, behind
the same proxy and headers a client meets — that the id a response carries is the row the details
are read by, and that a refusal says which control refused without changing what clients key on.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import GATEWAY_URL, wait_for_row

pytestmark = pytest.mark.integration


async def test_an_answer_names_its_request_and_its_model(fixture, security_token) -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        answer = await client.post(
            f"{GATEWAY_URL}/v1beta/models/mock-1:generateContent",
            headers=fixture.headers(),
            json={"contents": [{"parts": [{"text": "hi"}]}]},
        )
        assert answer.status_code == 200, answer.text
        request_id = answer.headers["x-aira-request-id"]
        assert answer.headers["x-aira-model"] == "mock-1"
        await wait_for_row(
            fixture.engine, "SELECT 1 FROM request_logs WHERE id = :id", {"id": request_id}
        )
        detail = await client.get(
            f"{GATEWAY_URL}/v1beta/traces/{request_id}",
            headers={"Authorization": f"Bearer {security_token}"},
        )

    assert detail.status_code == 200, detail.text
    assert detail.json()["trace"]["id"] == request_id
    assert detail.json()["trace"]["error"] is None


async def test_a_refusal_says_which_control_and_keeps_its_status(fixture, security_token) -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        refused = await client.post(
            f"{GATEWAY_URL}/v1beta/models/no-such-model:generateContent",
            headers=fixture.headers(),
            json={"contents": [{"parts": [{"text": "hi"}]}]},
        )
        request_id = refused.headers["x-aira-request-id"]
        await wait_for_row(
            fixture.engine, "SELECT 1 FROM request_logs WHERE id = :id", {"id": request_id}
        )
        detail = await client.get(
            f"{GATEWAY_URL}/v1beta/traces/{request_id}",
            headers={"Authorization": f"Bearer {security_token}"},
        )

    assert refused.status_code == 404
    error = refused.json()["error"]
    assert error["status"] == "NOT_FOUND"
    (info,) = error["details"]
    assert (info["reason"], info["metadata"]["request_id"]) == ("MODEL_NOT_FOUND", request_id)
    assert refused.headers["x-aira-reason"] == "model_not_found"
    assert detail.json()["trace"]["error"] == error["message"]
