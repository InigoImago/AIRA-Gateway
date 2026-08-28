"""What the API does with everything a caller can get wrong (live).

Roughly a hundred cases, and each one asserts three things rather than one:

1. **Never a 500.** A malformed request is the caller's mistake; a 500 makes it look like ours,
   invites a retry, and buries the real cause in a stack trace nobody outside the team can read.
   This is the stability claim, and it is asserted globally by :func:`_check` rather than
   remembered per case.
2. **A status a caller can act on.** 400 means "fix your request", 401 "fix your credential",
   404 "that does not exist", 429 "wait", 413 "send less". Collapsing those into one number is
   how an integration takes a week instead of an afternoon.
3. **A message that names the problem.** This is the half most suites skip. "Validation failed"
   is technically a correct answer and practically useless; a message that names the *field* is
   the difference between a two-minute fix and a support conversation.

The cases are grouped by what a caller is doing wrong, because that is how someone reading a
failure will look for them.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx
import pytest

from .conftest import GATEWAY_URL, LOCAL_CHAT_MODEL_ID, LOCAL_EMBED_MODEL_ID, Fixture

pytestmark = pytest.mark.integration

MODEL = "mock-1"
GENERATE = f"/v1beta/models/{MODEL}:generateContent"
KIRA = "/kira/api/external"

PDF = b"%PDF-1.7\n" + b"x" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 200


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


async def _post(
    fixture: Fixture, path: str, body: Any = None, *, headers: dict | None = None, **kwargs: Any
) -> httpx.Response:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=90.0) as client:
        return await client.post(
            path, json=body, headers={**fixture.headers(), **(headers or {})}, **kwargs
        )


def _check(response: httpx.Response, *, expect: tuple[int, ...], names: str = "") -> dict:
    """The three assertions every case makes.

    ``names`` is a lowercase fragment the message must contain — the field, the bound, the value.
    Left empty only where the useful message is the status itself (a 401 has nothing to name
    without telling an attacker which half of the credential was wrong).
    """
    assert response.status_code != 500, (
        f"a caller's mistake became our error: {response.text[:300]}"
    )
    assert response.status_code in expect, f"got {response.status_code}: {response.text[:300]}"

    if response.status_code == 200 or not response.content:
        return {}
    body = response.json()
    message = str(body.get("error", {}).get("message", "") or body.get("message", ""))
    assert message.strip(), "the refusal carries no message at all"
    if names:
        assert names.lower() in message.lower(), f"the message does not name {names!r}: {message}"
    return body


# == 1. the body itself ==========================================================================


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        (b"", "empty"),
        (b"not json at all", "prose"),
        (b"{", "truncated object"),
        (b"[]", "an array"),
        (b'"a string"', "a bare string"),
        (b"null", "null"),
        (b"123", "a number"),
        (b"{'single': 'quotes'}", "python-style quotes"),
    ],
)
async def test_a_body_that_is_not_a_request_object_is_refused(
    fixture: Fixture, raw: bytes, label: str
) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        response = await client.post(
            GENERATE,
            content=raw,
            headers={**fixture.headers(), "content-type": "application/json"},
        )
    _check(response, expect=(400, 422))


async def test_a_body_far_over_the_ceiling_is_refused_before_it_is_parsed(
    fixture: Fixture,
) -> None:
    """`ADR-0007`'s body ceiling. The point is that it is refused *before* buffering, so the
    protection does not depend on the parser being fast."""
    payload = {"contents": [{"role": "user", "parts": [{"text": "x" * 20_000_000}]}]}
    response = await _post(fixture, GENERATE, payload)
    _check(response, expect=(400, 413, 422))


async def test_a_content_type_the_route_does_not_speak_is_refused(fixture: Fixture) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        response = await client.post(
            GENERATE,
            content=b"contents=hello",
            headers={**fixture.headers(), "content-type": "application/x-www-form-urlencoded"},
        )
    _check(response, expect=(400, 415, 422))


# == 2. the request's shape ======================================================================


@pytest.mark.parametrize(
    ("body", "label"),
    [
        ({}, "no contents at all"),
        ({"contents": []}, "an empty contents list"),
        ({"contents": "hello"}, "contents as a string"),
        ({"contents": [{}]}, "a content with no parts"),
        ({"contents": [{"role": "user"}]}, "a role with no parts"),
        ({"contents": [{"role": "user", "parts": []}]}, "an empty parts list"),
        ({"contents": [{"role": "user", "parts": [{}]}]}, "a part that is neither"),
        ({"contents": [{"role": "user", "parts": "text"}]}, "parts as a string"),
        ({"contents": [{"role": "user", "parts": [{"text": None}]}]}, "a null text"),
        ({"contents": [{"role": "user", "parts": [{"text": 42}]}]}, "a numeric text"),
        ({"contents": [{"role": "user", "parts": [{"text": ["a"]}]}]}, "a list as text"),
        ({"contents": [{"role": "user", "parts": [{"text": "hi", "inlineData": {}}]}]}, "both"),
    ],
)
async def test_a_malformed_request_names_what_is_wrong(
    fixture: Fixture, body: dict, label: str
) -> None:
    """Every one of these is a 400 whose message points at a field. A validation error that says
    only "invalid" leaves the caller diffing their JSON against documentation."""
    response = await _post(fixture, GENERATE, body)
    _check(response, expect=(400, 422))


@pytest.mark.parametrize("role", ["", "assistant", "system", "USER", "robot", None])
async def test_an_unexpected_role_does_not_break_the_request(
    fixture: Fixture, role: str | None
) -> None:
    """Roles a caller might reasonably send. Google's own vocabulary is `user`/`model`, and
    anything else is read as `user` rather than refused — the alternative is a compatibility
    surface that rejects requests every other SDK considers valid."""
    content: dict[str, Any] = {"parts": [{"text": "hi"}]}
    if role is not None:
        content["role"] = role
    response = await _post(fixture, GENERATE, {"contents": [content]})
    _check(response, expect=(200, 400))


@pytest.mark.parametrize(
    "text_in",
    [
        "",
        " ",
        "\n\n\n",
        "\t",
        "hello" * 2000,
        "emoji 🎉🔥 and combining é",
        "日本語のテキスト",
        "line\nbreaks\rand\ttabs",
        "null byte: \x00 inside",
        "<script>alert(1)</script>",
        "'; DROP TABLE request_logs; --",
        "{{template}} ${injection} %s %d",
        "\\u0000\\uffff escaped",
        "a" * 100,
    ],
)
async def test_unusual_text_is_carried_rather_than_choking_the_gateway(
    fixture: Fixture, text_in: str
) -> None:
    """The gateway forwards text, it does not interpret it. Anything here that produced a 500
    would be a parsing bug on our side, and anything that produced a *different answer* would
    mean the text had been rewritten on the way through."""
    response = await _post(fixture, GENERATE, {"contents": [{"parts": [{"text": text_in}]}]})
    _check(response, expect=(200, 400))


async def test_a_very_deep_nesting_in_the_body_is_refused_not_recursed(fixture: Fixture) -> None:
    """Caller-controlled recursion. The bound has to be on our side, because the caller chooses
    the depth."""
    nested: Any = {"text": "deep"}
    for _ in range(2000):
        nested = {"parts": [nested]}
    response = await _post(fixture, GENERATE, {"contents": [nested]})
    _check(response, expect=(400, 413, 422))


# == 3. the model and the verb ===================================================================


@pytest.mark.parametrize(
    ("resource", "expect"),
    [
        ("nope-1:generateContent", (404,)),
        (":generateContent", (400, 404)),
        ("mock-1:", (400,)),
        ("mock-1", (400, 404, 405)),
        ("mock-1:GenerateContent", (400,)),
        ("mock-1:generatecontent", (400,)),
        ("mock-1:deleteEverything", (400,)),
        ("mock-1:generateContent:extra", (400, 404)),
        ("%2e%2e%2f%2e%2e%2fetc%2fpasswd:generateContent", (400, 404)),
        ("mock-1%00:generateContent", (400, 404)),
        ("a" * 500 + ":generateContent", (400, 404)),
        ("qwen3:0.6b:generateContent", (200, 400, 404, 502)),
    ],
)
async def test_the_resource_is_parsed_or_refused_but_never_guessed(
    fixture: Fixture, resource: str, expect: tuple[int, ...]
) -> None:
    """`model:method` is the whole addressing scheme, and a model name may itself contain a colon
    — which is how a real defect got in. These pin the parse against everything around it."""
    response = await _post(
        fixture, f"/v1beta/models/{resource}", {"contents": [{"parts": [{"text": "hi"}]}]}
    )
    _check(response, expect=expect)


async def test_an_unknown_model_says_which_one(fixture: Fixture) -> None:
    response = await _post(
        fixture,
        "/v1beta/models/no-such-model:generateContent",
        {"contents": [{"parts": [{"text": "hi"}]}]},
    )
    _check(response, expect=(404,), names="no-such-model")


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
async def test_the_wrong_http_method_is_refused_without_a_stack_trace(
    fixture: Fixture, method: str
) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        response = await client.request(method, GENERATE, headers=fixture.headers())
    assert response.status_code in (404, 405), response.text
    assert response.status_code != 500


# == 4. credentials ==============================================================================


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("x-goog-api-key", ""),
        ("x-goog-api-key", "not-a-key"),
        ("x-goog-api-key", "aira_"),
        ("x-goog-api-key", "aira_only_two"),
        ("x-goog-api-key", "aira_aa_bb_cc_dd"),
        ("x-goog-api-key", "aira_" + "f" * 8 + "_" + "0" * 48),
        ("x-goog-api-key", "aira_" + "x" * 5000),
        ("x-goog-api-key", "aira_%00_secret"),
        ("authorization", "Bearer not-a-token"),
        ("authorization", "Bearer"),
        ("authorization", "Basic dXNlcjpwYXNz"),
        ("authorization", "aira_abc_def"),
    ],
)
async def test_every_shape_of_bad_credential_is_a_401_and_nothing_else(
    header: str, value: str
) -> None:
    """A 401 and *only* a 401. A malformed credential that produced a 500 would be a parser
    reachable before authentication, which is the worst place to have one — and a 403 would leak
    that the credential was recognised."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        response = await client.post(
            GENERATE,
            json={"contents": [{"parts": [{"text": "hi"}]}]},
            headers={header: value, "content-type": "application/json"},
        )
    assert response.status_code == 401, f"got {response.status_code}: {response.text[:200]}"


