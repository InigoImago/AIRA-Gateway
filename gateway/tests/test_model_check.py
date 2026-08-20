"""Can this model actually be reached (`FRD-506`)?

The question the console could not answer, asked from the running system: *"wie kann ich neue
Modelle definieren von dem Provider, wenn ich keinen key habe, oder einen einfachen Test
durchführen ob es überhaupt ansprechbar wäre?"*

Both halves matter and they are different facts. A catalog entry is a **declaration** — it needs no
credential and proves nothing about reachability. Without a key no adapter is registered, so the
model sits in the catalog looking healthy while every request for it returns `model_not_found`,
which reads to the caller as a typo rather than as a missing credential.

Three answers, never collapsed into one: **declared**, **served**, **reachable**.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead

IT_SECURITY = Principal(subject="sec", method="oidc", roles=("it-security",))
GLOBAL_ADMIN = Principal(subject="root", method="oidc", roles=("global-admin",))
IT_STEUERUNG = Principal(subject="gov", method="oidc", roles=("it-steuerung",))
UC_ADMIN = Principal(subject="boss", method="oidc", roles=("use-case-admin",), use_cases=("uc-a",))


class _Reachable:
    # The check names the model it was asked about, so a double takes the same arguments the real
    # adapters do — a stand-in with a narrower signature is the shape this project warns about.
    async def ping(self, model: str = "", addressing: dict[str, str] | None = None) -> str:
        return f"{model} answered" if model else "3 models listed"


class _Unreachable:
    async def ping(self, model: str = "", addressing: dict[str, str] | None = None) -> str:
        raise ConnectionError("https://api.example/v1?key=super-secret-value")


class _Silent:
    """An adapter with nothing cheap to ask."""


def _client(principal: Principal, provider: Any = None) -> TestClient:
    # `log_queue_size=0` writes the audit row on the request path. `FRD-405` moved that write off
    # it, so a row is otherwise merely queued when the response returns — and the assertions below
    # are about rows.
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.dependency_overrides[require_principal] = lambda: principal
    if provider is not None:
        app.state.providers.provider_for = lambda *_a, **_k: provider  # type: ignore[method-assign]
    else:
        app.state.providers.provider_for = lambda *_a, **_k: None  # type: ignore[method-assign]
    return app


async def _declare(app, model: str = "gemini-2.0-flash") -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=model, capabilities=["generate"], publisher="google"))
        await session.commit()


@pytest.mark.anyio
async def test_a_declared_model_with_no_provider_says_it_is_not_served() -> None:
    """**The case a missing credential produces.** An adapter is registered only when its
    credential is configured, so "declared but not served" is almost always "nobody gave this
    installation a key for it" — and that sentence is what the reader needs, not a green tick."""
    app = _client(IT_SECURITY)
    with TestClient(app) as client:
        await _declare(app)
        body = client.get("/v1beta/models/gemini-2.0-flash:check").json()

    assert body["declared"] is True
    assert body["served"] is False
    assert body["reachable"] is None, "nothing was contacted, so this is not False"
    assert "credential" in body["detail"]


@pytest.mark.anyio
async def test_a_served_model_that_answers_is_reachable() -> None:
    app = _client(IT_SECURITY, _Reachable())
    with TestClient(app) as client:
        await _declare(app)
        body = client.get("/v1beta/models/gemini-2.0-flash:check").json()

    # One entry per region, and a model with none declared is asked once with no region — which
    # is what every dialect addressed by model name alone does (`FRD-609`).
    assert body.pop("regions") == [
        {"region": "", "reachable": True, "detail": "gemini-2.0-flash answered"}
    ]
    assert body == {
        "model": "gemini-2.0-flash",
        "declared": True,
        "served": True,
        "reachable": True,
        # **The model that was asked about.** The check used to ping whichever model an adapter had
        # configured first and report *that* name, so somebody asking about `gemini-2.5-pro` was
        # told "gemini-2.5-flash answered" — an answer about the credential, worded as an answer
        # about the model. Since cataloguing a model became enough to serve it, the one being
        # checked is usually the one *not* in configuration.
        "detail": "gemini-2.0-flash answered",
    }


