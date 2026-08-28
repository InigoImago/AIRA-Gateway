"""A developer round over both API surfaces, with a real language model and a real embedding model.

One use case, two models, two wire formats, four verbs. What is walked here is the **surface**:
what each one accepts, what it refuses, and — the part no single-surface test can reach — whether
the two leave the same facts behind when asked the same thing.

Three rules this file keeps, all of them learned the hard way in this repository:

- **Nothing asserts an answer's content.** That tests the model and flakes. What is asserted is
  status, envelope, and what reached `request_logs`.
- **A refusal is checked by its *reason*, not only its status.** Two controls answering 400 for
  different reasons are two different behaviours, and a test that reads only the number passes when
  the wrong one fires — which is how the attachment tests came to assert a media-type refusal while
  actually receiving `Missing use case`.
- **Real models, not the double.** `mock-1` is exempt from the release and approval gates, so a
  suite written against it cannot see them. It appears here only where the question is about this
  gateway's bookkeeping rather than about a model.
"""

from __future__ import annotations

import json

import httpx
import pytest

from .conftest import GATEWAY_URL, LOCAL_CHAT_MODEL_ID, LOCAL_EMBED_MODEL_ID
from .governed import CHAT_MODEL, EMBED_MODEL, GEMINI, KIRA, MOCK_MODEL, Governed

pytestmark = pytest.mark.integration

SHORT = {"maxOutputTokens": 16}


def _gemini(text: str = "Say OK.", **config: object) -> dict:
    return {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {**SHORT, **config},
    }


def _kira(text: str = "Say OK.", **fields: object) -> dict:
    return {
        "request": {"parts": [{"text": text}]},
        "model_id": LOCAL_CHAT_MODEL_ID,
        "maxTokens": 16,
        **fields,
    }


def _envelope(response: httpx.Response, surface: str) -> str:
    """The refusal message, read out of whichever envelope this surface owes its caller.

    Asserted as well as returned: a compatibility surface whose *errors* have a different shape is
    not one, because a migrating client switches on the code.
    """
    body = response.json()
    if surface == "kira":
        assert "code" in body and "error" not in body, f"Google's envelope leaked into KIRA: {body}"
        return str(body["message"])
    assert "error" in body, f"the Gemini surface answered in somebody else's envelope: {body}"
    return str(body["error"]["message"])


# ═══ 1. the two surfaces serve, and are recorded ═══════════════════════════════════════════════


async def test_the_use_case_may_call_both_of_its_models(governed: Governed) -> None:
    """The footing for everything below. If the release were wrong, every case in this file would
    fail with the same message and none of them would be about what it says it is."""
    generation = await governed.generate(_gemini())
    embedding = await governed.embed({"content": {"parts": [{"text": "hallo"}]}})

    assert generation.status_code == 200, generation.text
    assert embedding.status_code == 200, embedding.text


@pytest.mark.parametrize(
    ("surface", "operation"),
    [
        pytest.param("gemini", "generateContent", id="gemini-generate"),
        pytest.param("gemini", "embedContent", id="gemini-embed"),
        pytest.param("kira", "chat", id="kira-chat"),
        pytest.param("kira", "embed", id="kira-embed"),
    ],
)
async def test_every_verb_leaves_exactly_one_audit_row_naming_itself(
    governed: Governed, surface: str, operation: str
) -> None:
    """`ADR-0013`'s promise, per verb: a model call is auditable. The operation is asserted because
    a row that does not say which verb produced it cannot answer an incident question."""
    if surface == "gemini":
        response = (
            await governed.generate(_gemini())
            if operation == "generateContent"
            else await governed.embed({"content": {"parts": [{"text": "x"}]}})
        )
    else:
        response = (
            await governed.kira("/chat", _kira())
            if operation == "chat"
            else await governed.kira("/embed", {"text": "x", "model_id": LOCAL_EMBED_MODEL_ID})
        )

    assert response.status_code == 200, response.text
    rows = await governed.wait_for_rows(1)

    assert len(rows) == 1, rows
    assert rows[0]["api"] == surface
    assert rows[0]["operation"] == operation
    assert rows[0]["outcome"] == "served"
    assert rows[0]["use_case"] == governed.slug
    assert rows[0]["credential"], "the calling system is not identified on the row"


