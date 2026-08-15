"""One use case with a real language model and a real embedding model, and the knobs to govern it.

`conftest.Fixture` builds a use case and a key, which is what most suites need. This round needs
more, and the difference is `FRD-308`: a use case starts able to call **nothing**, so a fixture
that does not release a model can only ever exercise `mock-1` — the test double, which is exempt
from the release gate *and* from the approval gate, and therefore cannot answer any question about
either. Three suites had quietly become tests of the double before this was noticed.

So this one releases both real models, and then offers the policies as methods rather than as SQL
scattered through the tests: a pipeline, a rate limit, a budget, tool calling, prompt caching,
payload storage, a suspension. Each writes the gateway's read-model directly — the distribution
path from Management over Kafka has its own suites, and what is under test here is what the
**gateway** does once a policy has arrived.

Two things are deliberately *not* hidden behind methods. The audit rows are returned raw, because
every claim in this round is ultimately a claim about what was recorded; and the caching of
policies is respected rather than defeated — `RateLimitService` and the suspension gate both hold a
few seconds of configuration, so a test that changes a limit and expects the next request to feel
it is testing the clock. `settle()` waits for the change to be visible instead.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_common.apikeys import generate_api_key

from .conftest import GATEWAY_URL

#: The two models `make showcase` and CI pull. Small on purpose: what is under test is the gateway,
#: not the answer.
CHAT_MODEL = "qwen3:0.6b"
EMBED_MODEL = "all-minilm"
#: The deterministic double. Kept for the cases that are about *this* gateway's bookkeeping rather
#: than about a model — and never for a case about approval or release, which it is exempt from.
MOCK_MODEL = "mock-1"

KIRA = "/kira/api/external"
GEMINI = "/v1beta"

#: How long a policy written into the read-model may take to be felt. The rate limiter caches its
#: configuration for `CONFIG_CACHE_SECONDS` and the suspension gate for five; both are deliberate
#: (`FRD-405`, `FRD-503`), so this waits them out rather than pretending they are not there.
POLICY_SETTLE_SECONDS = 7.0


class Governed:
    """A use case that can call both models, plus every policy this round changes."""

    def __init__(self, engine: AsyncEngine, slug: str, key: str) -> None:
        self.engine = engine
        self.slug = slug
        self.key = key

    # ---- talking to it ------------------------------------------------------------------

    def headers(self, **extra: str) -> dict[str, str]:
        return {"x-goog-api-key": self.key, "content-type": "application/json", **extra}

    async def key_for(self, subject: str) -> str:
        """A second key on this use case, owned by **somebody else**.

        `each_member` means one counter per person, and a fixture with one credential cannot tell
        that apart from one counter per use case — every request it makes is the same person. This
        is the only way to ask, at this layer, whether a per-head allowance really binds per head.
        """
        full_key, prefix, key_hash = generate_api_key()
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO api_keys (id, prefix, key_hash, subject, use_case, label,"
                    " is_active) VALUES (:id, :prefix, :hash, :subject, :slug, :subject, true)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "prefix": prefix,
                    "hash": key_hash,
                    "subject": subject,
                    "slug": self.slug,
                },
            )
        return full_key

    async def generate_as(
        self, key: str, body: dict[str, Any], *, model: str = CHAT_MODEL, timeout: float = 120.0
    ) -> httpx.Response:
        """One request with a credential of this test's choosing, rather than the fixture's."""
        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=timeout) as client:
            return await client.post(
                f"{GEMINI}/models/{model}:generateContent",
                json=body,
                headers={"x-goog-api-key": key, "content-type": "application/json"},
            )

    async def generate(
        self, body: dict[str, Any], *, model: str = CHAT_MODEL, timeout: float = 120.0
    ) -> httpx.Response:
        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=timeout) as client:
            return await client.post(
                f"{GEMINI}/models/{model}:generateContent", json=body, headers=self.headers()
            )

    async def embed(
        self, body: dict[str, Any], *, model: str = EMBED_MODEL, timeout: float = 120.0
    ) -> httpx.Response:
        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=timeout) as client:
            return await client.post(
                f"{GEMINI}/models/{model}:embedContent", json=body, headers=self.headers()
            )

    async def kira(self, path: str, body: dict[str, Any], *, timeout: float = 120.0):
        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=timeout) as client:
            return await client.post(f"{KIRA}{path}", json=body, headers=self.headers())

    # ---- governing it -------------------------------------------------------------------

    async def _exec(self, statement: str, **params: Any) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(text(statement), params)

    async def release(self, *models: str) -> None:
        """Which models this use case may call (`FRD-308`). No arguments releases nothing, which is
        an answer — and a different one from never having been told."""
        await self._exec(
            "UPDATE use_cases SET allowed_models = :models WHERE slug = :slug",
            slug=self.slug,
            models=json.dumps(list(models)),
        )

    async def unrelease_everything(self) -> None:
        """`[]` — somebody released nothing. Distinct from `NULL`, which is *no event has said*."""
        await self.release()

    async def forget_the_release(self) -> None:
        """`NULL` — the state a read-model row written by an older Management is in."""
        await self._exec(
            "UPDATE use_cases SET allowed_models = NULL WHERE slug = :slug", slug=self.slug
        )

    async def pipeline(self, *steps: dict[str, Any], fallbacks: tuple[str, ...] = ()) -> None:
        await self._exec(
            "INSERT INTO pipeline_configs (use_case, steps, fallback_models)"
            " VALUES (:slug, CAST(:steps AS json), CAST(:fb AS json))"
            " ON CONFLICT (use_case) DO UPDATE SET"
            " steps = CAST(:steps AS json), fallback_models = CAST(:fb AS json)",
            slug=self.slug,
            steps=json.dumps(list(steps)),
            fb=json.dumps(list(fallbacks)),
        )

    async def no_pipeline(self) -> None:
        await self._exec("DELETE FROM pipeline_configs WHERE use_case = :slug", slug=self.slug)

    async def rate_limit(
        self, *, limit_rpm: int, burst: int = 0, scope: str = "use_case", subject: str = ""
    ) -> None:
        await self._exec(
            "INSERT INTO rate_limits (id, use_case, scope, subject, limit_rpm, burst, enabled)"
            " VALUES (:id, :slug, :scope, :subject, :rpm, :burst, true)",
            id=_row_id(),
            slug=self.slug,
            scope=scope,
            subject=subject,
            rpm=limit_rpm,
            burst=burst,
        )

    async def budget(
        self,
        *,
        tokens: int | None = None,
        requests: int | None = None,
        cost_nanos: int | None = None,
        period: str = "month",
        scope: str = "use_case",
        subject: str = "",
    ) -> None:
        await self._exec(
            "INSERT INTO budgets (id, use_case, scope, subject, period, limit_tokens,"
            " limit_requests, limit_cost_nanos, enabled)"
            " VALUES (:id, :slug, :scope, :subject, :period, :tokens, :requests, :cost, true)",
            id=_row_id(),
            slug=self.slug,
            scope=scope,
            subject=subject,
            period=period,
            tokens=tokens,
            requests=requests,
            cost=cost_nanos,
        )

    async def set_flag(self, column: str, value: Any) -> None:
        """One setter for the use case's own switches, because they differ only by column name.

        The column is **not** interpolated from anything a test computes — it comes from the small
        set below, so this cannot become a way to write arbitrary SQL from a parametrised case.
        """
        allowed = {
            "tools_enabled",
            "prompt_caching_enabled",
            "prompt_cache_ttl",
            "store_payloads",
            "retention_days",
            "restrict_members_to_own_requests",
        }
        assert column in allowed, f"{column} is not a use-case switch"
        await self._exec(
            f"UPDATE use_cases SET {column} = :value WHERE slug = :slug",  # noqa: S608 - see above
            slug=self.slug,
            value=value,
        )

    async def settle(self, seconds: float = POLICY_SETTLE_SECONDS) -> None:
        """Wait out the configuration caches the gateway deliberately keeps."""
        await asyncio.sleep(seconds)

    # ---- reading what happened ----------------------------------------------------------

    async def rows(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT api, operation, status, outcome, model, requested_model,"
                    " requested_model AS asked_for, model_selection, pipeline_decisions,"
                    " tool_calls, provider, publisher, region, prompt_tokens, completion_tokens,"
                    " total_tokens, cost_nanos, latency_ms, subject, credential, use_case,"
                    " request_payload, response_payload, flagged, degraded"
                    " FROM request_logs WHERE use_case = :slug ORDER BY created_at, id"
                ),
                {"slug": self.slug},
            )
            return [dict(row._mapping) for row in result]

    async def wait_for_rows(self, count: int, *, timeout: float = 25.0) -> list[dict[str, Any]]:
        """Wait until ``count`` audit rows exist.

        The write is off the request path (`FRD-405`), so a response arriving and its row existing
        are two events. Waiting for *one* row when the test made three is the mistake this
        repository has recorded four times; the count the test actually expects is the only version
        that cannot lie in either direction.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            rows = await self.rows()
            if len(rows) >= count:
                return rows
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError(f"expected {count} audit rows, saw {len(rows)}: {rows}")
            await asyncio.sleep(0.25)

    async def last_row(self, *, timeout: float = 25.0) -> dict[str, Any]:
        return (await self.wait_for_rows(1, timeout=timeout))[-1]

    async def clear_rows(self) -> None:
        """Forget what has been recorded, so the next assertion is about the next request.

        Used where a test needs "the row this produced" and the setup produced others. Never used
        to hide a row a test did not expect — the counts are asserted.
        """
        await self._exec("DELETE FROM request_logs WHERE use_case = :slug", slug=self.slug)