@pytest.mark.anyio
async def test_an_upstream_that_fails_never_repeats_its_error_text() -> None:
    """A provider's error can carry the URL it was called with, and that URL can carry the key.
    The **type** is diagnostic enough; the message is somebody else's to log."""
    app = _client(IT_SECURITY, _Unreachable())
    with TestClient(app) as client:
        await _declare(app)
        response = client.get("/v1beta/models/gemini-2.0-flash:check")

    body = response.json()
    assert body["served"] is True
    assert body["reachable"] is False
    assert "ConnectionError" in body["detail"]
    assert "super-secret-value" not in response.text


@pytest.mark.anyio
async def test_an_adapter_with_nothing_cheap_to_ask_is_not_reported_green() -> None:
    """`FRD-117`'s rule, one endpoint over: "we did not look" and "it is fine" are different
    answers, and only one of them is safe to act on."""
    app = _client(IT_SECURITY, _Silent())
    with TestClient(app) as client:
        await _declare(app)
        body = client.get("/v1beta/models/gemini-2.0-flash:check").json()

    assert body["reachable"] is None
    assert "not contacted" in body["detail"]


@pytest.mark.anyio
async def test_an_undeclared_model_is_reported_as_undeclared_rather_than_missing() -> None:
    """Serving an undeclared model is legitimate — it gets the baseline (`FRD-114` FR-7). What the
    check must not do is imply somebody has said what it can do."""
    app = _client(IT_SECURITY, _Reachable())
    with TestClient(app) as client:
        body = client.get("/v1beta/models/never-declared:check").json()

    assert body["declared"] is False
    assert body["served"] is True


@pytest.mark.parametrize(
    ("principal", "expected"),
    [(GLOBAL_ADMIN, 200), (IT_SECURITY, 200), (IT_STEUERUNG, 403), (UC_ADMIN, 403)],
    ids=["global-admin", "it-security", "it-steuerung", "use-case-admin"],
)
def test_who_may_ask(principal: Principal, expected: int) -> None:
    """It describes the **installation**, not anybody's traffic: the people who need it are the
    ones who declare models and the ones who investigate why a use case cannot reach one."""
    app = _client(principal, _Reachable())
    with TestClient(app) as client:
        response = client.get("/v1beta/models/gemini-2.0-flash:check")

    assert response.status_code == expected
    if expected == 403:
        assert "IT Security" in response.json()["error"]["message"]


# ═══ only an approved model may be used (FRD-307) ══════════════════════════════════════════════


@pytest.mark.anyio
async def test_a_declared_but_unapproved_model_is_refused_by_name() -> None:
    """The governance question the catalog could not answer.

    Every other requirement asks whether a model *can* do something; this asks whether anybody
    *decided* it may be used. A model appearing on an upstream is not the same event as somebody
    accepting it into this installation, and until `FRD-307` the first implied the second.
    """
    from aira_gateway.catalog import ModelCatalog
    from aira_gateway.requirements import ModelApproved

    app = _client(IT_SECURITY)
    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="pending-1", capabilities=["generate"], approved=False))
            session.add(ModelRead(model="allowed-1", capabilities=["generate"], approved=True))
            await session.commit()

        check = ModelApproved(ModelCatalog(app.state.db_sessionmaker))
        refusal = await check.refusal("pending-1")
        assert refusal is not None
        assert "has not been approved" in refusal
        assert "Global Administrator" in refusal, "the refusal must name who releases a model"

        assert await check.refusal("allowed-1") is None


@pytest.mark.anyio
async def test_a_model_that_is_not_in_the_catalog_at_all_is_refused() -> None:
    """**Reversed by owner decision on 2026-08-09**, and worth stating plainly.

    This test asserted the opposite for about an hour: that an undeclared model keeps `FRD-114`
    FR-7's baseline. The requirement is now *"es dürfen nur die Modelle verwendet werden, die im
    Katalog stehen und explizit von einem globalen Admin angelegt wurden"* — so the baseline for a
    model nobody catalogued is **nothing**.

    It closes the loophole the first version left: deleting a declaration made a model usable
    again, which meant approval could be removed by removing the thing that carried it.
    """
    from aira_gateway.catalog import ModelCatalog
    from aira_gateway.requirements import ModelApproved

    app = _client(IT_SECURITY)
    with TestClient(app):
        check = ModelApproved(ModelCatalog(app.state.db_sessionmaker))
        refusal = await check.refusal("never-catalogued")

    assert refusal is not None
    # Two facts, two actions: this one needs somebody to *add* the model, not to release it.
    assert "not in the model catalog" in refusal
    assert "catalogued and approved" in refusal