async def test_a_valid_key_in_the_query_string_works_like_the_header(fixture: Fixture) -> None:
    """Google's own alternative. It must work, and `ADR-0007` requires the value to be redacted
    from spans — which is why it is worth having a case rather than assuming."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=90.0) as client:
        response = await client.post(
            f"{GENERATE}?key={fixture.key}",
            json={"contents": [{"parts": [{"text": "hi"}]}]},
        )
    _check(response, expect=(200,))


async def test_two_different_credentials_do_not_silently_pick_one(fixture: Fixture) -> None:
    """Whatever the precedence is, it must not be *the invalid one wins by accident*."""
    response = await _post(
        fixture,
        GENERATE,
        {"contents": [{"parts": [{"text": "hi"}]}]},
        headers={"authorization": "Bearer garbage"},
    )
    _check(response, expect=(200, 401))


# == 5. the use-case selector ====================================================================


@pytest.mark.parametrize(
    "selector",
    [
        "",
        "no-such-use-case",
        "../admin",
        "UPPER-CASE",
        "with space",
        "with/slash",
        "a" * 300,
        "sql'; --",
        "null",
        "%2e%2e%2f",
    ],
)
async def test_a_selector_that_is_not_this_keys_use_case_is_refused(
    fixture: Fixture, selector: str
) -> None:
    """A key carries its use case (`FRD-205`), so a selector never *grants* anything — it only
    chooses among what the caller already has. An invalid one must be a 400 or a 403, never a
    quiet re-attribution to somebody else's budget."""
    response = await _post(
        fixture,
        GENERATE,
        {"contents": [{"parts": [{"text": "hi"}]}]},
        headers={"X-AIRA-Use-Case": selector},
    )
    # An empty header is no header at all, so the key's own use case applies.
    _check(response, expect=(200,) if selector == "" else (400, 403))


