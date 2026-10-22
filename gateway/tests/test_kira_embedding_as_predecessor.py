"""A `gemini-embedding-2` vector on the KIRA surface is the predecessor's (`FRD-146` FR-7 to FR-10).

An index built through the predecessor holds vectors of a particular request: each text inside its
task's prompt, as one part, all parts of one content, through `:embedContent`, at the width of the
model id the caller named. Any other request is a different vector, and the index silently
retrieves worse. So the upstream body is pinned here as Google receives it — through the real Vertex
adapter, with only Google replaced.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from aira_common.tokens import StaticTokenSource
from aira_gateway.api.kira.embedding_prompts import PROMPTS
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalEmbeddingRequest
from aira_gateway.db.models import ModelRead, UseCaseRead
from aira_gateway.embedding import TASK_TYPES
from aira_gateway.upstreams.base import DialectUnsupported, ProviderRegistry
from aira_gateway.upstreams.vertex.adapters import VertexGeminiAdapter
from aira_gateway.upstreams.vertex.transport import VertexTransport

KIRA = "/kira/api/external"
EIGHT = sorted(TASK_TYPES)
HOST = "https://europe-west1-aiplatform.googleapis.com/v1/projects/p/locations/europe-west1"
URL = f"{HOST}/publishers/google/models/gemini-embedding-2:embedContent"


class Google:
    """Google's side of the wire: what was sent, and a vector as wide as was asked."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.seen.append((str(request.url), body))
        width = (body.get("embedContentConfig") or {}).get("outputDimensionality", 3072)
        return httpx.Response(
            200,
            json={
                "embedding": {"values": [0.25] * width},
                "usageMetadata": {"promptTokenCount": 9},
            },
        )


def _adapter(google: Google) -> VertexGeminiAdapter:
    transport = VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(google)),
    )
    return VertexGeminiAdapter(transport, [])


def _entry(name: str, numeric_id: int, width: int, **fields: Any) -> ModelRead:
    addressing: dict[str, Any] = {"regions": ["europe-west1"], **fields.pop("addressing", {})}
    return ModelRead(
        model=name,
        numeric_id=numeric_id,
        provider="vertex",
        publisher="google",
        capabilities=["embed"],
        addressing=addressing,
        embedding={
            "task_types": fields.pop("task_types", EIGHT),
            "supports_batch": True,
            "dimensions": [width],
            "default": width,
        },
        **fields,
    )


def _app(google: Google, *entries: ModelRead, released: list[str] | None = None):  # noqa: ANN202
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([_adapter(google)])
    app.state._seed = [  # noqa: SLF001
        *entries,
        *(
            [UseCaseRead(slug="uc", name="uc", allowed_models=released)]
            if released is not None
            else []
        ),
    ]
    return app


async def _seed(app) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add_all(app.state._seed)  # noqa: SLF001
        await session.commit()


#: The predecessor's two ids for one model: 3072 and 768 values, one upstream model.
FULL = ("gemini-embedding-2", 2003, 3072)
NARROW = ("gemini-embedding-2-768", 2004, 768)


def _both() -> tuple[ModelRead, ModelRead]:
    return (
        _entry(*FULL),
        _entry(*NARROW, addressing={"model": "gemini-embedding-2"}),
    )


# == the body Google receives =====================================================================


async def test_one_text_is_one_part_inside_the_default_tasks_prompt() -> None:
    google = Google()
    app = _app(google, *_both())
    with TestClient(app) as client:
        await _seed(app)
        response = client.post(f"{KIRA}/embed", json={"text": "Bonn", "model_id": 2003})

    assert response.status_code == 200, response.text
    assert google.seen == [
        (
            URL,
            {
                "content": {"parts": [{"text": "task: search result | query: Bonn"}]},
                # No `taskType`: the task is in the text, and Google refuses the field for this
                # model.
                "embedContentConfig": {"autoTruncate": False, "outputDimensionality": 3072},
            },
        )
    ]


async def test_a_list_is_one_call_with_a_part_per_text_and_one_vector() -> None:
    google = Google()
    app = _app(google, *_both())
    with TestClient(app) as client:
        await _seed(app)
        response = client.post(
            f"{KIRA}/embed",
            json={"text": ["a", "b"], "model_id": 2003, "task_type": "RETRIEVAL_DOCUMENT"},
        )

    assert response.status_code == 200, response.text
    assert len(response.json()["vector"]) == 3072
    ((_, body),) = google.seen
    assert body["content"]["parts"] == [
        {"text": "title: none | text: a"},
        {"text": "title: none | text: b"},
    ]


@pytest.mark.parametrize("task_type", EIGHT)
async def test_every_task_type_is_written_as_googles_prompt(task_type: str) -> None:
    google = Google()
    app = _app(google, *_both())
    with TestClient(app) as client:
        await _seed(app)
        response = client.post(
            f"{KIRA}/embed", json={"text": "x", "model_id": 2003, "task_type": task_type}
        )

    assert response.status_code == 200, response.text
    ((_, body),) = google.seen
    assert body["content"]["parts"] == [{"text": PROMPTS[task_type].format(text="x")}]
    assert "taskType" not in body["embedContentConfig"]