@pytest.mark.anyio
async def test_a_test_double_is_not_governed_as_a_model() -> None:
    """The mock answers with deterministic fiction. Approving it would be theatre, and the
    exemption is bounded by where it is registered at all — `create_app` leaves it out of every
    environment but `local`."""
    from aira_gateway.catalog import ModelCatalog
    from aira_gateway.requirements import ModelApproved

    class _Registry:
        def provider_for(self, _model: str) -> object:
            return type("Double", (), {"is_test_double": True})()

    app = _client(IT_SECURITY)
    with TestClient(app):
        check = ModelApproved(ModelCatalog(app.state.db_sessionmaker), _Registry())
        assert await check.refusal("mock-1") is None


# ---- and whether it accepts the level words somebody typed (`ADR-0021`) -------------------------


class _TakesLevels:
    """A dialect with a level field, refusing one word the way a real provider does.

    Returns a **real** `CanonicalResponse` with usage on it, not a bare object. The first version
    returned `object()`, and a mutation run caught what that hides: the row's usage came back
    `None` whatever the code did, so *"the row carries what the answer reported"* was a property no
    test could lose. The trap this project keeps recording — a stand-in emptier than the thing it
    replaces.
    """

    expresses_thinking_levels = True

    async def generate(self, request: Any) -> Any:
        from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage
        from aira_gateway.upstreams.base import UpstreamError

        if request.thinking.mode == "medium":
            raise UpstreamError(
                "Unable to submit request because thinking_level is not supported by this model.",
                status_code=400,
            )
        return CanonicalResponse(
            model=request.model,
            text="ok",
            usage=CanonicalUsage(prompt_tokens=11, completion_tokens=1, total_tokens=12),
        )


class _BudgetOnly:
    """Anthropic's shape: thinking is asked for by naming a number, and there is no level field."""

    expresses_thinking_levels = False

    async def generate(self, request: Any) -> Any:  # pragma: no cover - must never be called
        raise AssertionError("a dialect with no level field must not be asked to spend a token")


@pytest.mark.anyio
async def test_each_level_word_is_answered_by_the_model_in_its_own_words() -> None:
    """**The authority free text needs.** A level is a word the vendor accepts, typed into the
    catalog, because no list here survives the vendors' next release — and the cost of that is
    that a typo looks exactly like a working declaration. So the model is asked, and what comes
    back is the provider's own sentence rather than a rule's paraphrase of it.

    Not hypothetical: run against the real stack on 2026-08-19, `gemini-2.5-flash` answered
    *"thinking_level is not supported by this model"* for all three words the migration had
    carried over for it."""
    app = _client(GLOBAL_ADMIN, _TakesLevels())
    with TestClient(app) as client:
        await _declare(app)
        body = client.post(
            "/v1beta/models/gemini-2.0-flash:checkThinking",
            json={"levels": ["low", "medium"]},
        ).json()

    # Every result names the region it is about, empty where the model declares none.
    assert body["results"] == [
        {"region": "", "level": "low", "accepted": True, "detail": "The model accepted it."},
        {
            "region": "",
            "level": "medium",
            "accepted": False,
            "detail": "Unable to submit request because thinking_level is not supported by this "
            "model.",
        },
    ]


@pytest.mark.anyio
async def test_a_dialect_with_no_level_field_answers_without_spending_anything() -> None:
    """Every word would be refused for the same reason, and none of it is about the model — so
    asking the provider would spend tokens to learn something the declaration already knows.
    `_BudgetOnly.generate` raises if it is reached."""
    app = _client(GLOBAL_ADMIN, _BudgetOnly())
    with TestClient(app) as client:
        await _declare(app)
        body = client.post(
            "/v1beta/models/gemini-2.0-flash:checkThinking", json={"levels": ["low"]}
        ).json()

    assert body["results"][0]["accepted"] is False
    assert "token budget" in body["results"][0]["detail"]


@pytest.mark.anyio
async def test_the_number_of_words_one_press_can_spend_on_is_bounded() -> None:
    """This endpoint spends real money, one output token per accepted word, and it is reached by a
    button. An unbounded list is an unbounded bill from one click."""
    from aira_gateway.api.incidents import MAX_LEVELS_PER_CHECK

    app = _client(GLOBAL_ADMIN, _TakesLevels())
    with TestClient(app) as client:
        await _declare(app)
        body = client.post(
            "/v1beta/models/gemini-2.0-flash:checkThinking",
            json={"levels": [f"w{n}" for n in range(MAX_LEVELS_PER_CHECK + 5)]},
        ).json()

    assert len(body["results"]) == MAX_LEVELS_PER_CHECK