# == 6. generation options =======================================================================


@pytest.mark.parametrize(
    ("config", "expect"),
    [
        ({"maxOutputTokens": 0}, (400,)),
        ({"maxOutputTokens": -1}, (400,)),
        ({"maxOutputTokens": -999999}, (400,)),
        ({"maxOutputTokens": 10**12}, (200, 400)),
        ({"maxOutputTokens": "64"}, (200, 400)),
        ({"maxOutputTokens": 1.5}, (400,)),
        ({"maxOutputTokens": None}, (200,)),
        ({"temperature": -5}, (200, 400)),
        ({"temperature": 99}, (200, 400)),
        ({"temperature": "hot"}, (400,)),
        # `FRD-124` **reversed** this. It read `(200,)` — a field nobody modelled was ignored, per
        # `FRD-100` FR-7, on the argument that real Gemini clients send extra keys. Measured, that
        # leniency cost more than it bought: of twelve fields a Google client can legitimately
        # send, eleven were accepted and silently dropped, and Google's own API is strict anyway.
        # Left in place with the new expectation rather than deleted, because the reversal is the
        # kind of thing a future reader should meet as a decision, not as an absence.
        ({"unknownFutureField": "value"}, (400,)),
        ({}, (200,)),
    ],
)
async def test_generation_options_are_bounded_or_ignored_but_never_crash(
    fixture: Fixture, config: dict, expect: tuple[int, ...]
) -> None:
    """Unknown fields are ignored on purpose (`FRD-100` FR-7): a real client sends keys we have
    not implemented yet, and refusing them would break every SDK upgrade. Known fields with
    impossible values are refused."""
    response = await _post(
        fixture, GENERATE, {"contents": [{"parts": [{"text": "hi"}]}], "generationConfig": config}
    )
    _check(response, expect=expect)


