"""Every request that is attributed to somebody says so **on its span**, not only on its row.

`FRD-101` §9 and `FRD-102` §9 name `aira.subject`, `aira.auth_method`, `aira.use_case` and
`aira.credential` as built — and they were, at **one** of the four places a request is attributed.
The Gemini surface's dependency set them; the KIRA surface, `pipeline:dryRun` and the console's
model check each built the same `Attribution`, assigned it to `request.state` and stopped there.

All four write an audit row, so the figures were in the database and the trace could not be
filtered by any of them. The provisioned dashboard selects `span.aira.use_case` and
`span.aira.subject`, so those columns were empty for every request that did not arrive through the
Gemini surface — and an empty column reads as *nothing to show* rather than as a missing
attribute.

Two guards, because they fail differently:

- the **behavioural** half drives both surfaces over a real ASGI transport and reads the span;
- the **structural** half fails on a fifth site that assigns `request.state.attribution` itself,
  which is a test that cannot be satisfied by remembering — the same answer `FRD-126` and
  `test_every_dispatch_applies_the_conditions.py` reached for the same shape of hole.
"""

from __future__ import annotations

import ast
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest_asyncio
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import create_all
from aira_gateway.db.models import ModelRead

MODEL = "mock-1"
NUMERIC_ID = 4711

#: What an attribution owes its span. The audit row carries more; these four are the ones a
#: **trace** is filtered by, and the four `auth/attribution.attribute` sets.
ATTRIBUTED = ("aira.subject", "aira.auth_method")


@pytest_asyncio.fixture
async def served() -> AsyncIterator[tuple[httpx.AsyncClient, InMemorySpanExporter, Any]]:
    app = create_app(GatewaySettings(auth_required=False, store_payloads=False))
    await create_all(app.state.db_engine)
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=MODEL, numeric_id=NUMERIC_ID, capabilities=["generate"]))
        await session.commit()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, exporter, provider
    await app.state.db_engine.dispose()


async def _attributes(
    client: httpx.AsyncClient,
    exporter: InMemorySpanExporter,
    provider: Any,
    path: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    exporter.clear()
    with provider.get_tracer("test").start_as_current_span("POST"):
        response = await client.post(path, json=body)
    assert response.status_code == 200, response.text
    spans = exporter.get_finished_spans()
    assert spans, "the test's own span was never closed"
    return dict(spans[0].attributes or {})


# ═══ the behaviour ═══════════════════════════════════════════════════════════════════════════════


async def test_the_gemini_surface_puts_the_caller_on_the_span(
    served: tuple[httpx.AsyncClient, InMemorySpanExporter, Any],
) -> None:
    """The one that always did, kept here so the two are read together."""
    client, exporter, provider = served
    attributes = await _attributes(
        client,
        exporter,
        provider,
        f"/v1beta/models/{MODEL}:generateContent",
        {"contents": [{"role": "user", "parts": [{"text": "hallo"}]}]},
    )
    assert all(name in attributes for name in ATTRIBUTED), sorted(attributes)


async def test_the_compatibility_surface_puts_the_caller_on_the_span(
    served: tuple[httpx.AsyncClient, InMemorySpanExporter, Any],
) -> None:
    """The one that did not. Same request, same caller, same audit row — and a span a trace view
    could not filter by *who* or by *which use case*."""
    client, exporter, provider = served
    attributes = await _attributes(
        client,
        exporter,
        provider,
        "/kira/api/external/chat",
        {"request": {"parts": [{"text": "hallo"}]}, "model_id": NUMERIC_ID},
    )
    missing = [name for name in ATTRIBUTED if name not in attributes]
    assert not missing, (
        f"the KIRA surface's span carries no {missing}; a trace of this request cannot be "
        "filtered by who made it (FRD-101 §9, FRD-102 §9)"
    )


# ═══ the structure ═══════════════════════════════════════════════════════════════════════════════


SOURCE = Path(__file__).resolve().parents[1] / "src" / "aira_gateway"

#: The one function that may attach an attribution, because it is the one that also puts it on the
#: span. Named rather than inferred: a second owner is exactly the state this file was written in.
OWNER = "gateway/src/aira_gateway/auth/attribution.py"


def _assignment_sites() -> list[str]:
    """Every `request.state.attribution = …` in the gateway, as `<path>:<line>`."""
    sites: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "attribution"
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "state"
                ):
                    sites.append(f"{path.relative_to(SOURCE.parents[2])}:{node.lineno}")
    return sites


def test_there_is_an_assignment_to_find() -> None:
    """A guard on the guard: if the shape stops matching — a rename, a different spelling — the
    assertion below passes by finding nothing, which is how an absence check goes vacuous."""
    assert _assignment_sites()


def test_only_one_place_attributes_a_request() -> None:
    strays = [site for site in _assignment_sites() if not site.startswith(OWNER)]
    assert not strays, (
        f"{strays} assign `request.state.attribution` directly, so the request is attributed for "
        "the audit row and not for the trace. Call `auth.attribution.attribute(request, …)`, "
        "which does both — that split is what left three of four surfaces off the dashboard."
    )