@pytest.mark.anyio
async def test_asking_about_levels_is_bounded_by_the_same_role_as_the_check_beside_it() -> None:
    """It describes the installation and it spends money; both point at the same two roles."""
    app = _client(UC_ADMIN, _TakesLevels())
    with TestClient(app) as client:
        await _declare(app)
        response = client.post(
            "/v1beta/models/gemini-2.0-flash:checkThinking", json={"levels": ["low"]}
        )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_the_check_answers_about_the_provenance_it_was_given() -> None:
    """**The button lives in an editor, so the saved row is the wrong thing to answer about.**

    Reported after a real attempt: a model catalogued under `generative-language` — a provider this
    installation has no credential for — was corrected in the form to `vertex` with a region typed
    beside it, and *Check reachability* answered *"Declared, but nothing serves it"*. Correct about
    the stored declaration, and about nothing the reader was asking. The same shape as a verdict
    left standing from a previous model: right, wearing the wrong label.
    """
    served: dict[str, Any] = {}

    def provider_for(model: str, provider: str = "", publisher: str = "") -> Any:
        served["asked"] = (provider, publisher)
        return _Reachable() if provider == "vertex" else None

    # The registry is closed on shutdown, so it is **patched rather than replaced**: a stand-in
    # narrower than the thing it stands for is the trap this file already carries a comment about,
    # one method along.
    app = create_app(GatewaySettings(auth_required=False))
    app.dependency_overrides[require_principal] = lambda: GLOBAL_ADMIN
    app.state.providers.provider_for = provider_for  # type: ignore[method-assign]

    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(
                ModelRead(
                    model="gemini-3.5-flash",
                    capabilities=["generate"],
                    provider="generative-language",
                    publisher="google",
                )
            )
            await session.commit()

        stored = client.get("/v1beta/models/gemini-3.5-flash:check").json()
        asked = client.get(
            "/v1beta/models/gemini-3.5-flash:check",
            params={"provider": "vertex", "publisher": "google", "region": "global"},
        ).json()

    # Unchanged where nobody overrides: this still answers about the catalogue.
    assert stored["served"] is False
    # And about the form where they do.
    assert served["asked"] == ("vertex", "google")
    assert asked["served"] is True
    assert asked["reachable"] is True


# ---- several regions, checked one by one (`FRD-609`) --------------------------------------------


class _ReachableInOneRegion:
    """Answers in `europe-west4` and refuses in `europe-west1` — the case the list is for."""

    expresses_thinking_levels = True

    async def ping(self, model: str = "", addressing: dict[str, Any] | None = None) -> str:
        region = ((addressing or {}).get("regions") or [""])[0]
        if region == "europe-west1":
            from aira_gateway.upstreams.base import UpstreamError

            raise UpstreamError("Vertex upstream returned 404.", 404)
        return f"{model} answered in {region}"

    async def generate(self, request: Any) -> Any:
        from aira_gateway.upstreams.base import UpstreamError

        region = (request.addressing.get("regions") or [""])[0]
        if region == "europe-west1":
            raise UpstreamError(
                "Unable to submit request because thinking_level is not supported by this model.",
                400,
            )
        return object()


@pytest.mark.anyio
async def test_every_declared_region_is_checked_and_the_summary_says_which_failed() -> None:
    """**A model in three places is reachable in some and not others**, which is the whole reason
    somebody lists more than one. Answering about the first would be an answer to a question nobody
    asked — the same defect as reporting about whichever model an adapter had configured first.

    The summary is the *best* of them, because the request will be served: a model that answers in
    one of its regions **is** reachable, and a summary of "not reachable" would be false. What the
    administrator needs is both — it works, and here is the one that does not.
    """
    app = _client(GLOBAL_ADMIN, _ReachableInOneRegion())
    with TestClient(app) as client:
        await _declare(app, "gemini-2.5-pro")
        body = client.get(
            "/v1beta/models/gemini-2.5-pro:check",
            params={"provider": "vertex", "region": "europe-west1,europe-west4"},
        ).json()

    assert [entry["region"] for entry in body["regions"]] == ["europe-west1", "europe-west4"]
    assert [entry["reachable"] for entry in body["regions"]] == [False, True]
    assert body["reachable"] is True
    assert "Not reachable in: europe-west1" in body["detail"]