@pytest.mark.parametrize(
    ("thinking", "expect"),
    [
        ({"mode": "ludicrous"}, (400,)),
        ({"mode": ""}, (400,)),
        ({"mode": None}, (200, 400)),
        ({"mode": "LIMITED"}, (200, 400)),
        ({"mode": "limited"}, (400,)),
        ({"mode": "limited", "tokens": -5}, (400,)),
        ({"mode": "auto", "tokens": 500}, (400,)),
        ({"thinkingBudget": 0}, (200, 400)),
        ({"thinkingBudget": -1}, (200, 400)),
        ({"thinkingBudget": -99}, (200, 400)),
        ({"thinkingBudget": "many"}, (400,)),
        ({"thinkingBudget": 100, "mode": "auto"}, (400,)),
        ({}, (200,)),
    ],
)
async def test_thinking_settings_are_validated_against_the_model(
    fixture: Fixture, thinking: dict, expect: tuple[int, ...]
) -> None:
    """`mock-1` declares no thinking at all, so anything but "off" is refused — and the refusal
    has to say so rather than reporting a vendor error about a parameter the caller never saw."""
    response = await _post(
        fixture,
        GENERATE,
        {
            "contents": [{"parts": [{"text": "hi"}]}],
            "generationConfig": {"thinkingConfig": thinking},
        },
    )
    _check(response, expect=expect)


@pytest.mark.parametrize(
    ("schema", "expect"),
    [
        ("a string", (400,)),
        ([], (400,)),
        (123, (400,)),
        ({}, (400,)),
        ({"type": "OBJECT"}, (400,)),
        ({"type": "NOTATYPE"}, (400,)),
        ({"type": "OBJECT", "unknownKeyword": 1}, (400,)),
        ({"type": "OBJECT", "properties": {"a": {"type": "MYSTERY"}}}, (400,)),
        ({"type": "ARRAY"}, (400,)),
    ],
)
async def test_a_schema_is_parsed_at_our_boundary_not_forwarded_blindly(
    fixture: Fixture, schema: Any, expect: tuple[int, ...]
) -> None:
    """`mock-1` does not declare `structured_output`, so every one of these is refused — but the
    *reason* must be the caller's schema where the schema is the problem, so that fixing the
    catalog and fixing the request are distinguishable."""
    response = await _post(
        fixture,
        GENERATE,
        {"contents": [{"parts": [{"text": "hi"}]}], "generationConfig": {"responseSchema": schema}},
    )
    _check(response, expect=expect)


async def test_a_schema_nested_past_the_bound_names_the_bound(fixture: Fixture) -> None:
    deep: Any = {"type": "STRING"}
    for _ in range(60):
        deep = {"type": "ARRAY", "items": deep}
    response = await _post(
        fixture,
        GENERATE,
        {"contents": [{"parts": [{"text": "hi"}]}], "generationConfig": {"responseSchema": deep}},
    )
    _check(response, expect=(400,), names="nests deeper")


# == 7. attachments ==============================================================================


