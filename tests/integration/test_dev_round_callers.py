"""How a caller actually reaches the gateway: credentials, selectors, conversations, and abuse.

The other files in this round drive one shape of request and vary the *policy*. This one holds the
policy still and varies the **caller** — the three ways a credential can arrive, the two ways a use
case can be named, a conversation rather than a single turn, and the input a real client eventually
sends by accident: an enormous body, a deeply nested one, control characters, four hundred
kilobytes of emoji.

Every case makes the three claims `test_edge_cases.py` established, because they are what separates
a governed gateway from one that merely works: **never a 500**, a status the caller can act on, and
a message that names the problem. A caller's mistake becoming our error is the failure that turns a
support question into an incident.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from .conftest import GATEWAY_URL, LOCAL_CHAT_MODEL_ID
from .governed import CHAT_MODEL, EMBED_MODEL, GEMINI, KIRA, MOCK_MODEL, Governed

pytestmark = pytest.mark.integration

SHORT = {"maxOutputTokens": 8}


def _body(text: str = "Say OK.", **config: object) -> dict:
    return {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {**SHORT, **config},
    }


def _kira_body(text: str = "Say OK.", **fields: object) -> dict:
    return {
        "request": {"parts": [{"text": text}]},
        "model_id": LOCAL_CHAT_MODEL_ID,
        "maxTokens": 8,
        **fields,
    }


def _never_500(response: httpx.Response, label: str) -> None:
    assert response.status_code != 500, f"{label} became our error: {response.text[:300]}"


# ═══ 1. the three ways a credential arrives ════════════════════════════════════════════════════


@pytest.mark.parametrize("how", ["header", "query", "bearer-style-header"])
async def test_a_key_is_accepted_however_the_google_protocol_spells_it(
    governed: Governed, how: str
) -> None:
    """The SDK sends `x-goog-api-key`; the documented curl uses `?key=`. A gateway that took only
    one of them would refuse the client its own documentation tells people to use."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=90.0) as client:
        if how == "header":
            response = await client.post(
                f"{GEMINI}/models/{MOCK_MODEL}:generateContent",
                json=_body(),
                headers=governed.headers(),
            )
        elif how == "query":
            response = await client.post(
                f"{GEMINI}/models/{MOCK_MODEL}:generateContent?key={governed.key}",
                json=_body(),
                headers={"content-type": "application/json"},
            )
        else:
            response = await client.post(
                f"{GEMINI}/models/{MOCK_MODEL}:generateContent",
                json=_body(),
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {governed.key}",
                },
            )

    assert response.status_code == 200, f"{how}: {response.text[:300]}"


async def test_a_credential_in_the_query_string_never_reaches_the_stored_payload(
    governed: Governed,
) -> None:
    """`?key=` is redacted out of spans and out of the access log. The audit row is the third place
    it could land, and it is the one that is kept for months."""
    await governed.set_flag("store_payloads", True)
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=90.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{MOCK_MODEL}:generateContent?key={governed.key}",
            json=_body(),
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 200, response.text[:200]
    row = await governed.last_row()

    assert governed.key not in str(row), "the credential was written into the audit row"


# ═══ 2. naming a use case ══════════════════════════════════════════════════════════════════════


async def test_a_bound_key_needs_no_selector_at_all(governed: Governed) -> None:
    """A key issued by Management belongs to one use case, so a client normally sends nothing
    else — which is the whole reason the selector is optional."""
    assert (await governed.generate(_body(), model=MOCK_MODEL)).status_code == 200
    assert (await governed.last_row())["use_case"] == governed.slug