@pytest.mark.anyio
async def test_a_thinking_word_is_asked_in_every_region() -> None:
    """Which words a place accepts is not knowable from here: a vendor rolls a family out region by
    region, so `thinkingLevel` can work in one and answer *"not supported by this model"* in
    another — and a declaration checked in one region would be a claim about the others."""
    app = _client(GLOBAL_ADMIN, _ReachableInOneRegion())
    with TestClient(app) as client:
        await _declare(app, "gemini-2.5-pro")
        body = client.post(
            "/v1beta/models/gemini-2.5-pro:checkThinking",
            params={"provider": "vertex", "region": "europe-west1,europe-west4"},
            json={"levels": ["low", "high"]},
        ).json()

    assert [(r["region"], r["level"], r["accepted"]) for r in body["results"]] == [
        ("europe-west1", "low", False),
        ("europe-west1", "high", False),
        ("europe-west4", "low", True),
        ("europe-west4", "high", True),
    ]
    assert "thinking_level is not supported" in body["results"][0]["detail"]


# ---- a check that spends money leaves a row (`FRD-610`) ----------------------------------------


@pytest.mark.anyio
async def test_asking_the_model_leaves_an_audit_row_with_what_it_cost() -> None:
    """**The exemption this closes was mine, and its argument was wrong.**

    It said the spend is tiny, bounded and role-gated — all true, and an answer to a different
    question. The rule is that every request is auditable, and *"how much did this cost"* has to be
    answerable: a small amount nobody can see is not a small amount, it is an invisible one.
    Measured before the fix, against the running installation: 1491 audit rows before pressing the
    button and 1491 after.
    """
    from aira_gateway.audit import Outcome
    from aira_gateway.db.models import RequestLog

    app = _client(GLOBAL_ADMIN, _TakesLevels())
    with TestClient(app) as client:
        await _declare(app)
        client.post(
            "/v1beta/models/gemini-2.0-flash:checkThinking",
            json={"levels": ["low", "medium"]},
        )
        async with app.state.db_sessionmaker() as session:
            rows = (await session.execute(RequestLog.__table__.select())).fetchall()

    diagnostics = [row for row in rows if row.outcome == Outcome.DIAGNOSTIC]
    assert [row.operation for row in diagnostics] == [
        "models:checkThinking:low",
        "models:checkThinking:medium",
    ]
    # **No use case, and none invented.** The check exists for a model nobody has released yet, so
    # there is nothing to attribute it to — and inventing an owner so a row has somewhere to sit is
    # the failure `FRD-403` names.
    assert {row.use_case for row in diagnostics} == {None} or {
        row.use_case for row in diagnostics
    } == {""}
    # Attributed to the person who pressed it.
    assert {row.subject for row in diagnostics} == {GLOBAL_ADMIN.subject}

    # **What it cost**, from the answer rather than from an estimate. The accepted word carries the
    # usage the model reported; the refused one carries none, because nothing was generated — and
    # `NULL` rather than `0` is the convention a refusal already uses here (`FRD-403`).
    accepted = next(row for row in diagnostics if row.operation.endswith(":low"))
    refused = next(row for row in diagnostics if row.operation.endswith(":medium"))
    assert (accepted.prompt_tokens, accepted.completion_tokens) == (11, 1)
    assert accepted.total_tokens == 12
    assert refused.total_tokens is None


@pytest.mark.anyio
async def test_a_diagnostic_is_not_counted_as_served() -> None:
    """Its own outcome, because counted as `served` these would inflate every request figure with
    traffic no use case made — the shape `FRD-125b` refused for pipeline calls. Separable is also
    what makes *"what did diagnostics cost this month"* answerable at all."""
    from aira_gateway.audit import Outcome
    from aira_gateway.db.models import RequestLog

    app = _client(GLOBAL_ADMIN, _Reachable())
    with TestClient(app) as client:
        await _declare(app)
        client.get("/v1beta/models/gemini-2.0-flash:check")
        async with app.state.db_sessionmaker() as session:
            rows = (await session.execute(RequestLog.__table__.select())).fetchall()

    assert [row.outcome for row in rows] == [Outcome.DIAGNOSTIC]
    assert [row.operation for row in rows] == ["models:check"]
    # `:countTokens` is free, so nothing was spent — and the row saying so is a stronger statement
    # than no row at all.
    assert rows[0].total_tokens is None