@pytest.mark.parametrize(
    ("inline", "label"),
    [
        ({"mimeType": "application/pdf", "data": "not base64!!"}, "invalid base64"),
        ({"mimeType": "application/pdf", "data": ""}, "empty data"),
        ({"mimeType": "application/pdf", "data": _b64(b"this is not a pdf")}, "wrong signature"),
        ({"mimeType": "application/x-msdownload", "data": _b64(PDF)}, "media type off the list"),
        ({"mimeType": "", "data": _b64(PDF)}, "no media type"),
        ({"mimeType": "application/pdf"}, "no data at all"),
        ({"data": _b64(PDF)}, "data with no type"),
        ({"mimeType": "image/png", "data": _b64(PDF)}, "png that is a pdf"),
        ({"mimeType": "application/pdf", "data": _b64(b"%PDF-" + b"x" * 50_000_000)}, "oversized"),
    ],
)
async def test_an_attachment_that_is_not_what_it_claims_is_refused(
    fixture: Fixture, inline: dict, label: str
) -> None:
    """The gateway does not parse documents — it decodes, counts and compares a few bytes
    (`FRD-110`). Every one of these must be a clean refusal, because this is the one place a
    caller hands us arbitrary binary."""
    response = await _post(fixture, GENERATE, {"contents": [{"parts": [{"inlineData": inline}]}]})
    _check(response, expect=(400, 413, 422))


async def test_a_model_that_cannot_read_an_attachment_refuses_by_name(fixture: Fixture) -> None:
    """The rule the whole document feature turns on: a model that cannot read the file is refused,
    never sent the prompt without it. A dropped attachment produces no error — it produces a
    fluent wrong answer with a 200 on it."""
    response = await _post(
        fixture,
        GENERATE,
        {
            "contents": [
                {
                    "parts": [
                        {"text": "what is in this"},
                        {"inlineData": {"mimeType": "application/pdf", "data": _b64(PDF)}},
                    ]
                }
            ]
        },
    )
    body = _check(response, expect=(400,))
    assert "attachment" in str(body).lower() or "pdf" in str(body).lower()


# == 8. embeddings ===============================================================================


@pytest.mark.parametrize(
    ("body", "expect"),
    [
        ({}, (400, 422)),
        ({"content": {}}, (400, 422)),
        ({"content": {"parts": []}}, (400, 422)),
        ({"content": {"parts": [{"text": ""}]}}, (400,)),
        ({"content": {"parts": [{"text": "   "}]}}, (400,)),
        ({"content": {"parts": [{"text": "ok"}], "role": "user"}}, (200,)),
        ({"content": {"parts": [{"text": "ok"}]}, "taskType": "NONSENSE"}, (400,)),
        ({"content": {"parts": [{"text": "ok"}]}, "taskType": "CLUSTERING"}, (400,)),
        ({"content": {"parts": [{"text": "ok"}]}, "outputDimensionality": 7}, (400,)),
        ({"content": {"parts": [{"text": "ok"}]}, "outputDimensionality": -1}, (400,)),
    ],
)
async def test_embedding_input_is_validated_before_anything_is_spent(
    fixture: Fixture, body: dict, expect: tuple[int, ...]
) -> None:
    response = await _post(fixture, f"/v1beta/models/{MODEL}:embedContent", body)
    _check(response, expect=expect)


@pytest.mark.parametrize(
    ("body", "expect"),
    [
        ({"requests": []}, (400, 422)),
        ({"requests": "many"}, (400, 422)),
        ({}, (400, 422)),
        ({"requests": [{"content": {"parts": [{"text": "a"}]}}]}, (200, 400)),
        (
            {
                "requests": [
                    {"content": {"parts": [{"text": "a"}]}, "taskType": "RETRIEVAL_QUERY"},
                    {"content": {"parts": [{"text": "b"}]}, "taskType": "CLUSTERING"},
                ]
            },
            (400,),
        ),
        (
            {"requests": [{"content": {"parts": [{"text": f"t{i}"}]}} for i in range(500)]},
            (400, 429),
        ),
    ],
)
async def test_batch_embedding_bounds_hold(
    fixture: Fixture, body: dict, expect: tuple[int, ...]
) -> None:
    """A batch is metered as the many requests it is, so its bounds are a control rather than
    politeness — an unbounded batch is a rate limit with a hole in it."""
    response = await _post(fixture, f"/v1beta/models/{MODEL}:batchEmbedContents", body)
    _check(response, expect=expect)


# == 9. the KIRA surface's own vocabulary ========================================================