@pytest.mark.parametrize("surface", ["gemini", "kira"])
async def test_the_path_selector_reaches_the_same_use_case(
    governed: Governed, surface: str
) -> None:
    """`/uc/<slug>` is the other way in (`FRD-102`). It chooses among what the caller already has
    and never grants anything.

    **Both surfaces, because for a long time it was one.** The middleware is mounted before every
    route and the Gemini routes read the scope it writes; the KIRA surface rewrote the header half
    by hand and never looked at the path, so the prefix was invisible there — on the surface a
    migrating client uses, which is the one whose base URL is configurable and whose headers often
    are not.

    Note what this case cannot prove on its own: a **bound** key carries its use case, so the
    request succeeds whether or not the selector was read. That is exactly how the gap survived a
    check — it was verified with the one credential that makes the selector unnecessary. The
    property is proved in `gateway/tests/test_kira_use_case_selector.py`, where the caller holds
    two memberships and the selector is the only thing that can decide between them; this case is
    here so the *live* path is exercised too.
    """
    path = (
        f"/uc/{governed.slug}{GEMINI}/models/{MOCK_MODEL}:generateContent"
        if surface == "gemini"
        else f"/uc/{governed.slug}{KIRA}/chat"
    )
    body = (
        _body()
        if surface == "gemini"
        else {
            "request": {"parts": [{"text": "hi"}]},
            "model_id": LOCAL_CHAT_MODEL_ID,
            "maxTokens": 8,
        }
    )
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=90.0) as client:
        response = await client.post(path, json=body, headers=governed.headers())

    assert response.status_code == 200, response.text[:300]
    row = await governed.last_row()
    assert row["use_case"] == governed.slug
    assert row["api"] == surface


async def test_the_header_selector_reaches_the_same_use_case(governed: Governed) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=90.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{MOCK_MODEL}:generateContent",
            json=_body(),
            headers=governed.headers(**{"X-AIRA-Use-Case": governed.slug}),
        )

    assert response.status_code == 200, response.text[:300]


@pytest.mark.parametrize(
    "selector",
    ["../etc/passwd", "UPPERCASE", "a" * 200, "with spaces", "semi;colon", "slash/slash"],
)
async def test_a_selector_that_is_not_a_slug_is_refused_before_it_reaches_anything(
    governed: Governed, selector: str
) -> None:
    """Client input that would otherwise reach the audit log, the read-model lookups and the trace
    attributes (`ADR-0007`). Bounded to the same charset and length as a Management slug."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{MOCK_MODEL}:generateContent",
            json=_body(),
            headers=governed.headers(**{"X-AIRA-Use-Case": selector}),
        )

    _never_500(response, f"selector {selector!r}")
    assert response.status_code in (400, 403), f"{selector!r}: {response.status_code}"


async def test_a_blank_selector_is_no_selector_rather_than_a_bad_one(
    governed: Governed,
) -> None:
    """An empty header is a client sending an unset variable, not an attempt to name something. It
    falls through to what the key is bound to, which is the only reading that does not break a
    caller whose template rendered nothing — and it is deliberately *not* in the list above, where
    it would have asserted a refusal the gateway is right not to give.

    Only the empty string, not a whitespace one: `httpx` refuses to put `"   "` on the wire at all
    (`Illegal header value`), so that case cannot reach the gateway over HTTP and a test for it
    would be a test of the client library.
    """
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=90.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{MOCK_MODEL}:generateContent",
            json=_body(),
            headers=governed.headers(**{"X-AIRA-Use-Case": ""}),
        )

    assert response.status_code == 200, response.text[:300]
    assert (await governed.last_row())["use_case"] == governed.slug


async def test_the_header_wins_over_the_path(governed: Governed, second_governed: Governed) -> None:
    """Documented precedence (`FRD-102`), and worth a test because the two disagreeing silently
    would attribute traffic to whichever the implementation happened to read first."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        response = await client.post(
            f"/uc/{governed.slug}{GEMINI}/models/{MOCK_MODEL}:generateContent",
            json=_body(),
            headers=governed.headers(**{"X-AIRA-Use-Case": second_governed.slug}),
        )

    # The header names a use case this key is not bound to, so the request is refused — which is
    # only possible if the header was the one that was read.
    assert response.status_code == 403, response.text[:300]


# ═══ 3. conversations ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("turns", [1, 3, 7])
async def test_a_multi_turn_conversation_is_carried(governed: Governed, turns: int) -> None:
    """A chat client sends the whole history every time. What is asserted is that it is *served* —
    not what the model made of it."""
    contents = []
    for index in range(turns):
        contents.append({"role": "user", "parts": [{"text": f"turn {index}"}]})
        if index < turns - 1:
            contents.append({"role": "model", "parts": [{"text": f"answer {index}"}]})

    response = await governed.generate(
        {"contents": contents, "generationConfig": SHORT}, model=MOCK_MODEL
    )

    assert response.status_code == 200, response.text[:300]


