"""Half a surrogate pair in a hand-written body is the caller's `400`, wherever it sits.

JSON may escape one (`"\\ud800"`), and it parses into a Python string that no UTF-8 encoder takes.
Both generation surfaces and the ranking route refuse such a body where they read it; the incident
endpoints and the pipeline dry run read theirs and went on, so the value died far from where it
entered, as a `500`:

- a suspension's `target_value` or `reason` passes the control-character check and fails in the
  database driver — the stop an incident responder meant to put in place is not stored;
- a thinking word to check is echoed into the answer, which cannot be encoded;
- a category's model in a dry run's pipeline — a mapping the request schema does not type — becomes
  the model a request is built for, and that request refuses it.

Sent as a client sends it: the escape in otherwise valid JSON.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from aira_gateway.anomalies.suspensions import AccessSuspension
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, UseCaseRead

#: Both incident roles, and a member of the use case the dry run names.
ADMIN = Principal(
    subject="root",
    method="oidc",
    roles=("global-admin", "it-security"),
    use_cases=("demo-uc",),
    username="root",
)

LONE = "\ud800"

CASES: list[tuple[str, dict[str, Any]]] = [
    ("/v1beta/suspensions", {"target": "subject", "target_value": f"ada{LONE}"}),
    ("/v1beta/suspensions", {"target": "subject", "target_value": "ada", "reason": f"x{LONE}"}),
    ("/v1beta/models/mock-1:checkThinking", {"modes": [f"auto{LONE}"]}),
    ("/v1beta/models/mock-1:checkThinking", {"levels": [f"low{LONE}"]}),
    (
        "/v1beta/pipeline:dryRun",
        {
            "use_case": "demo-uc",
            "user": "hi",
            "pipeline": {
                "steps": [
                    {
                        "type": "model_route",
                        "config": {"categories": [{"name": "a", "model": f"m{LONE}"}]},
                    }
                ]
            },
        },
    ),
]


@pytest.mark.parametrize(("path", "body"), CASES, ids=[f"{p}:{sorted(b)}" for p, b in CASES])
def test_a_lone_surrogate_is_refused_where_the_body_is_read(
    path: str, body: dict[str, Any]
) -> None:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.dependency_overrides[require_principal] = lambda: ADMIN
    with TestClient(app, raise_server_exceptions=False) as client:

        async def seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(
                    ModelRead(model="mock-1", capabilities=["generate"], publisher="google")
                )
                session.add(UseCaseRead(slug="demo-uc", name="demo-uc", allowed_models=None))
                await session.commit()

        async def stops() -> int:
            async with app.state.db_sessionmaker() as session:
                count = await session.execute(select(func.count()).select_from(AccessSuspension))
                return int(count.scalar_one())

        client.portal.call(seed)  # type: ignore[attr-defined]
        raw = json.dumps(body).encode("ascii")
        response = client.post(path, content=raw, headers={"content-type": "application/json"})
        stored = client.portal.call(stops)  # type: ignore[attr-defined]

    assert response.status_code == 400, (path, response.status_code, response.text[:300])
    assert "surrogate" in response.json()["error"]["message"]
    assert stored == 0