@pytest.mark.parametrize(
    ("body", "expect"),
    [
        ({}, (422,)),
        ({"request": {"parts": [{"text": "hi"}]}}, (422,)),
        ({"model_id": 9999999}, (422,)),
        # `422`, the contract's own status for a model that is not there — it was `404` here and
        # written down as a deliberate deviation until the predecessor's suite was run against this
        # surface (2026-08-13).
        ({"request": {"parts": [{"text": "hi"}]}, "model_id": 9999999}, (422,)),
        ({"request": {"parts": [{"text": "hi"}]}, "model_id": "nine"}, (422,)),
        ({"request": {"parts": [{"text": "hi"}]}, "model_id": -1}, (404, 422)),
        ({"request": {"parts": []}, "model_id": LOCAL_CHAT_MODEL_ID}, (400, 404)),
        ({"request": {"parts": [{}]}, "model_id": LOCAL_CHAT_MODEL_ID}, (422,)),
        ({"request": "hi", "model_id": LOCAL_CHAT_MODEL_ID}, (422,)),
        (
            {
                "request": {"parts": [{"text": "hi"}]},
                "model_id": LOCAL_CHAT_MODEL_ID,
                "maxTokens": 0,
            },
            (422,),
        ),
        (
            {
                "request": {"parts": [{"text": "hi"}]},
                "model_id": LOCAL_CHAT_MODEL_ID,
                "maxTokens": -5,
            },
            (422,),
        ),
        (
            {
                "request": {"parts": [{"text": "hi"}]},
                "model_id": LOCAL_CHAT_MODEL_ID,
                "temperature": "warm",
            },
            (422,),
        ),
    ],
)
async def test_the_kira_surface_refuses_in_the_predecessors_shape(
    fixture: Fixture, body: dict, expect: tuple[int, ...]
) -> None:
    """A compatibility surface whose *errors* have a different shape is not one: a migrating
    client switches on the code, so every refusal carries `code` and `message` at the top level
    rather than Google's nested envelope."""
    response = await _post(fixture, f"{KIRA}/chat", body)
    assert response.status_code != 500, response.text[:300]
    assert response.status_code in expect, f"got {response.status_code}: {response.text[:200]}"
    payload = response.json()
    assert "code" in payload and "message" in payload, f"not the predecessor's envelope: {payload}"
    assert "error" not in payload, "Google's envelope leaked into the compatibility surface"


@pytest.mark.parametrize(
    ("body", "expect"),
    [
        ({"text": "", "model_id": LOCAL_EMBED_MODEL_ID}, (422,)),
        ({"text": [], "model_id": LOCAL_EMBED_MODEL_ID}, (422,)),
        ({"text": "ok", "model_id": LOCAL_EMBED_MODEL_ID, "task_type": "NONSENSE"}, (422,)),
        ({"text": 42, "model_id": LOCAL_EMBED_MODEL_ID}, (422,)),
        ({"model_id": LOCAL_EMBED_MODEL_ID}, (422,)),
        ({"text": "ok"}, (422,)),
    ],
)
async def test_kira_embedding_refuses_in_the_predecessors_shape(
    fixture: Fixture, body: dict, expect: tuple[int, ...]
) -> None:
    response = await _post(fixture, f"{KIRA}/embed", body)
    assert response.status_code != 500, response.text[:300]
    assert response.status_code in expect, f"got {response.status_code}: {response.text[:200]}"
    assert "code" in response.json()


async def test_a_list_is_joined_into_one_embedding_rather_than_many(
    fixture: Fixture,
) -> None:
    """A list is **one** embedding, its parts joined with nothing between them.

    Confirmed from the contract on 2026-08-12 after `FRD-113` §11 had assumed the other reading:
    a caller sending five chunks receives one vector, not five. Asserted as the property rather
    than as the status — `200` alone would also be true of a surface that embedded only the first
    element, dropped the rest, or embedded the literal list — so the vector is compared against the
    one the joined string produces on its own, which is the only thing that distinguishes joining
    from any of those.

    This used to be written with `["ok", ""]`, which made it *also* an assertion that a blank
    element is acceptable input. It is not, and the test below is why; the join is the same
    property with a part that carries something.
    """
    joined = await _post(
        fixture, f"{KIRA}/embed", {"text": ["ok", "!"], "model_id": LOCAL_EMBED_MODEL_ID}
    )
    plain = await _post(fixture, f"{KIRA}/embed", {"text": "ok!", "model_id": LOCAL_EMBED_MODEL_ID})

    assert joined.status_code == 200, joined.text[:300]
    assert plain.status_code == 200, plain.text[:300]
    first, second = joined.json()["vector"], plain.json()["vector"]

    assert len(first) == len(second)
    assert first == pytest.approx(second, abs=1e-6), "the parts are not being joined into one text"