async def test_the_kira_surface_carries_its_own_conversation_shape(governed: Governed) -> None:
    """The predecessor spells a history differently — `{content, role}` rather than Google's
    `{role, parts}` — and a compatibility surface that took Google's shape would refuse the very
    clients it exists for."""
    response = await governed.kira(
        "/chat",
        _kira_body(
            conversation_history=[
                {"role": "user", "content": {"parts": [{"text": "hi"}]}},
                {"role": "model", "content": {"parts": [{"text": "hello"}]}},
            ]
        ),
    )

    assert response.status_code == 200, response.text[:300]


async def test_a_system_instruction_is_carried_on_both_surfaces(governed: Governed) -> None:
    gemini = await governed.generate(
        {
            "systemInstruction": {"parts": [{"text": "Answer in one word."}]},
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "generationConfig": SHORT,
        },
        model=MOCK_MODEL,
    )
    kira = await governed.kira(
        "/chat", _kira_body(system_instruction={"parts": [{"text": "Answer in one word."}]})
    )

    assert gemini.status_code == 200, gemini.text[:200]
    assert kira.status_code == 200, kira.text[:200]


async def test_several_text_parts_in_one_turn_are_carried(governed: Governed) -> None:
    """A caller may split one message into parts. Dropping the second would produce an answer to
    half a question, with a 200."""
    response = await governed.generate(
        {
            "contents": [{"role": "user", "parts": [{"text": "Say "}, {"text": "OK."}]}],
            "generationConfig": SHORT,
        },
        model=MOCK_MODEL,
    )

    assert response.status_code == 200, response.text[:300]


# ═══ 4. input a real client eventually sends by accident ═══════════════════════════════════════


@pytest.mark.parametrize(
    ("text", "label"),
    [
        pytest.param("héllo wörld — ünïcödé", "accented text", id="accents"),
        pytest.param("🎉" * 200, "emoji", id="emoji"),
        pytest.param("日本語のテキスト", "a non-latin script", id="cjk"),
        pytest.param("​​ zero width", "zero-width characters", id="zero-width"),
        pytest.param("line\nbreak\ttab", "control whitespace", id="whitespace"),
        pytest.param("<script>alert(1)</script>", "markup", id="markup"),
        pytest.param("'; DROP TABLE request_logs; --", "an injection attempt at us", id="sql"),
        pytest.param("{{template}} ${var} %s", "template syntax", id="templates"),
    ],
)
async def test_unusual_text_is_carried_rather_than_choking_the_gateway(
    governed: Governed, text: str, label: str
) -> None:
    """None of these is special to a model; all of them are special to something between the caller
    and it — a JSON encoder, a log formatter, a JSONB column. The last two are aimed at *us*."""
    response = await governed.generate(_body(text), model=MOCK_MODEL)

    _never_500(response, label)
    assert response.status_code == 200, f"{label}: {response.text[:300]}"


async def test_a_prompt_that_looks_like_sql_is_stored_without_incident(
    governed: Governed,
) -> None:
    """The payload column is JSONB and the query is parameterised, so this can only ever be text.
    Asserted end to end because "it is parameterised" is a claim about code and this is evidence."""
    await governed.set_flag("store_payloads", True)
    marker = "'; DROP TABLE request_logs; --"

    assert (await governed.generate(_body(marker), model=MOCK_MODEL)).status_code == 200
    row = await governed.last_row()

    assert marker in str(row["request_payload"]), row["request_payload"]


@pytest.mark.parametrize("size", [10_000, 100_000])
async def test_a_large_but_legal_body_is_served(governed: Governed, size: int) -> None:
    """Under the ceiling, so it is ordinary traffic. A gateway that fell over here would be one
    nobody could send a document-sized prompt to."""
    response = await governed.generate(_body("x" * size), model=MOCK_MODEL)

    _never_500(response, f"{size} characters")
    assert response.status_code == 200, response.text[:200]