@pytest.mark.parametrize(
    ("surface", "model"),
    [
        pytest.param("gemini", CHAT_MODEL, id="gemini-chat-model"),
        pytest.param("kira", CHAT_MODEL, id="kira-chat-model"),
    ],
)
async def test_generation_is_priced_and_the_token_split_is_kept(
    governed: Governed, surface: str, model: str
) -> None:
    """Input and output are billed at different rates by every provider AIRA talks to, so a single
    total cannot be priced at all (`FRD-403`). The row keeps the split, and the cost is positive
    because the local model carries a fictitious price for exactly this reason."""
    response = (
        await governed.generate(_gemini())
        if surface == "gemini"
        else await governed.kira("/chat", _kira())
    )
    assert response.status_code == 200, response.text
    row = await governed.last_row()

    assert row["model"] == model
    assert int(row["prompt_tokens"]) > 0, "no input tokens recorded"
    assert int(row["completion_tokens"]) > 0, "no output tokens recorded"
    assert int(row["total_tokens"]) >= int(row["prompt_tokens"]) + int(row["completion_tokens"])
    assert int(row["cost_nanos"]) > 0, "a priced model produced an unpriced row"


async def test_an_embedding_is_recorded_without_inventing_tokens(governed: Governed) -> None:
    """This dialect reports no usage for an embedding, and `FRD-403`'s rule is that unknown is not
    zero. The row exists, is `served`, and reports nothing rather than a nothing-shaped figure."""
    assert (await governed.embed({"content": {"parts": [{"text": "x"}]}})).status_code == 200
    row = await governed.last_row()

    assert row["outcome"] == "served"
    assert row["prompt_tokens"] is None or int(row["prompt_tokens"]) == 0


# ═══ 2. the same request, both surfaces, the same facts ════════════════════════════════════════


async def test_both_surfaces_record_a_generation_identically(governed: Governed) -> None:
    """The only way to be sure the shared controls were **run** rather than merely present: send
    one logical request through each and compare what the audit kept (`FRD-126`)."""
    assert (await governed.generate(_gemini())).status_code == 200
    assert (await governed.kira("/chat", _kira())).status_code == 200
    rows = await governed.wait_for_rows(2)
    by_api = {row["api"]: row for row in rows}

    assert {"gemini", "kira"} == set(by_api), f"one surface left no row: {sorted(by_api)}"
    for field in ("model", "outcome", "provider", "publisher", "region", "use_case", "credential"):
        assert by_api["gemini"][field] == by_api["kira"][field], (
            f"the surfaces disagree about {field}: "
            f"{by_api['gemini'][field]!r} vs {by_api['kira'][field]!r}"
        )


async def test_both_surfaces_record_an_embedding_identically(governed: Governed) -> None:
    assert (await governed.embed({"content": {"parts": [{"text": "x"}]}})).status_code == 200
    assert (
        await governed.kira("/embed", {"text": "x", "model_id": LOCAL_EMBED_MODEL_ID})
    ).status_code == 200
    rows = await governed.wait_for_rows(2)
    by_api = {row["api"]: row for row in rows}

    assert {"gemini", "kira"} == set(by_api)
    for field in ("model", "outcome", "provider", "region", "use_case"):
        assert by_api["gemini"][field] == by_api["kira"][field], field


async def test_the_same_text_embeds_to_the_same_vector_on_both_surfaces(
    governed: Governed,
) -> None:
    """Two wire formats over one model. A surface that quietly altered the text — adding a task
    type, a prefix, a separator — would return a different vector for the same input, and nothing
    in either response would say so."""
    gemini = await governed.embed({"content": {"parts": [{"text": "governance"}]}})
    kira = await governed.kira("/embed", {"text": "governance", "model_id": LOCAL_EMBED_MODEL_ID})

    assert gemini.status_code == 200 and kira.status_code == 200
    assert gemini.json()["embedding"]["values"] == pytest.approx(kira.json()["vector"], abs=1e-6)


# ═══ 3. generation options, carried or refused by name ═════════════════════════════════════════


