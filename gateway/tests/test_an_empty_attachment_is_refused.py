"""An empty attachment is the caller's mistake, refused naming the part (`FRD-110`).

It used to be forwarded, and Google's refusal came back as the provider's error.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead


async def test_an_empty_attachment_is_refused_on_both_surfaces() -> None:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(
                ModelRead(
                    model="mock-1",
                    numeric_id=1,
                    capabilities=["generate", "attachments"],
                    attachments={"media_types": {"text/plain": None}},
                )
            )
            await session.commit()
        gemini = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"inlineData": {"mimeType": "text/plain", "data": ""}},
                            {"text": "Was steht darin?"},
                        ],
                    }
                ]
            },
        )
        kira = client.post(
            "/kira/api/external/chat",
            json={
                "request": {"parts": [{"mime_type": "text/plain", "data": ""}, {"text": "?"}]},
                "model_id": 1,
            },
        )

    assert gemini.status_code == 400, gemini.text
    assert "empty" in gemini.json()["error"]["message"]
    assert kira.status_code == 400, kira.text
    assert "empty" in kira.json()["message"]