async def test_a_blank_element_in_a_list_is_refused_rather_than_absorbed(
    fixture: Fixture,
) -> None:
    """The join is exactly what makes this dangerous: an empty element disappears into it.

    `["ok", ""]` embedded identically to `["ok"]`, answered 200, and nothing told the caller that
    one of their chunks was empty — a silent drop, in the one place a caller cannot notice it,
    since the vector looks perfectly normal. The contract refuses blank entries and so does this
    surface now. Whitespace counts: three spaces contribute nothing to a vector either.
    """
    for text in (["ok", ""], ["ok", "   "], [""], []):
        response = await _post(
            fixture, f"{KIRA}/embed", {"text": text, "model_id": LOCAL_EMBED_MODEL_ID}
        )

        assert response.status_code == 422, f"{text!r}: {response.text[:200]}"
        assert response.json()["code"], response.text[:200]


async def test_the_kira_surface_announces_itself_as_transitional_even_when_refusing(
    fixture: Fixture,
) -> None:
    """A deprecation header on the happy path only would be missed by exactly the clients most
    likely to still be using the surface a year from now."""
    response = await _post(fixture, f"{KIRA}/chat", {})
    assert response.headers.get("Deprecation") == "true"


# == 10. streaming ===============================================================================


async def test_a_stream_that_the_client_abandons_does_not_take_the_gateway_with_it(
    fixture: Fixture,
) -> None:
    """The integration layer found a real defect here once: a real socket drop cancels the
    response task, and a bare await in the streaming `finally` lost the settle and the audit row."""
    url = f"/v1beta/models/{MODEL}:streamGenerateContent?alt=sse"
    async with (
        httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client,
        client.stream(
            "POST",
            url,
            json={"contents": [{"parts": [{"text": "hi"}]}]},
            headers=fixture.headers(),
        ) as response,
    ):
        assert response.status_code == 200
        async for _ in response.aiter_bytes():
            break  # hang up after the first chunk

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        assert (await client.get("/readyz")).status_code == 200


@pytest.mark.parametrize("query", ["", "?alt=sse", "?alt=json", "?alt=nonsense", "?alt="])
async def test_the_stream_format_selector_never_breaks_the_response(
    fixture: Fixture, query: str
) -> None:
    url = f"/v1beta/models/{MODEL}:streamGenerateContent{query}"
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=90.0) as client:
        response = await client.post(
            url, json={"contents": [{"parts": [{"text": "hi"}]}]}, headers=fixture.headers()
        )
    assert response.status_code == 200, response.text[:200]
    assert response.content, "the stream produced nothing at all"


# == 11. it stays up ==============================================================================


async def test_a_burst_of_malformed_requests_leaves_the_gateway_healthy(
    fixture: Fixture,
) -> None:
    """Stability under abuse rather than under load: fifty bad requests at once, then the
    readiness probe. A parser that leaked a connection or a task per failure would show here."""
    bodies: list[Any] = [
        {},
        {"contents": []},
        {"contents": [{"parts": [{}]}]},
        "string",
        [1, 2, 3],
        None,
        {"contents": [{"parts": [{"text": None}]}]},
        {"generationConfig": {"maxOutputTokens": -1}},
    ]
    await asyncio.gather(
        *[_post(fixture, GENERATE, bodies[i % len(bodies)]) for i in range(50)],
        return_exceptions=True,
    )

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


async def test_the_gateway_still_serves_a_good_request_after_all_of_that(
    fixture: Fixture,
) -> None:
    """The assertion that makes the rest of this file mean something."""
    response = await _post(fixture, GENERATE, {"contents": [{"parts": [{"text": "still here?"}]}]})
    _check(response, expect=(200,))


async def test_health_and_readiness_need_no_credential_and_say_what_they_know(
    fixture: Fixture,
) -> None:
    """A probe that required a credential would be a probe that fails for the wrong reason on the
    day the credential expires."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        health = await client.get("/healthz")
        ready = await client.get("/readyz")

    assert health.status_code == 200
    assert ready.status_code == 200
    body = ready.json()
    # Degradation is reported rather than hidden — "we did not look" and "nothing is wrong" are
    # different answers, and only one of them is safe to act on.
    assert "degraded" in body and "checks" in body