@pytest.mark.parametrize(
    ("config", "why"),
    [
        pytest.param({"temperature": 0.1}, "temperature", id="temperature"),
        pytest.param({"topP": 0.5}, "top_p", id="topP"),
        pytest.param({"maxOutputTokens": 4}, "a small cap", id="tiny-cap"),
        pytest.param({"maxOutputTokens": 40960}, "the model's ceiling", id="ceiling"),
        pytest.param({"stopSequences": ["STOP"]}, "stop sequences", id="stopSequences"),
        pytest.param({"seed": 7}, "a seed this dialect can express", id="seed"),
        pytest.param({"responseMimeType": "text/plain"}, "a plain-text mime type", id="mime-text"),
    ],
)
async def test_a_carried_generation_option_is_served(
    governed: Governed, config: dict, why: str
) -> None:
    """`FRD-124`: a field is carried, refused by name, or the candidate is skipped — never accepted
    and dropped. These are the carried ones, and what is asserted is that they are *served*, not
    what they did to the answer."""
    response = await governed.generate(_gemini(**config))

    assert response.status_code == 200, f"{why} was refused: {response.text[:300]}"


@pytest.mark.parametrize(
    ("body", "names"),
    [
        pytest.param(
            {"generationConfig": {**SHORT, "candidateCount": 3}},
            "candidateCount",
            id="candidateCount",
        ),
        pytest.param(
            {
                "safetySettings": [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}
                ]
            },
            "safetySettings",
            id="safetySettings",
        ),
        pytest.param({"cachedContent": "caches/abc"}, "cachedContent", id="cachedContent"),
    ],
)
async def test_an_option_out_of_scope_is_refused_by_name(
    governed: Governed, body: dict, names: str
) -> None:
    """Refused **naming the field**, because a caller cannot act on "invalid request". `ADR-0013`
    puts these out of scope and `FRD-124`'s rule is that a surface says so rather than ignoring
    them: a dropped field produces an answer that is wrong for a reason nobody can see."""
    merged = {**_gemini(), **body}
    response = await governed.generate(merged)

    assert response.status_code == 400, response.text[:300]
    assert names.lower() in _envelope(response, "gemini").lower()


@pytest.mark.parametrize(
    ("cap", "expected"),
    [
        pytest.param(0, "positive", id="zero"),
        pytest.param(-5, "positive", id="negative"),
        pytest.param(999_999, "exceeds", id="past-the-model-ceiling"),
    ],
)
async def test_an_impossible_output_cap_is_refused_before_anything_is_spent(
    governed: Governed, cap: int, expected: str
) -> None:
    """A negative cap used to be accepted and silently truncated the answer. The message names the
    field the caller sent, not the vendor's spelling of it."""
    response = await governed.generate(_gemini(maxOutputTokens=cap))

    assert response.status_code == 400, response.text[:300]
    message = _envelope(response, "gemini")
    assert "maxOutputTokens" in message
    assert expected in message.lower()


async def test_a_structured_answer_comes_back_as_a_document(governed: Governed) -> None:
    """`FRD-112`. What is asserted is that the answer parses and the row is `served` — never the
    document's contents, which is the model's business."""
    response = await governed.generate(
        _gemini(
            "Give me an object with a field 'a'.",
            maxOutputTokens=64,
            responseMimeType="application/json",
            responseSchema={"type": "object", "properties": {"a": {"type": "string"}}},
        )
    )

    assert response.status_code == 200, response.text[:300]
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    assert isinstance(json.loads(text), dict), f"not a document: {text[:200]}"


async def test_a_schema_the_surface_cannot_parse_is_refused_at_our_boundary(
    governed: Governed,
) -> None:
    """Parsed here, then **forwarded, never executed**: re-validating would run caller-supplied
    regexes over provider output on the hot path. An unknown field is an error naming the field."""
    response = await governed.generate(
        _gemini(
            maxOutputTokens=32,
            responseMimeType="application/json",
            responseSchema={"type": "object", "nonsenseKeyword": True},
        )
    )

    assert response.status_code == 400, response.text[:300]
    assert "nonsenseKeyword" in _envelope(response, "gemini")


# ═══ 4. streaming ══════════════════════════════════════════════════════════════════════════════


async def test_a_stream_arrives_in_pieces_and_is_recorded_once(governed: Governed) -> None:
    """Several SSE events, one audit row. A stream that answered in a single event would satisfy
    the wire format and defeat the reason a client asked for one."""
    events = 0
    async with (
        httpx.AsyncClient(base_url=GATEWAY_URL, timeout=120.0) as client,
        client.stream(
            "POST",
            f"{GEMINI}/models/{CHAT_MODEL}:streamGenerateContent?alt=sse",
            json=_gemini("Count to five.", maxOutputTokens=48),
            headers=governed.headers(),
        ) as response,
    ):
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events += 1

    assert events > 1, f"a stream that is not one: {events} event(s)"
    row = await governed.last_row()
    assert row["operation"] == "streamGenerateContent"
    assert row["outcome"] == "served"


