"""An embedding verb runs no pipeline, and that is a stated gap rather than a quiet one.

`prepare_for_dispatch` runs the pipeline `if canonical is not None`, and an embedding request
carries no canonical *generation* — so `:embedContent` and `/kira/api/external/embed` go past the
whole stage. `FRD-300` recorded that as a non-goal when the steps were an injection filter and a
router, both of which are about a prompt a model will answer. `pii_filter` (`FRD-309`) arrived into
the same branch a fortnight later, and it is not the same decision: its contract is about **where
the caller's text goes and what is stored**, and an embedding call sends the same text to the same
class of upstream and writes it to the same audit row.

Measured on 2026-08-27 against the hermetic app: one use case, one `pii_filter`, the same sentence
— redacted on `:generateContent`, untouched on both embedding verbs.

**This file pins the gap rather than closing it.** Closing it is a feature: an embedding request
carries *N* texts (`FRD-113` FR-6), so applying the step is *N* redactor calls per request — a
cost, latency and batching decision that belongs to whoever owns the scope, not to a guard. What a
guard can do is make the fact unmissable: if somebody wires the pipeline into the embedding path,
this test fails and they have to say so in `FRD-309` §2, `docs/REQUEST-LIFECYCLE.md` and
`docs/GAP-ANALYSIS.md` — which is exactly the announcement a change of this kind owes a reader.

The shape this defends against is the one the project keeps naming: a control the console shows as
active for a use case, doing nothing on one of its verbs, with nothing anywhere saying so.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

PERSONAL = "Bitte an Max Mustermann, Hauptstrasse 3 senden."
REDACTED = "Bitte an <PERSON>, <ADDRESS> senden."


class _Redactor:
    """Serves both verbs, and answers a redaction request with the redacted sentence."""

    is_test_double = True

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("trusted", "trusted", ("generateContent", "embedContent"))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model="trusted",
            text=REDACTED,
            usage=CanonicalUsage(prompt_tokens=5, completion_tokens=5),
        )

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.5, 0.5]]


class _Store:
    async def get(self, use_case: str | None) -> Pipeline:
        return Pipeline(
            steps=(PipelineStep(type=StepType.PII_FILTER, config={"model": "trusted"}),),
            fallback_models=(),
        )


def _app():  # noqa: ANN202
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    registry = ProviderRegistry([_Redactor()])
    app.state.providers = registry
    # The engine keeps its own registry reference from construction.
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _Store()
    return app


async def _seed(app) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug="uc", name="uc", allowed_models=["trusted"]))
        session.add(
            ModelRead(
                model="trusted",
                approved=True,
                capabilities=["generate", "embed"],
                embedding={"dimensions": [2]},
            )
        )
        await session.commit()


async def test_a_generation_is_redacted_and_an_embedding_is_not() -> None:
    """Both halves in one test, because the finding is the **difference** between them."""
    app = _app()
    with TestClient(app) as client:
        await _seed(app)
        headers = {"x-aira-use-case": "uc"}

        generated = client.post(
            "/v1beta/models/trusted:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": PERSONAL}]}]},
            headers=headers,
        )
        embedded = client.post(
            "/v1beta/models/trusted:embedContent",
            json={"model": "models/trusted", "content": {"parts": [{"text": PERSONAL}]}},
            headers=headers,
        )

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert generated.status_code == 200, generated.text
    assert embedded.status_code == 200, embedded.text

    stored = {
        row.operation: str(row.request_payload)
        for row in rows
        if not row.operation.startswith("pipeline:")
    }
    generation = next(value for key, value in stored.items() if "embed" not in key)
    embedding = next(value for key, value in stored.items() if "embed" in key)

    assert "Mustermann" not in generation, "the generation path stopped redacting"
    assert "Mustermann" in embedding, (
        "the embedding path now redacts — good, and it has to be announced: update FRD-309 §2, "
        "docs/REQUEST-LIFECYCLE.md and docs/GAP-ANALYSIS.md, then delete this guard"
    )


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/v1beta/models/trusted:embedContent",
            {"model": "models/trusted", "content": {"parts": [{"text": PERSONAL}]}},
        ),
        ("/kira/api/external/embed", {"text": PERSONAL, "model_id": 4242}),
    ],
    ids=["gemini", "kira"],
)
async def test_no_embedding_surface_records_a_pipeline_decision(path: str, body: dict) -> None:
    """Both surfaces, because a gap on one of two is a different finding from a gap on both.

    It is on both: the branch is in `prepare_for_dispatch`, which is the layer both surfaces share
    (`FRD-126`). That is the reassuring half — there is one place to change when somebody closes
    this, not two that have to be kept agreeing.
    """
    app = _app()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc", name="uc", allowed_models=["trusted"]))
            session.add(
                ModelRead(
                    model="trusted",
                    approved=True,
                    numeric_id=4242,
                    capabilities=["generate", "embed"],
                    embedding={"dimensions": [2]},
                )
            )
            await session.commit()

        response = client.post(path, json=body, headers={"x-aira-use-case": "uc"})

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert response.status_code == 200, response.text
    assert not [row for row in rows if row.operation.startswith("pipeline:")], (
        "an embedding paid for a pipeline step — announce it as above"
    )
    assert all(not row.pipeline_decisions for row in rows), (
        "an embedding recorded a pipeline decision — announce it as above"
    )