def test_the_prompt_table_is_googles_for_all_eight() -> None:
    assert set(PROMPTS) == set(TASK_TYPES)
    assert all("{text}" in prompt for prompt in PROMPTS.values())


async def test_a_task_the_model_does_not_declare_is_refused_before_google_is_asked() -> None:
    """The predecessor always names a task, so a model must declare the ones it takes — absence
    of a declaration is not permission (`FRD-114` FR-7)."""
    google = Google()
    app = _app(google, _entry(*FULL, task_types=["RETRIEVAL_QUERY"]))
    with TestClient(app) as client:
        await _seed(app)
        refused = client.post(
            f"{KIRA}/embed", json={"text": "x", "model_id": 2003, "task_type": "CLUSTERING"}
        )

    assert refused.status_code == 422
    assert refused.json()["code"] == "INVALID_EMBEDDING_TASK_TYPE"
    assert google.seen == []


# == one model under two ids (FR-10) ==============================================================


async def test_two_ids_are_two_widths_of_one_upstream_model() -> None:
    google = Google()
    app = _app(google, *_both())
    with TestClient(app) as client:
        await _seed(app)
        full = client.post(f"{KIRA}/embed", json={"text": "x", "model_id": 2003})
        narrow = client.post(f"{KIRA}/embed", json={"text": "x", "model_id": 2004})

    assert (full.status_code, narrow.status_code) == (200, 200), narrow.text
    assert (len(full.json()["vector"]), len(narrow.json()["vector"])) == (3072, 768)
    assert [url for url, _ in google.seen] == [URL, URL], "both are the same model at Google"


async def test_an_alias_is_released_on_its_own() -> None:
    """Releasing the model releases nothing about its alias — they are two entries, each decided
    by somebody."""
    google = Google()
    app = _app(google, *_both(), released=["gemini-embedding-2"])
    headers = {"x-aira-use-case": "uc"}
    with TestClient(app) as client:
        await _seed(app)
        released = client.post(
            f"{KIRA}/embed", json={"text": "x", "model_id": 2003}, headers=headers
        )
        alias = client.post(f"{KIRA}/embed", json={"text": "x", "model_id": 2004}, headers=headers)

    assert released.status_code == 200, released.text
    assert alias.status_code == 400, alias.text
    assert "gemini-embedding-2-768" in alias.json()["message"]
    assert len(google.seen) == 1


async def test_an_alias_is_permitted_a_region_by_its_own_name() -> None:
    """A region permitted by name is permitted to the entry (`ADR-0034`), not to whatever model it
    is an alias of — or naming one would open it for every alias somebody adds later."""
    google = Google()
    adapter = VertexGeminiAdapter(
        VertexTransport(
            project="p",
            tokens=StaticTokenSource("t"),
            client=httpx.AsyncClient(transport=httpx.MockTransport(google)),
            model_regions={"gemini-embedding-2": frozenset({"global"})},
        ),
        [],
    )
    request = CanonicalEmbeddingRequest(
        model="gemini-embedding-2-768",
        addressing={"regions": ["global"], "model": "gemini-embedding-2"},
        texts=["x"],
    )

    from aira_gateway.residency import RegionNotAllowed

    with pytest.raises(RegionNotAllowed):
        await adapter.embed(request)
    assert google.seen == []


# == the Gemini surface keeps its vectors =========================================================


async def test_the_gemini_surface_sends_the_text_as_written() -> None:
    google = Google()
    app = _app(google, *_both())
    with TestClient(app) as client:
        await _seed(app)
        single = client.post(
            "/v1beta/models/gemini-embedding-2:embedContent",
            json={"content": {"parts": [{"text": "Bonn"}]}},
        )
        batch = client.post(
            "/v1beta/models/gemini-embedding-2:batchEmbedContents",
            json={
                "requests": [
                    {"content": {"parts": [{"text": "a"}]}},
                    {"content": {"parts": [{"text": "b"}]}},
                ]
            },
        )

    assert single.status_code == 200, single.text
    assert batch.status_code == 200, batch.text
    assert len(batch.json()["embeddings"]) == 2, "one vector per text"
    parts = [body["content"]["parts"] for _, body in google.seen]
    assert parts == [[{"text": "Bonn"}], [{"text": "a"}], [{"text": "b"}]]


# == a model that embeds one text per vector ======================================================


async def test_predict_cannot_make_one_vector_of_several_texts() -> None:
    adapter = _adapter(Google())

    with pytest.raises(DialectUnsupported):
        await adapter.embed(
            CanonicalEmbeddingRequest(
                model="gemini-embedding-001",
                addressing={"regions": ["europe-west1"]},
                texts=["a", "b"],
                aggregate=True,
            )
        )