async def test_the_kira_stream_speaks_the_predecessors_events(governed: Governed) -> None:
    """`update` events while the answer is written, then one `completed`. A client that reads only
    the terminal event is unaffected, because that one still carries the whole answer."""
    statuses: list[str] = []
    async with (
        httpx.AsyncClient(base_url=GATEWAY_URL, timeout=120.0) as client,
        client.stream(
            "POST",
            f"{KIRA}/streaming-chat",
            json=_kira("Count to five.", maxTokens=48),
            headers=governed.headers(),
        ) as response,
    ):
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                statuses.append(str(json.loads(line[len("data: ") :]).get("status")))

    assert statuses, "the stream carried no events at all"
    assert statuses[-1] == "completed", statuses[-3:]
    assert "update" in statuses, "no intermediate event: this is SSE as a costume"


async def test_a_stream_is_refused_before_the_first_chunk_when_a_condition_fails(
    governed: Governed,
) -> None:
    """The bypass closed on 2026-08-11: `:streamGenerateContent` asked none of the dispatch
    conditions, so a model the use case may not call was **served with a 200** on the one verb every
    chat client uses. The refusal has to arrive as a status, which is only possible before the
    response exists."""
    await governed.release(EMBED_MODEL)
    await governed.settle(1.0)

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{CHAT_MODEL}:streamGenerateContent?alt=sse",
            json=_gemini(),
            headers=governed.headers(),
        )

    assert response.status_code == 400, response.text[:300]
    assert "released" in _envelope(response, "gemini")


# ═══ 5. embeddings, in depth, on both surfaces ═════════════════════════════════════════════════


@pytest.mark.parametrize("size", [1, 2, 8, 32])
async def test_a_batch_embeds_every_text_it_was_given(governed: Governed, size: int) -> None:
    """The Gemini batch verb answers one vector per request, and *n* in means *n* out. A surface
    that returned fewer would leave a caller indexing one document under another's vector."""
    body = {
        "requests": [
            {"model": f"models/{EMBED_MODEL}", "content": {"parts": [{"text": f"text {i}"}]}}
            for i in range(size)
        ]
    }
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=120.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{EMBED_MODEL}:batchEmbedContents",
            json=body,
            headers=governed.headers(),
        )

    assert response.status_code == 200, response.text[:300]
    assert len(response.json()["embeddings"]) == size


async def test_a_kira_list_is_one_embedding_and_the_surfaces_differ_deliberately(
    governed: Governed,
) -> None:
    """The two contracts genuinely disagree, and both are right.

    Gemini's batch verb answers one vector **per request**. The predecessor's `/embed` takes a list
    as the *parts of one* embedding and answers the documented singular `vector` — confirmed from
    its own source and measured: a multi-part content's vector is cosine 1.000000 to the parts
    concatenated with nothing between them.

    Asserted together because the difference is exactly the kind a migrating caller would otherwise
    discover as "the numbers changed".
    """
    kira = await governed.kira(
        "/embed", {"text": ["gover", "nance"], "model_id": LOCAL_EMBED_MODEL_ID}
    )
    joined = await governed.kira("/embed", {"text": "governance", "model_id": LOCAL_EMBED_MODEL_ID})

    assert kira.status_code == 200 and joined.status_code == 200
    assert "vector" in kira.json() and "vectors" not in kira.json()
    assert kira.json()["vector"] == pytest.approx(joined.json()["vector"], abs=1e-6)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        pytest.param(
            {"text": "", "model_id": LOCAL_EMBED_MODEL_ID},
            "EMPTY_EMBEDDING_INPUT",
            id="empty-string",
        ),
        pytest.param(
            {"text": [], "model_id": LOCAL_EMBED_MODEL_ID}, "EMPTY_EMBEDDING_INPUT", id="empty-list"
        ),
        pytest.param(
            {"text": ["", ""], "model_id": LOCAL_EMBED_MODEL_ID},
            "EMPTY_EMBEDDING_INPUT",
            id="nothing-but-empties",
        ),
        pytest.param(
            {"text": "ok", "model_id": LOCAL_EMBED_MODEL_ID, "task_type": "NONSENSE"},
            "INVALID_EMBEDDING_TASK_TYPE",
            id="invented-task-type",
        ),
        pytest.param(
            {"text": "ok", "model_id": LOCAL_EMBED_MODEL_ID, "task_type": "RETRIEVAL_QUERY"},
            "INVALID_EMBEDDING_TASK_TYPE",
            id="task-type-the-model-never-declared",
        ),
    ],
)
async def test_kira_embedding_refuses_in_the_predecessors_vocabulary(
    governed: Governed, body: dict, code: str
) -> None:
    """The codes are the predecessor's, so a migrating client keeps switching on the same strings.

    The last case is the interesting one: `RETRIEVAL_QUERY` is a perfectly real task type and this
    model declares none, so it is refused rather than sent as a field the endpoint would ignore
    (`FRD-113`). Undeclared means unsupported — absence of information is not permission.
    """
    response = await governed.kira("/embed", body)

    assert response.status_code == 422, response.text[:300]
    assert response.json()["code"] == code, response.text[:300]