async def test_a_body_far_over_the_ceiling_is_refused_before_it_is_parsed(
    governed: Governed,
) -> None:
    """`AIRA_MAX_REQUEST_BYTES`, enforced in pure ASGI before any route — which is why it once left
    no trace at all, and why both exits now record through one function under
    `request_too_large`."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=90.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{MOCK_MODEL}:generateContent",
            json=_body("x" * 25_000_000),
            headers=governed.headers(),
        )

    _never_500(response, "a 25 MB body")
    assert response.status_code == 413, response.text[:200]


async def test_a_deeply_nested_body_is_refused_rather_than_recursed(
    governed: Governed,
) -> None:
    """A parser that recurses on caller input is a stack overflow with a queue in front of it."""
    nested: object = {"text": "deep"}
    for _ in range(600):
        nested = {"parts": [nested]}

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=90.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{MOCK_MODEL}:generateContent",
            json={"contents": [nested], "generationConfig": SHORT},
            headers=governed.headers(),
        )

    _never_500(response, "600 levels of nesting")
    assert response.status_code in (400, 413, 422), response.status_code


@pytest.mark.parametrize(
    ("path", "label"),
    [
        pytest.param(f"{GEMINI}/models/{MOCK_MODEL}:noSuchVerb", "an invented verb", id="verb"),
        pytest.param(f"{GEMINI}/nonsense", "a path that is not a route", id="not-a-route"),
        pytest.param(f"{KIRA}/nonsense", "the same on the compatibility surface", id="kira-path"),
        pytest.param(
            f"{GEMINI}/models/%2e%2e%2f%2e%2e/etc", "an encoded traversal", id="traversal"
        ),
    ],
)
async def test_an_unroutable_path_answers_in_the_shape_of_the_api_it_was_aimed_at(
    governed: Governed, path: str, label: str
) -> None:
    """A caller who has not found the right route is the one least equipped to deal with an
    unfamiliar error shape, so the envelope has to be the surface's own."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        response = await client.post(path, json=_body(), headers=governed.headers())

    _never_500(response, label)
    assert response.status_code in (400, 404, 405), f"{label}: {response.status_code}"
    body = response.json()
    expected = "code" if path.startswith(KIRA) else "error"
    assert expected in body, f"{label} answered in somebody else's envelope: {body}"


@pytest.mark.parametrize(
    ("params", "label"),
    [
        pytest.param({"alt": "nonsense"}, "an unknown alt", id="alt"),
        pytest.param({"key": ""}, "an empty key parameter", id="empty-key"),
    ],
)
async def test_a_bad_query_parameter_is_answered_in_this_apis_words(
    governed: Governed, params: dict, label: str
) -> None:
    """The framework answers its own `422`/`detail` shape, which a Google client reads as "unknown
    error". Each surface renders it in its own envelope instead."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{MOCK_MODEL}:generateContent",
            params=params,
            json=_body(),
            headers=governed.headers(),
        )

    _never_500(response, label)
    if response.status_code >= 400:
        assert "error" in response.json(), f"{label}: {response.text[:200]}"


# ═══ 5. what every response carries ════════════════════════════════════════════════════════════


async def test_every_kira_response_announces_that_the_surface_is_transitional(
    governed: Governed,
) -> None:
    """On the happy path *and* on a refusal — a deprecation header only on success would be missed
    by exactly the clients most likely to still be here in a year."""
    served = await governed.kira("/chat", _kira_body())
    refused = await governed.kira("/chat", {})

    for label, response in (("served", served), ("refused", refused)):
        assert response.headers.get("Deprecation") == "true", label
        assert 'rel="deprecation"' in response.headers.get("Link", ""), label


@pytest.mark.parametrize("header", ["x-content-type-options", "x-frame-options"])
async def test_security_headers_are_on_an_ordinary_answer(governed: Governed, header: str) -> None:
    response = await governed.generate(_body(), model=MOCK_MODEL)

    assert response.headers.get(header), dict(response.headers)


async def test_security_headers_are_on_a_refusal_too(governed: Governed) -> None:
    """The two responses that answer without reaching a route carried none. A header that is only
    on the happy path protects the requests that were going to be fine anyway."""
    response = await governed.generate({"contents": []})

    assert response.status_code in (400, 422)
    assert response.headers.get("x-content-type-options"), dict(response.headers)


# ═══ 6. several callers at once ════════════════════════════════════════════════════════════════


async def test_both_surfaces_can_be_used_at_the_same_time(governed: Governed) -> None:
    """Nothing in the shared sequence may be per-process state that two in-flight requests share.
    Ten at once across two wire formats, and every one of them recorded."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=120.0) as client:

        async def gemini() -> int:
            response = await client.post(
                f"{GEMINI}/models/{MOCK_MODEL}:generateContent",
                json=_body(),
                headers=governed.headers(),
            )
            return response.status_code

        async def kira() -> int:
            response = await client.post(
                f"{KIRA}/chat", json=_kira_body(), headers=governed.headers()
            )
            return response.status_code

        statuses = await asyncio.gather(*[gemini() for _ in range(5)], *[kira() for _ in range(5)])

    assert statuses == [200] * 10, statuses
    rows = await governed.wait_for_rows(10, timeout=40.0)
    assert len([row for row in rows if row["outcome"] == "served"]) >= 10, len(rows)


