"""A chain that found nobody to answer still says whom it tried, and why (`FRD-302` FR-11).

**What was wrong.** The candidates a chain passed over reached the audit row only when a later one
answered: the routes record ``trail.passed_over`` on success, and an exhausted chain raised its
error without the two lists. So the row of a refused request — `upstream_error` after every model
in the chain had failed, `no_capable_model` after every one was excluded — carried no
`pipeline_decisions` at all, while FR-11 promised both kinds of entry there. The reasons survived
only in the response message, which is not stored. The row that most needed to explain itself was
the one that could not.

Now the chain's error carries what it passed over (`PassedOver`), and the refusal boundary records
it like the success path does — on both surfaces, for a buffered and for a streamed request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.pipeline.config import Pipeline
from aira_gateway.pipeline.dispatch import (
    NoCapableModel,
    dispatch_with_fallback,
    passed_over_by,
    stream_with_fallback,
)
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

_GEMINI = {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}
#: The predecessor addresses a model by number; `_catalogue` hands them out from 9001.
_KIRA = {"request": {"parts": [{"text": "hello"}]}, "model_id": 9001}
_USAGE = CanonicalUsage(prompt_tokens=1, completion_tokens=1)


class _Down:
    """An adapter whose every call fails, buffered or streamed, with the vendor's 503."""

    def __init__(self, model: str) -> None:
        self._model = model

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(
                self._model, self._model, ("generateContent", "streamGenerateContent"), "acme"
            )
        ]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        raise UpstreamError(f"{self._model} is unavailable", 503)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        raise UpstreamError(f"{self._model} is unavailable", 503)
        yield  # pragma: no cover — unreachable, and what makes this an async generator

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


class _Up(_Down):
    """An adapter that would answer — used where a condition must exclude it first."""

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(model=request.model, text="answered", usage=_USAGE)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        yield CanonicalChunk(text_delta="answered", usage=_USAGE, finish_reason="stop")


class _FixedStore:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def get(self, use_case: str | None) -> Pipeline:
        return self._pipeline


def _app(chain: tuple[str, ...], *providers: object) -> Any:
    app = create_app(GatewaySettings(auth_required=False, require_use_case=False, log_queue_size=0))
    registry = ProviderRegistry(list(providers))
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _FixedStore(Pipeline(fallback_models=chain))
    return app


async def _catalogue(app: Any, *models: str, unapproved: tuple[str, ...] = ()) -> None:
    async with app.state.db_sessionmaker() as session:
        for number, model in enumerate(models, start=9001):
            session.add(
                ModelRead(
                    model=model,
                    numeric_id=number,
                    approved=model not in unapproved,
                    capabilities=["generate"],
                )
            )
        await session.commit()


async def _rows(app: Any) -> list[RequestLog]:
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


def _decisions(row: RequestLog) -> set[tuple[str | None, str | None]]:
    return {
        (decision.get("action"), decision.get("to")) for decision in row.pipeline_decisions or []
    }


# == the chain hands its report to whoever records the refusal ===================================


async def test_a_chain_whose_every_candidate_failed_raises_the_report_with_the_error() -> None:
    registry = ProviderRegistry([_Down("primary-1"), _Down("backup-1")])
    request = CanonicalRequest(
        model="primary-1",
        messages=[{"role": "user", "parts": [{"type": "text", "text": "hello"}]}],  # type: ignore[list-item]
    )

    with pytest.raises(UpstreamError) as caught:
        await dispatch_with_fallback(registry, request, ("backup-1",))
    report = passed_over_by(caught.value)

    assert report is not None, "the error left the chain without saying what was tried"
    assert [(entry.model, entry.reason) for entry in report.failed] == [
        ("primary-1", "primary-1 is unavailable (HTTP 503)"),
        ("backup-1", "backup-1 is unavailable (HTTP 503)"),
    ]


async def test_a_streamed_chain_that_excluded_everybody_raises_the_report_too() -> None:
    registry = ProviderRegistry([_Up("primary-1"), _Up("backup-1")])
    request = CanonicalRequest(
        model="primary-1",
        messages=[{"role": "user", "parts": [{"type": "text", "text": "hello"}]}],  # type: ignore[list-item]
    )

    async def permits(model: str) -> str | None:
        return f"{model} is not released"

    with pytest.raises(NoCapableModel) as caught:
        await stream_with_fallback(registry, request, ("backup-1",), permits=permits)
    report = passed_over_by(caught.value)

    assert report is not None
    assert [entry.model for entry in report.skipped] == ["primary-1", "backup-1"]
    assert report.failed == ()


def test_an_error_no_chain_raised_carries_no_report() -> None:
    """The refusal boundary asks every error; one that did not come out of a chain must not
    answer with somebody else's candidates."""
    assert passed_over_by(UpstreamError("raised elsewhere", 502)) is None


# == and the row says it, on both surfaces ========================================================


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1beta/models/primary-1:generateContent", _GEMINI),
        ("/v1beta/models/primary-1:streamGenerateContent", _GEMINI),
        ("/kira/api/external/chat", _KIRA),
        ("/kira/api/external/streaming-chat", _KIRA),
    ],
)
async def test_the_row_of_a_chain_that_failed_names_every_attempt(
    path: str, body: dict[str, Any]
) -> None:
    app = _app(("backup-1",), _Down("primary-1"), _Down("backup-1"))
    with TestClient(app) as client:
        await _catalogue(app, "primary-1", "backup-1")
        response = client.post(path, json=body)
        rows = await _rows(app)

    assert response.status_code >= 400, response.text
    assert [row.outcome for row in rows] == ["upstream_error"]
    assert _decisions(rows[0]) == {("failed", "primary-1"), ("failed", "backup-1")}, (
        "the row of a request every model failed does not say which models were tried"
    )
    assert "unavailable" in str(rows[0].pipeline_decisions)


@pytest.mark.parametrize(
    "path",
    [
        "/v1beta/models/primary-1:generateContent",
        "/v1beta/models/primary-1:streamGenerateContent",
    ],
)
async def test_the_row_names_a_skipped_fallback_beside_a_failed_primary(path: str) -> None:
    """The two kinds side by side: the primary was tried and failed, the fallback was never called
    because the installation has not approved it — something a vendor fixes beside something an
    operator fixes, which is why FR-11 keeps them apart."""
    app = _app(("backup-1",), _Down("primary-1"), _Up("backup-1"))
    with TestClient(app) as client:
        await _catalogue(app, "primary-1", "backup-1", unapproved=("backup-1",))
        response = client.post(path, json=_GEMINI)
        rows = await _rows(app)

    assert response.status_code == 503, response.text
    assert [row.outcome for row in rows] == ["upstream_error"]
    assert _decisions(rows[0]) == {("failed", "primary-1"), ("skipped", "backup-1")}
