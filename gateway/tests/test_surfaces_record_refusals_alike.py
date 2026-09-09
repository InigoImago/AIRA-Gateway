"""A refusal from an authenticated caller leaves a row — on **both** surfaces.

`FRD-122`'s rule is that the log records what was **asked**, not only what was served, and
`test_a_kira_request_is_audited_exactly_like_a_gemini_one` already compares the two surfaces row
for row. It compares a **served** request. A developer round on 2026-08-12 asked the same question
about the refused ones and found the two surfaces disagreeing:

    malformed JSON, valid credential    Gemini: a row      KIRA: nothing at all

The cause is the order of two steps that look unrelated. `_refused` records only when
`request.state.attribution` is set — deliberately, because a request refused *before* the
credential was judged has nobody to attribute to and writing one would let anyone put another
system's name in the audit trail (`FRD-122` §2). On the Gemini surface attribution is a
router-level dependency and is therefore always resolved before the route body runs. On KIRA it
was resolved *inside* the route, after the body was parsed — so anything the parse rejected fell
into the gap.

**Attribution never needed the body.** It reads the header and the principal. Putting it first
costs nothing and closes the difference.

Two surfaces answering one governance question differently is the shape this project keeps
finding — an empty membership list meaning "anything goes" on one of them, a kill switch guarded
by a visibility predicate on one plane. The lesson each time is the same: **the parity is the
property, so the parity is what a test has to assert.** Comparing them only where they succeed
compares them where they are least likely to differ.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, RequestLog

KIRA = "/kira/api/external/chat"
GEMINI = "/v1beta/models/mock-1:generateContent"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(
        GatewaySettings(auth_required=False, environment="local", demo_mode=True, log_queue_size=0)
    )
    with TestClient(app) as running:

        async def _seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(
                    ModelRead(
                        model="mock-1",
                        numeric_id=9001,
                        capabilities=["generate"],
                        approved=True,
                    )
                )
                await session.commit()

        running.portal.call(_seed)  # type: ignore[attr-defined]
        yield running


async def _rows(client: TestClient) -> list[Any]:
    app = client.app  # type: ignore[attr-defined]
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


@pytest.mark.parametrize(
    ("surface", "url"),
    [pytest.param("gemini", GEMINI, id="gemini"), pytest.param("kira", KIRA, id="kira")],
)
async def test_malformed_json_from_a_known_caller_is_recorded(
    client: TestClient, surface: str, url: str
) -> None:
    """The case that differed. Parametrised over both surfaces on purpose: a property that holds
    on one of them is not the property — and asserting it separately is how the two came to
    disagree in the first place."""
    response = client.post(url, content=b"{ not json")

    assert response.status_code in (400, 422), response.text
    rows = await _rows(client)

    assert len(rows) == 1, f"{surface}: a refusal left no trace"
    assert rows[0].api == surface
    assert rows[0].outcome == "invalid_request"


@pytest.mark.parametrize(
    ("surface", "url", "body"),
    [
        pytest.param("gemini", GEMINI, {"contents": []}, id="gemini"),
        pytest.param(
            "kira",
            KIRA,
            {"request": {"parts": [{"text": ""}]}, "model_id": 9001},
            id="kira",
        ),
    ],
)
async def test_an_empty_request_is_recorded_on_both_surfaces(
    client: TestClient, surface: str, url: str, body: dict[str, Any]
) -> None:
    """The second half of the same rule, with a body that parses and asks for nothing. It already
    held on both — kept so that a future change to one surface's ordering shows up as *two*
    failures rather than one, which is what makes a parity break legible."""
    response = client.post(url, json=body)

    assert response.status_code >= 400
    rows = await _rows(client)

    assert len(rows) == 1, f"{surface}: a refusal left no trace"
    assert rows[0].api == surface


#: JSON that parses and is not an object. Between "malformed" (asserted above) and "wrong fields"
#: (asserted below) — and the gap between two named cases is where the third one lives.
NOT_AN_OBJECT: list[bytes] = [b"[1, 2]", b'"text"', b"5", b"null", b"true"]


@pytest.mark.parametrize("raw", NOT_AN_OBJECT, ids=lambda raw: raw.decode())
@pytest.mark.parametrize(
    ("surface", "url"),
    [pytest.param("gemini", GEMINI, id="gemini"), pytest.param("kira", KIRA, id="kira")],
)
async def test_a_body_that_is_not_an_object_is_recorded(
    client: TestClient, surface: str, url: str, raw: bytes
) -> None:
    """The third case, found on 2026-09-08, and it had gone the same way as the first.

    `[1, 2]` is valid JSON, so it walked past the parse; the Gemini surface assigned it to the
    audit trail and refused it a few lines later, correctly, with a `400`. **The write then
    failed**: the payload columns hold an object, `_maybe` ended in `dict(...)`, and a list is not
    a mapping — `TypeError`, `audit_refusal_not_recorded`, and no row. Two characters, and a
    request that left no trace.

    KIRA already refused it before touching the trail (*"Request body must be an object."*), as
    does `incidents._body_of` (*"Send one JSON object."*). Two of three said it and the third —
    the one a real client posts to — did not, which is why the assertion is a **parity** one:
    a rule stated per surface is a rule some surface is missing.
    """
    response = client.post(url, content=raw, headers={"content-type": "application/json"})

    assert response.status_code in (400, 422), response.text
    rows = await _rows(client)

    assert len(rows) == 1, f"{surface}: a refusal left no trace"
    assert rows[0].api == surface
    assert rows[0].outcome == "invalid_request"
    # **Refused before the trail, not merely survived by the writer.** Both surfaces stop a
    # non-object body before assigning it, so the row carries no payload — and asserting that is
    # what keeps this test able to fail. The writer wraps a non-mapping rather than raising
    # (`writer.as_object`), so without this line the row would still be written with the body in
    # it and the surface's own check could be deleted unnoticed: a property guarded twice is a
    # property no single assertion sees losing half of itself.
    assert rows[0].request_payload is None, (
        f"{surface}: the body reached the trail before its shape was checked"
    )


# =================================================================================================
# The matrix — one condition, both surfaces, compared rather than each checked against itself
# =================================================================================================
#
# The three cases above are the ones that have already gone wrong. This is the question that
# **found** the third of them: drive the same *condition* through both surfaces and compare
# `served / recorded / outcome`, rather than asserting each surface against its own expectations.
#
# An asymmetry between two files is in neither of them (`LESSONS.md` §7), and the two surfaces here
# had been compared on a served request and on malformed JSON — the two cases most likely to agree.
# Written as a matrix on 2026-09-08, eighteen conditions; one row disagreed and is now a test of
# its own. These are the rest, kept as the regression guard a matrix is for: each exercises a
# different stage — the encodability door, attachment validation, a dispatch requirement — so a
# change that moves one surface's ordering shows up here rather than in a bug report.


def _gemini(**generation: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    if generation:
        body["generationConfig"] = generation
    return body


def _kira(**extra: Any) -> dict[str, Any]:
    return {"request": {"parts": [{"text": "hi"}]}, "model_id": 9001, **extra}


def _part(part: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {"contents": [{"role": "user", "parts": [part]}]},
        {"request": {"parts": [part]}, "model_id": 9001},
    )


_BAD_IMAGE = {"inlineData": {"mimeType": "image/png", "data": "!!!!"}}
_BAD_TYPE = {"inlineData": {"mimeType": "application/x-evil", "data": "AAAA"}}

#: ``(name, gemini body, kira body)``. Only conditions both surfaces can express: KIRA has no
#: `tools` field and is deliberately tolerant of unknown ones (`FRD-124`), so those two are
#: differences by design rather than divergences, and a matrix that included them would be
#: asserting the opposite of the contract.
CONDITIONS: list[tuple[str, Any, Any]] = [
    ("a lone surrogate", *_part({"text": "\ud800"})),
    ("an unencodable number", _gemini(maxOutputTokens=1e400), _kira(maxTokens=1e400)),
    ("a non-positive cap", _gemini(maxOutputTokens=-5), _kira(maxTokens=-5)),
    (
        "thinking a model does not declare",
        _gemini(thinkingConfig={"thinkingBudget": 100}),
        _kira(thinking={"mode": "enabled", "tokenCount": 100}),
    ),
    (
        "a schema no model can express",
        _gemini(responseSchema={"type": "STRING"}),
        _kira(responseSchema={"type": "STRING"}),
    ),
    (
        "a schema that is not one",
        _gemini(responseSchema={"type": "NOPE"}),
        _kira(responseSchema={"type": "NOPE"}),
    ),
    (
        "nothing to answer",
        {"contents": [{"role": "user", "parts": []}]},
        {"request": {"parts": []}, "model_id": 9001},
    ),
    ("an attachment that is not base64", *_part(_BAD_IMAGE)),
    ("a media type nothing accepts", *_part(_BAD_TYPE)),
]


def _outcome(client: TestClient, url: str, body: Any) -> tuple[bool, int, str | None]:
    """Send ``body`` and report *(served, rows, outcome)* — the three facts being compared.

    Sent as raw bytes, because two of the conditions are values the test client's own JSON encoder
    refuses to carry: `Infinity` and a lone surrogate are exactly the shapes under examination, and
    routing them through an encoder that objects would test the encoder.
    """
    raw = json.dumps(body, allow_nan=True).encode("utf-8", "surrogatepass")
    response = client.post(url, content=raw, headers={"content-type": "application/json"})
    rows = client.portal.call(_rows, client)  # type: ignore[attr-defined]
    return response.status_code < 400, len(rows), rows[0].outcome if rows else None


@pytest.mark.parametrize("condition", CONDITIONS, ids=[name for name, _g, _k in CONDITIONS])
def test_both_surfaces_answer_a_condition_the_same_way(
    condition: tuple[str, Any, Any],
) -> None:
    """One app per surface, so neither sees the other's rows, and one assertion per fact.

    Deliberately **not** comparing status codes: KIRA answers `422` where Gemini answers `400`, on
    purpose and by contract, and a matrix that demanded identical statuses would be asserting the
    opposite of what the compatibility surface is for. What has to agree is what *happened* —
    whether it was served, whether it left a row, and what the row says it was.
    """
    name, gemini_body, kira_body = condition
    results: dict[str, tuple[bool, int, str | None]] = {}
    for surface, url, body in (("gemini", GEMINI, gemini_body), ("kira", KIRA, kira_body)):
        app = create_app(
            GatewaySettings(
                auth_required=False, environment="local", demo_mode=True, log_queue_size=0
            )
        )
        with TestClient(app) as running:

            async def _seed() -> None:
                async with app.state.db_sessionmaker() as session:  # noqa: B023
                    session.add(
                        ModelRead(
                            model="mock-1",
                            numeric_id=9001,
                            capabilities=["generate"],
                            approved=True,
                        )
                    )
                    await session.commit()

            running.portal.call(_seed)  # type: ignore[attr-defined]
            results[surface] = _outcome(running, url, body)

    gemini, kira = results["gemini"], results["kira"]
    assert gemini[0] == kira[0], f"{name}: served on one surface only — {gemini} vs {kira}"
    assert gemini[1] == kira[1], f"{name}: recorded on one surface only — {gemini} vs {kira}"
    assert gemini[2] == kira[2], f"{name}: different outcome — {gemini[2]} vs {kira[2]}"


async def test_an_unauthenticated_request_is_still_not_recorded() -> None:
    """The deliberate exception, asserted so the fix above cannot quietly widen into it.

    A request refused **before** the credential was judged has nobody to attribute to, and writing
    a row for it would let anyone put another system's name into the audit trail with one
    unauthenticated call — an unverifiable claim is not evidence (`FRD-122` §2). The repair was to
    resolve attribution earlier, *after* authentication; it must not become "record everything".
    """
    app = create_app(GatewaySettings(auth_required=True, environment="local", log_queue_size=0))
    with TestClient(app) as client:
        assert client.post(KIRA, content=b"{ not json").status_code == 401
        assert client.post(GEMINI, content=b"{ not json").status_code == 401

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert rows == []