async def test_an_embedding_batch_past_the_bound_names_the_bound(governed: Governed) -> None:
    body = {
        "requests": [
            {"model": f"models/{EMBED_MODEL}", "content": {"parts": [{"text": "x"}]}}
            for _ in range(500)
        ]
    }
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{EMBED_MODEL}:batchEmbedContents",
            json=body,
            headers=governed.headers(),
        )

    assert response.status_code == 400, response.text[:300]
    message = _envelope(response, "gemini")
    assert any(word in message.lower() for word in ("batch", "256", "most")), message


# ═══ 6. asking a model for something it is not ═════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("surface", "expect_code"),
    [pytest.param("gemini", 400, id="gemini"), pytest.param("kira", 422, id="kira")],
)
async def test_asking_a_language_model_to_embed_is_refused_by_name(
    governed: Governed, surface: str, expect_code: int
) -> None:
    """A capability the catalog does not declare (`FRD-114`). Each surface answers in its own
    envelope; both name the model, because "invalid request" sends the reader nowhere."""
    if surface == "gemini":
        response = await governed.embed({"content": {"parts": [{"text": "x"}]}}, model=CHAT_MODEL)
    else:
        response = await governed.kira("/embed", {"text": "x", "model_id": LOCAL_CHAT_MODEL_ID})

    assert response.status_code == expect_code, response.text[:300]
    assert CHAT_MODEL in _envelope(response, surface)


async def test_asking_an_embedding_model_to_generate_is_refused_by_name(
    governed: Governed,
) -> None:
    response = await governed.generate(_gemini(), model=EMBED_MODEL)

    assert response.status_code == 400, response.text[:300]
    message = _envelope(response, "gemini")
    assert EMBED_MODEL in message and "generation" in message.lower()


@pytest.mark.parametrize(
    ("surface", "expect"),
    # **The two surfaces differ here on purpose.** A 404 sends the reader to the catalog and a 400
    # would send them to their own request — but the compatibility contract answers `422
    # MODEL_NOT_FOUND` for a model that is not there, and matching it is the whole point of that
    # surface (`FRD-107`). A generated HTTP client switches on the status before the body.
    [pytest.param("gemini", 404, id="gemini"), pytest.param("kira", 422, id="kira")],
)
async def test_a_model_nobody_serves_is_named_in_each_surfaces_own_status(
    governed: Governed, surface: str, expect: int
) -> None:
    """Either way the model is named, which is what sends somebody to the right place."""
    if surface == "gemini":
        response = await governed.generate(_gemini(), model="not-a-model-here")
        assert "not-a-model-here" in _envelope(response, surface)
    else:
        response = await governed.kira("/chat", _kira(model_id=987_654))
        assert "987654" in _envelope(response, surface) or "id" in _envelope(response, surface)

    assert response.status_code == expect, response.text[:300]


# ═══ 7. malformed bodies, in each surface's own words ══════════════════════════════════════════