def _row_id() -> int:
    """A read-model id. These tables are fed by events, so nothing here generates one."""
    return uuid.uuid4().int % 2_000_000_000


async def build(engine: AsyncEngine, *, released: tuple[str, ...] | None = None) -> Governed:
    slug = f"dev-{uuid.uuid4().hex[:8]}"
    full_key, prefix, key_hash = generate_api_key()
    models = (CHAT_MODEL, EMBED_MODEL, MOCK_MODEL) if released is None else released

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO use_cases (slug, name, store_payloads, retention_days,"
                " allowed_models) VALUES (:slug, :slug, true, 7, CAST(:models AS json))"
            ),
            {"slug": slug, "models": json.dumps(list(models))},
        )
        await connection.execute(
            text(
                "INSERT INTO api_keys (id, prefix, key_hash, subject, use_case, label, is_active)"
                " VALUES (:id, :prefix, :hash, 'dev-round', :slug, 'dev-round', true)"
            ),
            {"id": str(uuid.uuid4()), "prefix": prefix, "hash": key_hash, "slug": slug},
        )
    return Governed(engine, slug, full_key)


async def destroy(governed: Governed) -> None:
    async with governed.engine.begin() as connection:
        for statement in (
            "DELETE FROM request_logs WHERE use_case = :slug",
            "DELETE FROM budgets WHERE use_case = :slug",
            "DELETE FROM budget_usage WHERE scope_key LIKE :like",
            "DELETE FROM rate_limits WHERE use_case = :slug",
            "DELETE FROM pipeline_configs WHERE use_case = :slug",
            "DELETE FROM anomaly_events WHERE use_case = :slug",
            "DELETE FROM anomaly_rules WHERE use_case = :slug",
            "DELETE FROM access_suspensions WHERE use_case = :slug OR target_value = :slug",
            "DELETE FROM api_keys WHERE use_case = :slug",
            "DELETE FROM use_cases WHERE slug = :slug",
        ):
            await connection.execute(
                text(statement), {"slug": governed.slug, "like": f"%{governed.slug}%"}
            )
