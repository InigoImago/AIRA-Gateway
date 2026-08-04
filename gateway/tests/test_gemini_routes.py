import json


def test_generate_content(client) -> None:
    resp = client.post(
        "/v1beta/models/mock-1:generateContent",
        json={"contents": [{"role": "user", "parts": [{"text": "Hi there"}]}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    text = body["candidates"][0]["content"]["parts"][0]["text"]
    assert text.startswith("[mock:mock-1]")
    assert body["candidates"][0]["finishReason"] == "STOP"
    usage = body["usageMetadata"]
    assert usage["totalTokenCount"] == usage["promptTokenCount"] + usage["candidatesTokenCount"]
    assert body["modelVersion"] == "mock-1"


def test_generate_unknown_model_returns_404(client) -> None:
    resp = client.post(
        "/v1beta/models/nope:generateContent",
        json={"contents": [{"role": "user", "parts": [{"text": "x"}]}]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["status"] == "NOT_FOUND"


def test_missing_method_returns_400(client) -> None:
    resp = client.post("/v1beta/models/mock-1", json={"contents": [{"parts": [{"text": "x"}]}]})
    assert resp.status_code == 400
    assert resp.json()["error"]["status"] == "INVALID_ARGUMENT"


def test_invalid_body_returns_400(client) -> None:
    resp = client.post("/v1beta/models/mock-1:generateContent", json={"contents": []})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == 400


def test_non_json_body_returns_400(client) -> None:
    resp = client.post(
        "/v1beta/models/mock-1:generateContent",
        content="not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


def test_unknown_method_returns_400(client) -> None:
    resp = client.post("/v1beta/models/mock-1:frobnicate", json={"anything": 1})
    assert resp.status_code == 400


def test_embed_content(client) -> None:
    resp = client.post(
        "/v1beta/models/mock-1:embedContent",
        json={"content": {"parts": [{"text": "embed me"}]}},
    )
    assert resp.status_code == 200
    assert len(resp.json()["embedding"]["values"]) == 8


def test_embed_invalid_returns_400(client) -> None:
    resp = client.post("/v1beta/models/mock-1:embedContent", json={"content": {}})
    assert resp.status_code == 400


def test_list_models(client) -> None:
    resp = client.get("/v1beta/models")
    assert resp.status_code == 200
    assert "models/mock-1" in [m["name"] for m in resp.json()["models"]]


def test_get_model(client) -> None:
    resp = client.get("/v1beta/models/mock-1")
    assert resp.status_code == 200
    assert resp.json()["name"] == "models/mock-1"


def test_get_model_unknown_returns_404(client) -> None:
    resp = client.get("/v1beta/models/nope")
    assert resp.status_code == 404


_STREAM_BODY = {"contents": [{"role": "user", "parts": [{"text": "one two three four five"}]}]}


def test_stream_generate_content_json_array(client) -> None:
    resp = client.post("/v1beta/models/mock-1:streamGenerateContent", json=_STREAM_BODY)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")

    chunks = json.loads(resp.text)
    assert isinstance(chunks, list)
    assert len(chunks) >= 2
    assert chunks[-1]["candidates"][0]["finishReason"] == "STOP"

    streamed = "".join(c["candidates"][0]["content"]["parts"][0]["text"] for c in chunks).strip()
    assert streamed.startswith("[mock:mock-1]")


def test_stream_generate_content_sse(client) -> None:
    resp = client.post("/v1beta/models/mock-1:streamGenerateContent?alt=sse", json=_STREAM_BODY)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = [
        line.removeprefix("data: ") for line in resp.text.splitlines() if line.startswith("data: ")
    ]
    assert len(events) >= 2
    assert json.loads(events[-1])["candidates"][0]["finishReason"] == "STOP"