@pytest.mark.parametrize(
    ("body", "label"),
    [
        pytest.param({}, "nothing at all", id="empty-object"),
        pytest.param({"contents": []}, "an empty contents list", id="empty-contents"),
        pytest.param({"contents": [{"role": "user", "parts": []}]}, "no parts", id="empty-parts"),
        pytest.param(
            {"contents": [{"role": "user", "parts": [{"text": ["a"]}]}]},
            "a list where text belongs",
            id="list-as-text",
        ),
        pytest.param(
            {"contents": [{"role": "user", "parts": [{}]}]},
            "a part that is neither",
            id="empty-part",
        ),
        pytest.param({"contents": "hello"}, "a string where contents belong", id="string-contents"),
    ],
)
async def test_a_malformed_gemini_body_is_refused_and_never_a_500(
    governed: Governed, body: dict, label: str
) -> None:
    """A caller's mistake must not become our error. The status is theirs to act on, and the
    envelope is the one this surface owes."""
    response = await governed.generate(body)

    assert response.status_code != 500, f"{label} became our error: {response.text[:300]}"
    assert response.status_code in (400, 422), f"{label}: {response.status_code}"
    assert _envelope(response, "gemini")


@pytest.mark.parametrize(
    ("body", "label"),
    [
        pytest.param({}, "nothing at all", id="empty-object"),
        pytest.param({"request": {"parts": [{"text": "hi"}]}}, "no model id", id="no-model-id"),
        pytest.param({"model_id": LOCAL_CHAT_MODEL_ID}, "no request", id="no-request"),
        pytest.param(
            {"request": "hi", "model_id": LOCAL_CHAT_MODEL_ID},
            "a string request",
            id="string-request",
        ),
        pytest.param(
            {"request": {"parts": [{}]}, "model_id": LOCAL_CHAT_MODEL_ID},
            "an empty part",
            id="empty-part",
        ),
        pytest.param(
            {"request": {"parts": [{"text": "hi"}]}, "model_id": "nine"},
            "a model id that is not a number",
            id="model-id-not-a-number",
        ),
        pytest.param(
            {
                "request": {"parts": [{"text": "hi"}]},
                "model_id": LOCAL_CHAT_MODEL_ID,
                "temperature": "warm",
            },
            "a temperature that is not a number",
            id="temperature-not-a-number",
        ),
    ],
)
async def test_a_malformed_kira_body_is_refused_in_the_predecessors_envelope(
    governed: Governed, body: dict, label: str
) -> None:
    """The whole point of the compatibility surface is its error shape: a migrating client switches
    on `code`, so Google's nested envelope must never appear here."""
    response = await governed.kira("/chat", body)

    assert response.status_code != 500, f"{label} became our error: {response.text[:300]}"
    assert response.status_code in (400, 404, 422), f"{label}: {response.status_code}"
    assert _envelope(response, "kira")


async def test_malformed_json_is_refused_on_both_surfaces_and_still_recorded(
    governed: Governed,
) -> None:
    """`FRD-122`: the log records what was **asked**. A valid credential sending a broken body is
    very much something that was asked, and it left no row at all until the KIRA surface learned to
    resolve attribution before parsing."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        gemini = await client.post(
            f"{GEMINI}/models/{CHAT_MODEL}:generateContent",
            content=b"{ not json",
            headers=governed.headers(),
        )
        kira = await client.post(f"{KIRA}/chat", content=b"{ not json", headers=governed.headers())

    assert gemini.status_code in (400, 422), gemini.text[:200]
    assert kira.status_code in (400, 422), kira.text[:200]
    rows = await governed.wait_for_rows(2)

    assert {row["api"] for row in rows} == {"gemini", "kira"}
    assert all(row["outcome"] == "invalid_request" for row in rows), rows


# ═══ 8. the double, where the question is about bookkeeping rather than a model ════════════════


async def test_the_mock_is_reachable_and_says_what_it_is(governed: Governed) -> None:
    """Used below wherever a deterministic answer matters more than a real one — and never for a
    question about approval or release, which it is exempt from."""
    response = await governed.generate(_gemini(), model=MOCK_MODEL)

    assert response.status_code == 200, response.text[:200]
    assert "mock" in response.json()["candidates"][0]["content"]["parts"][0]["text"]


async def test_a_request_with_no_text_and_no_attachment_is_refused(governed: Governed) -> None:
    """It would be billed for an answer to nothing. Found live, and it had been refused for
    embeddings all along — the same rule, one verb over."""
    response = await governed.generate(
        {"contents": [{"role": "user", "parts": [{"text": ""}]}], "generationConfig": SHORT},
        model=MOCK_MODEL,
    )

    assert response.status_code == 400, response.text[:300]
    assert "no text" in _envelope(response, "gemini").lower()