async def test_concurrent_embeddings_are_each_recorded(governed: Governed) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=120.0) as client:

        async def one(index: int) -> int:
            response = await client.post(
                f"{GEMINI}/models/{EMBED_MODEL}:embedContent",
                json={"content": {"parts": [{"text": f"text {index}"}]}},
                headers=governed.headers(),
            )
            return response.status_code

        statuses = await asyncio.gather(*(one(index) for index in range(6)))

    assert statuses == [200] * 6, statuses
    assert len(await governed.wait_for_rows(6, timeout=40.0)) >= 6


# ═══ 7. embedding options ══════════════════════════════════════════════════════════════════════


async def test_an_output_dimensionality_the_model_never_declared_is_refused(
    governed: Governed,
) -> None:
    """The predecessor makes width part of the model's *identity* — two ids for one model differing
    only in it — so a request that asks for one this catalog entry does not declare is refused
    rather than answered at a different width than was asked for."""
    response = await governed.embed(
        {"content": {"parts": [{"text": "x"}]}, "outputDimensionality": 64}
    )

    assert response.status_code == 400, response.text[:300]
    assert "DIMENSION" in response.json()["error"]["message"].upper()


@pytest.mark.parametrize("task_type", ["RETRIEVAL_DOCUMENT", "SEMANTIC_SIMILARITY"])
async def test_a_task_type_the_model_never_declared_is_refused_on_the_gemini_surface(
    governed: Governed, task_type: str
) -> None:
    """Undeclared means unsupported, and sending it anyway would be a field the endpoint ignores —
    an answer that is subtly not the one that was asked for."""
    response = await governed.embed({"content": {"parts": [{"text": "x"}]}, "taskType": task_type})

    assert response.status_code == 400, response.text[:300]


async def test_an_embedding_of_the_same_text_is_stable(governed: Governed) -> None:
    """Not a claim about the model — a claim about us. Two identical requests that produced
    different vectors would mean something between the caller and the model was varying."""
    first = await governed.embed({"content": {"parts": [{"text": "stability"}]}})
    second = await governed.embed({"content": {"parts": [{"text": "stability"}]}})

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["embedding"]["values"] == pytest.approx(
        second.json()["embedding"]["values"], abs=1e-6
    )


async def test_two_different_texts_embed_differently(governed: Governed) -> None:
    """The control for the test above: if the surface returned a constant, both would pass."""
    first = await governed.embed({"content": {"parts": [{"text": "governance"}]}})
    second = await governed.embed({"content": {"parts": [{"text": "elephant"}]}})

    assert first.json()["embedding"]["values"] != second.json()["embedding"]["values"]


# ═══ 8. the model listing as a client reads it ═════════════════════════════════════════════════


async def test_a_named_model_can_be_fetched_on_its_own(governed: Governed) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get(f"{GEMINI}/models/{CHAT_MODEL}", headers=governed.headers())

    assert response.status_code == 200, response.text[:300]
    assert response.json()["name"].removeprefix("models/") == CHAT_MODEL


async def test_the_listing_says_which_verbs_each_model_supports(governed: Governed) -> None:
    """A client picks an endpoint from this. A model listed without its verbs is one a client has
    to guess about."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get(f"{GEMINI}/models", headers=governed.headers())

    models = {m["name"].removeprefix("models/"): m for m in response.json()["models"]}
    assert "generateContent" in models[CHAT_MODEL]["supportedGenerationMethods"]
    assert "embedContent" in models[EMBED_MODEL]["supportedGenerationMethods"]
