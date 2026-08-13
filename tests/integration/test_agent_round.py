"""A developer round over the agent surface, live (`FRD-131`, `FRD-502`).

The other suites prove the happy path works and that a role sees what it should. This one walks
the edges of the two things that landed today — **tool calls in the audit row** and **the filters
an incident uses** — with the same three claims `test_edge_cases.py` makes: never a 500, a status
the caller can act on, and a message that names the problem.

Two properties are load-bearing enough to be asserted from more than one direction:

- **A filter narrows.** Combined, contradictory, malformed or absurd, no parameter may return a row
  the caller could not already see. This is the property a *new* parameter is most likely to break,
  because each one is written next to the last and copied from it.
- **Arguments are not metadata.** The audit row records that `read_file` was called; what it was
  called *with* is the caller's content, and `FRD-406` governs content. A column that started
  carrying arguments would leak the contents of a repository into a list any oversight role reads.

Nothing here asserts an answer's content — that tests the model, and flakes.
"""

from __future__ import annotations

import pathlib

import httpx
import pytest

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration

MODEL = "qwen3:0.6b"
#: A model **measured** to emit real tool calls (`FRD-131`). Not the one `make showcase` and CI
#: pull — those take the two smallest models there are, on purpose — so a stack that serves the
#: rest of this file may well not serve this one.
TOOL_MODEL = "qwen2.5:3b"
SHORT = {"generationConfig": {"maxOutputTokens": 8}}

WEATHER = {
    "functionDeclarations": [
        {
            "name": "get_weather",
            "description": "Current weather for a city",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        }
    ]
}


@pytest.fixture
async def tool_model(fixture) -> str:
    """Skip unless this stack actually serves :data:`TOOL_MODEL`.

    **A precondition stated once, because it was stated once out of five times.** The guard existed
    — an inline `if response.status_code == 404: pytest.skip(...)` in a single test — and the other
    four asked the same model on a stack that does not serve it and failed with
    `404 Model 'qwen2.5:3b' not found`. That reads as a broken gateway; it means somebody chose not
    to pull a 2 GB model for four tests.

    A fixture rather than another inline `if`, and it asks **before** the request rather than
    interpreting the answer: a 404 can also mean the model was retired, mistyped, or never
    catalogued, and a skip that swallows those hides the failures worth seeing. Requesting the
    fixture is also visible in a signature, which is what
    :func:`test_every_tool_model_test_states_that_it_needs_one` can check — an inline skip is not.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{GATEWAY_URL}/v1beta/models", headers=fixture.headers())
    response.raise_for_status()
    served = {model.get("name", "").removeprefix("models/") for model in response.json()["models"]}
    if TOOL_MODEL not in served:
        pytest.skip(
            f"this stack does not serve {TOOL_MODEL}; it serves {sorted(served)}. "
            f"Pull it with: docker exec aira-ollama ollama pull {TOOL_MODEL}"
        )
    return TOOL_MODEL


def test_every_tool_model_test_states_that_it_needs_one() -> None:
    """A test that reaches for `TOOL_MODEL` without the fixture fails instead of skipping — which
    is how four of the five came to report a missing model as a broken gateway.

    Parsed rather than trusted: the whole reason this file needed fixing is that the rule was
    applied where somebody remembered it.
    """
    import ast

    source = pathlib.Path(__file__).read_text()
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith("test"):
            continue
        uses = any(
            isinstance(inner, ast.Name) and inner.id == "TOOL_MODEL" for inner in ast.walk(node)
        )
        if uses and "tool_model" not in {argument.arg for argument in node.args.args}:
            offenders.append(node.name)

    assert not offenders, (
        f"these ask for TOOL_MODEL without requesting the `tool_model` fixture: {offenders}. "
        "On a stack that does not serve it they fail with a 404 that reads as a broken gateway."
    )


async def _traces(client: httpx.AsyncClient, token: str, **params) -> httpx.Response:
    return await client.get(
        f"{GATEWAY_URL}/v1beta/traces",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30.0,
    )


def _check(response: httpx.Response, *allowed: int) -> dict:
    """Never a 500, and one of the statuses this case is allowed to answer with."""
    assert response.status_code != 500, (
        f"a 500 makes the caller's mistake look like ours: {response.text}"
    )
    assert response.status_code in allowed, f"{response.status_code}: {response.text}"
    return response.json()


# ═══ 1. a filter narrows, whatever it is handed ════════════════════════════════════════════════


@pytest.mark.parametrize(
    "params",
    [
        {"mine": "true", "subject": "somebody-else"},
        {"credential": "no-such-prefix"},
        {"subject": ""},
        {"credential": "", "mine": "false"},
        {"tools_only": "true", "refusals_only": "true"},
        {"outcome": "served", "refusals_only": "true"},
    ],
    ids=[
        "mine-and-somebody-else",
        "unknown-credential",
        "empty-subject",
        "empty-and-off",
        "tool-turns-that-were-refused",
        "served-and-refusals",
    ],
)
async def test_a_filter_combination_is_answered_and_never_widens(governance_token, params) -> None:
    """`mine` plus another subject is a contradiction, not a request for both — an OR here would
    hand somebody else's traffic to a reader who asked for their own."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        body = _check(await _traces(client, governance_token, **params), 200)

    assert isinstance(body["traces"], list)
    if params.get("mine") == "true" and params.get("subject"):
        assert body["traces"] == [], "two identity filters were treated as alternatives"
    if params.get("outcome") == "served" and params.get("refusals_only") == "true":
        assert body["traces"] == [], "'served' and 'refusals only' cannot both be satisfied"


