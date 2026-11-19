"""One catalogue entry's malformed thinking default does not take the KIRA model list down.

The list is built in one pass over every served model, so one entry that cannot be rendered was a
`500` for every client that reads it — and a KIRA client reads it to decide what to ask for at all.
The request path already reads a declared default defensively: a `tokens` that is not a whole
number is sent as none. The list read the same block without that guard, and Management stores
such a default where the mode takes no figure (`{"mode": "auto", "tokens": 1.5}`).

The listing now says what a request that names no thinking is sent — the one rule, read once.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.api.kira import BASE
from aira_gateway.app import create_app
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead
from aira_gateway.thinking import resolve
from aira_gateway.upstreams.base import ProviderRegistry
from aira_gateway.upstreams.mock import MockProvider

WELL_FORMED = {"modes": ["auto", "disabled"], "default": {"mode": "disabled"}}


@pytest.mark.parametrize("tokens", [1.5, "none", True, [1]])
def test_the_list_stands_and_lists_the_default_a_request_is_sent(tokens: Any) -> None:
    malformed = {"modes": ["auto", "disabled"], "default": {"mode": "auto", "tokens": tokens}}
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([MockProvider("mock-1", "mock-2")])
    with TestClient(app, raise_server_exceptions=False) as client:

        async def seed() -> None:
            async with app.state.db_sessionmaker() as session:
                for model, numeric_id, thinking in (
                    ("mock-1", 1001, malformed),
                    ("mock-2", 1002, WELL_FORMED),
                ):
                    session.add(
                        ModelRead(
                            model=model,
                            numeric_id=numeric_id,
                            capabilities=["generate", "thinking"],
                            thinking=thinking,
                        )
                    )
                await session.commit()

        client.portal.call(seed)  # type: ignore[attr-defined]
        response = client.get(f"{BASE}/models")

    assert response.status_code == 200, (response.status_code, response.text[:200])
    listed = {
        entry["name"]: entry["thinkingConfig"]["defaultThinking"] for entry in response.json()
    }
    assert listed == {
        "mock-1": {"mode": "auto", "tokens": None},
        "mock-2": {"mode": "disabled", "tokens": None},
    }
    sent = resolve(None, ModelDeclaration(name="mock-1", thinking=malformed))
    assert sent is not None and (sent.mode, sent.tokens) == ("auto", None)
