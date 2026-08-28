"""The bypass, against the running stack (`ADR-0015`).

This is the layer that found it. Four verification layers were green — 1481 hermetic tests, 271
mutation properties, an integration suite and a Playwright suite — and one request made it obvious:
a caller belonging to no use case sent `X-AIRA-Use-Case: <somebody else's>` to the KIRA surface,
got a real answer, and had the tokens billed to that use case's budget and written into its audit
trail. The Gemini surface refused the identical request in both of its selector forms.

The property is asserted **from outside**, against both surfaces, and the audit trail is read in
Postgres rather than inferred from the response — because the harm was not the answer, it was the
row.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text

from .conftest import GATEWAY_URL, LOCAL_CHAT_MODEL_ID

pytestmark = pytest.mark.integration

MODEL = "qwen3:0.6b"
#: The KIRA surface addresses models by integer id (`FRD-107`). A real one, because the finding
#: only shows up *past* attribution: the first version of this test used id 1 and every identity
#: came back `MODEL_NOT_FOUND` — which reads exactly like a refusal and is not one.
MODEL_ID = LOCAL_CHAT_MODEL_ID
SHORT = {"generationConfig": {"maxOutputTokens": 8}}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_caller_in_no_use_case_cannot_name_one_on_the_kira_surface(
    governance_token: str, engine
) -> None:
    """The finding itself, and the identity matters.

    `if memberships and header not in memberships` refused a caller who had *some* memberships and
    waved through one who had **none** — so the reproduction needs an identity in no use case at
    all. `it-steuerung` is exactly that: it sees every use case (oversight) and is a member of
    none, which is the whole point of an oversight role. An account that merely administers a use
    case does **not** reproduce it, and the first draft of this test used one and passed against
    the broken code.
    """
    marker = f"selector-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/kira/api/external/chat",
            headers={**_auth(governance_token), "X-AIRA-Use-Case": "demo-uc"},
            json={"model_id": MODEL_ID, "request": {"parts": [{"text": marker}]}},
        )

    assert response.status_code == 403, response.text
    assert "demo-uc" in response.text

    # And no row was written against the use case the caller named. The refusal happens before
    # dispatch, so `FRD-122`'s refusal row carries no use case rather than the victim's.
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT use_case FROM request_logs WHERE request_payload::text LIKE :m"),
            {"m": f"%{marker}%"},
        )
        assert [r[0] for r in rows if r[0] == "demo-uc"] == []


async def test_the_gemini_surface_refuses_the_same_request(governance_token: str) -> None:
    """It always did. Asserted here so a future change cannot make the two disagree again in the
    other direction."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        header_form = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers={**_auth(governance_token), "X-AIRA-Use-Case": "demo-uc"},
            json={"contents": [{"parts": [{"text": "hi"}]}], **SHORT},
        )
        path_form = await client.post(
            f"{GATEWAY_URL}/uc/demo-uc/v1beta/models/{MODEL}:generateContent",
            headers=_auth(governance_token),
            json={"contents": [{"parts": [{"text": "hi"}]}], **SHORT},
        )

    assert header_form.status_code == 403, header_form.text
    assert path_form.status_code == 403, path_form.text


async def test_both_surfaces_refuse_with_their_own_envelope(governance_token: str) -> None:
    """Sharing the rule must not leak one surface's error shape into the other: a KIRA client
    parses `code`, and a Gemini-shaped body would be an outage for them."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        kira = await client.post(
            f"{GATEWAY_URL}/kira/api/external/chat",
            headers={**_auth(governance_token), "X-AIRA-Use-Case": "demo-uc"},
            json={"model_id": MODEL_ID, "request": {"parts": [{"text": "hi"}]}},
        )
        gemini = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers={**_auth(governance_token), "X-AIRA-Use-Case": "demo-uc"},
            json={"contents": [{"parts": [{"text": "hi"}]}], **SHORT},
        )

    assert "code" in kira.json()
    assert "error" in gemini.json() and "status" in gemini.json()["error"]