@pytest.mark.parametrize(
    "params, allowed",
    [
        ({"limit": "0"}, (200, 400)),
        ({"limit": "-1"}, (400,)),
        ({"limit": "100000"}, (400,)),
        ({"limit": "not-a-number"}, (400,)),
        ({"outcome": "definitely-not-an-outcome"}, (200, 400)),
        ({"credential": "x" * 500}, (400,)),
        ({"subject": "x" * 5000}, (400,)),
        ({"cursor": "1"}, (400,)),
        ({"cursor": "|"}, (400,)),
        ({"tools_only": "perhaps"}, (400,)),
    ],
    ids=[
        "limit-zero",
        "limit-negative",
        "limit-enormous",
        "limit-not-a-number",
        "outcome-outside-the-vocabulary",
        "credential-past-the-bound",
        "subject-far-past-the-bound",
        "cursor-half-formed",
        "cursor-empty-halves",
        "boolean-that-is-not-one",
    ],
)
async def test_a_malformed_parameter_is_answered_rather_than_crashed(
    governance_token, params, allowed
) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await _traces(client, governance_token, **params)

    body = _check(response, *allowed)
    if response.status_code >= 400:
        # 400, in this API's envelope — never the framework's `422` + `detail` list, which a Google
        # client reads as "unknown error" (fixed 2026-08-08, found by `limit=100000`).
        assert "error" in body, f"the framework's own error shape reached a caller: {body}"
        # The half most suites skip: the message has to name what is wrong, or the caller is
        # guessing which of six parameters the server disliked.
        message = str(body).lower()
        assert any(key in message for key in params), f"the error names no field: {body}"


async def test_an_unknown_outcome_matches_nothing_rather_than_everything(governance_token) -> None:
    """A value outside the closed vocabulary must not fall through to "no filter". That failure
    mode is silent: the reader sees a full list and believes it was filtered."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        filtered = _check(
            await _traces(client, governance_token, outcome="definitely-not-an-outcome"), 200, 400
        )

    if "traces" in filtered:
        assert filtered["traces"] == [], "an unrecognised outcome behaved as no filter at all"


# ═══ 2. what a tool turn leaves behind ═════════════════════════════════════════════════════════


async def test_a_declared_tool_is_recorded_and_its_arguments_are_not(
    fixture, governance_token, tool_model
) -> None:
    """Names and a count. The arguments are the caller's content — a file path, a query, a
    customer number — and this list is readable by every oversight role."""
    await fixture.enable_tools()
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{TOOL_MODEL}:generateContent",
            headers=fixture.headers(),
            json={
                "contents": [{"parts": [{"text": "Weather in Hamburg? Use the tool."}]}],
                "tools": [WEATHER],
                **SHORT,
            },
            timeout=180.0,
        )
        assert response.status_code == 200, response.text

        rows: list = []
        for _ in range(20):
            body = _check(
                await _traces(client, governance_token, use_case=fixture.slug, tools_only="true"),
                200,
            )
            rows = [row for row in body["traces"] if row.get("tool_calls")]
            if rows:
                break

    assert rows, "a request that declared a function left no record of having declared one"
    recorded = rows[0]["tool_calls"]
    assert recorded["declared"] == 1
    serialised = str(recorded)
    assert "Hamburg" not in serialised, "an argument reached the metadata column"
    assert "arguments" not in serialised


async def test_a_use_case_without_tools_is_refused_and_the_refusal_is_recorded(
    fixture, governance_token, tool_model
) -> None:
    """`FRD-131` FR-3: the toggle is **off** by default, and `FRD-122`: the log records what was
    *asked*, not only what was served. A refusal that left no row would make "somebody keeps
    trying to use tools here" invisible."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{TOOL_MODEL}:generateContent",
            headers=fixture.headers(),
            json={
                "contents": [{"parts": [{"text": "hi"}]}],
                "tools": [WEATHER],
                **SHORT,
            },
            timeout=60.0,
        )
        body = _check(response, 400, 403)
        assert "tool" in str(body).lower(), f"the refusal does not name the reason: {body}"

        for _ in range(20):
            traces = _check(
                await _traces(
                    client, governance_token, use_case=fixture.slug, refusals_only="true"
                ),
                200,
            )
            if traces["traces"]:
                break

    assert traces["traces"], "a refused tool request left no audit row"


async def test_an_empty_tool_list_is_the_same_request_as_none(fixture) -> None:
    """A client that always includes the field must not be refused for asking nothing — otherwise
    the use-case toggle blocks callers who declared no tools at all."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers=fixture.headers(),
            json={"contents": [{"parts": [{"text": "Say OK"}]}], "tools": [], **SHORT},
            timeout=60.0,
        )

    _check(response, 200)


@pytest.mark.parametrize(
    "declaration, reason",
    [
        ({"functionDeclarations": [{"name": "", "parameters": {"type": "object"}}]}, "name"),
        (
            {"functionDeclarations": [{"name": "has spaces", "parameters": {"type": "object"}}]},
            "name",
        ),
        (
            {
                "functionDeclarations": [
                    {"name": "dup", "parameters": {"type": "object"}},
                    {"name": "dup", "parameters": {"type": "object"}},
                ]
            },
            "dup",
        ),
    ],
    ids=["nameless", "uncallable-name", "declared-twice"],
)
async def test_an_uncallable_declaration_is_refused_at_the_surface(
    fixture, declaration, reason, tool_model
) -> None:
    """Parsing belongs to the surface. A name nothing can call, or the same name twice, would be
    rejected downstream with a message naming neither the tool nor the field — and a duplicate
    cannot be matched to one function, so the caller runs whichever their code found first."""
    await fixture.enable_tools()
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{TOOL_MODEL}:generateContent",
            headers=fixture.headers(),
            json={
                "contents": [{"parts": [{"text": "hi"}]}],
                "tools": [declaration],
                **SHORT,
            },
            timeout=60.0,
        )

    body = _check(response, 400)
    assert reason in str(body), f"the refusal names neither the tool nor the field: {body}"


# ═══ 3. the incident filters, at their edges ═══════════════════════════════════════════════════


@pytest.mark.parametrize(
    "value",
    ["10.0.0.7", "::1", "not-an-address", "'; DROP TABLE request_logs; --", "%", "_"],
    ids=["v4", "v6", "prose", "sql-shaped", "sql-wildcard", "sql-single-wildcard"],
)
async def test_an_address_filter_is_a_value_and_never_a_pattern(security_token, value) -> None:
    """`%` and `_` are wildcards in `LIKE` and nothing in `=`. If the filter were ever rewritten to
    match loosely, `%` would return every row while looking like a narrowing filter."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        body = _check(await _traces(client, security_token, source_ip=value), 200, 400)

    if "traces" in body and value in ("%", "_"):
        assert body["traces"] == [], "a wildcard matched rows, so the filter is a pattern"


async def test_the_address_filter_is_refused_for_a_role_that_may_not_act(governance_token) -> None:
    """Refused, not ignored — and the message says who may, so the reader knows what to ask for
    rather than concluding the feature is broken."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await _traces(client, governance_token, source_ip="10.0.0.7")

    body = _check(response, 403)
    assert "security" in str(body).lower()


async def test_an_anonymous_caller_is_refused_before_any_filter_is_considered() -> None:
    """A trace names a caller, a credential, a model and a price."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/v1beta/traces", params={"source_ip": "10.0.0.7"}
        )

    assert response.status_code == 401
