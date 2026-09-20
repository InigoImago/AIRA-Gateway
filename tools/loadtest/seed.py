"""The use cases, keys and catalogue rows one load run needs (`FRD-136` FR-8).

Written straight into the gateway's **read-model**, as `tools/seed_local_catalog.py` does and for
the same reason: Management is the authority and publishes over Kafka, that path has its own suite,
and what a measurement needs is the rows in place. Nothing here is a claim about how an
installation is configured.

Every row this writes carries the `lt-` prefix, and `--clean` removes exactly those — including the
request logs, because a load run's rows left in the audit trail are a lie about what the
installation did.

    uv run python -m tools.loadtest.seed            # create or refresh
    uv run python -m tools.loadtest.seed --clean    # remove everything it made
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import text
from tools.loadtest.models import CHAT_MODELS, EMBEDDING_MODELS, PROVIDER

from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine

#: Everything this module writes begins with it, so the cleanup is a prefix match and not a list
#: somebody has to keep current.
PREFIX = "lt-"

#: The heaviest single request a load run sends: one embedding batch. A rate limit whose burst is
#: below this refuses that request outright, whatever its per-minute allowance says.
MAX_BATCH_WEIGHT = 256

#: Not a secret: it derives keys for a development stack that a load run is about to hammer. It is
#: a constant so that the driver can derive the same keys without a handover file, and it is
#: *stated* to be public so nobody promotes it by accident.
KEY_SALT = "aira-loadtest-not-a-secret"


def key_for(slug: str, index: int) -> str:
    """The API key of one simulated person. Derived, so the driver needs no file from the seeder."""
    digest = hashlib.sha256(f"{KEY_SALT}:{slug}:{index}".encode()).hexdigest()
    return f"aira_{digest[:8]}_{digest[8:56]}"


def subject_for(slug: str, index: int) -> str:
    """One simulated person. Distinct per key, because an allowance belongs to a person
    (`ADR-0019`) and a run in which three hundred people share one subject exercises one bucket."""
    return f"{slug}-person-{index:03d}"


class Workload:
    """One use case a profile drives, and everything the read-model needs to know about it."""

    def __init__(
        self,
        slug: str,
        name: str,
        models: tuple[str, ...],
        people: int,
        *,
        tools_enabled: bool = False,
        include_reasoning: bool = True,
        store_payloads: bool = True,
        prompt_caching: bool = False,
        rpm_per_person: int = 600,
    ) -> None:
        self.slug = f"{PREFIX}{slug}"
        self.name = name
        self.models = models
        self.people = people
        self.tools_enabled = tools_enabled
        self.include_reasoning = include_reasoning
        self.store_payloads = store_payloads
        self.prompt_caching = prompt_caching
        self.rpm_per_person = rpm_per_person


#: The three workloads the owner named, plus the one that measures the gateway alone.
#:
#: The people counts are the top of the stated range — 300 agentic seats — because a capacity
#: statement made at the comfortable end of a range is a capacity statement about the comfortable
#: end. `people` is how many *keys* exist; how many are active at once is the profile's concurrency.
WORKLOADS: tuple[Workload, ...] = (
    Workload(
        "agentic",
        "Load test — agentic coding",
        models=("sim-coder",),
        people=300,
        tools_enabled=True,
        include_reasoning=True,
        prompt_caching=True,
    ),
    Workload(
        "chat",
        "Load test — chat with a RAG",
        models=("sim-chat", "sim-chat-batched"),
        people=200,
        include_reasoning=False,
    ),
    Workload(
        "embed",
        "Load test — nightly embedding batches",
        models=("sim-embed",),
        people=8,
        # **Texts per minute, not requests per minute.** A rate limit counts a request's *weight*
        # and a batch weighs its own size (`FRD-405` §4.2), so an allowance of 600 "requests" a
        # minute is 4.7 batches of 128 — measured, as 99.2% of a nightly-batch step refused with
        # `RESOURCE_EXHAUSTED`. An installation sizing this for a re-embedding job has the same
        # arithmetic to do, and the unit is the trap. Set ten times higher than a realistic nightly
        # job needs, so that what this profile measures is the gateway and not the limiter.
        rpm_per_person=128 * 600,
        # A batch job's bodies are the corpus. Storing them would measure the disk, and no
        # installation stores a nightly re-embedding of its document store.
        store_payloads=False,
    ),
    Workload(
        "bare",
        "Load test — the gateway's own cost",
        models=("sim-instant",),
        people=64,
        include_reasoning=False,
    ),
)

BY_SLUG = {workload.slug: workload for workload in WORKLOADS}


def _catalog_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model in CHAT_MODELS:
        capabilities = ["generate"]
        if model.supports_tools:
            capabilities.append("tools")
        thinking = None
        if model.reasoning_share > 0:
            capabilities.append("thinking")
            # The words this dialect takes, and `disabled` because the double honours
            # `reasoning_effort: "none"` by not thinking — measured in `modelsim._output_plan`,
            # which is the only sense in which a double can be measured.
            thinking = {
                "modes": ["disabled"],
                "levels": ["low", "medium", "high"],
                "default": {"mode": "high"},
            }
        rows.append(
            {
                "model": model.name,
                "display_name": model.display_name,
                "provider": PROVIDER,
                "publisher": "local",
                "platform": PROVIDER,
                "hosting": "self_deployed",
                "input_price_per_million_nanos": model.input_price_nanos,
                "output_price_per_million_nanos": model.output_price_nanos,
                "capabilities": capabilities,
                "thinking": thinking,
                "context_window": model.context_window,
                "max_output_tokens": model.max_output_tokens,
                "default_max_output_tokens": model.default_max_tokens,
                "approved": True,
            }
        )
    for embed in EMBEDDING_MODELS:
        rows.append(
            {
                "model": embed.name,
                "display_name": embed.display_name,
                "provider": PROVIDER,
                "publisher": "local",
                "platform": PROVIDER,
                "hosting": "self_deployed",
                "input_price_per_million_nanos": embed.input_price_nanos,
                "output_price_per_million_nanos": embed.input_price_nanos,
                "capabilities": ["embed"],
                "embedding": {
                    "supports_batch": True,
                    "dimensions": [embed.dimensions],
                    "default": embed.dimensions,
                },
                "approved": True,
            }
        )
    return rows


def _bind(row: dict[str, Any]) -> dict[str, Any]:
    """JSON columns need a string on the raw-connection path this script uses."""
    return {
        key: json.dumps(value) if isinstance(value, dict | list) else value
        for key, value in row.items()
    }


async def _align_sequences(connection: Any) -> None:
    """Put the identity sequences back where the rows are.

    `budgets` and `rate_limits` are written by the Kafka consumer with **explicit** ids, which
    leaves each table's sequence behind the rows it holds. The next insert that lets the sequence
    choose then collides, and the message names a primary key rather than the cause. Not this
    module's defect to fix — the consumer is the authority for those rows — but it is this
    module's to survive.
    """
    for table in ("budgets", "rate_limits"):
        await connection.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'),"
                f" COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
            )
        )


async def create(connection: Any) -> None:
    await _align_sequences(connection)
    for row in _catalog_rows():
        await connection.execute(
            text("DELETE FROM model_catalog WHERE model = :model"), {"model": row["model"]}
        )
        columns = ", ".join(row)
        placeholders = ", ".join(f":{name}" for name in row)
        await connection.execute(
            text(f"INSERT INTO model_catalog ({columns}) VALUES ({placeholders})"), _bind(row)
        )
    print(f"declared {len(_catalog_rows())} simulated models")

    for workload in WORKLOADS:
        await connection.execute(
            text("DELETE FROM use_cases WHERE slug = :slug"), {"slug": workload.slug}
        )
        await connection.execute(
            text(
                "INSERT INTO use_cases (slug, name, description, retention_days, store_payloads,"
                " tools_enabled, include_reasoning, prompt_caching_enabled, allowed_models)"
                " VALUES (:slug, :name, :description, 1, :store_payloads, :tools_enabled,"
                " :include_reasoning, :prompt_caching, :allowed_models)"
            ),
            {
                "slug": workload.slug,
                "name": workload.name,
                "description": "Created by tools/loadtest. Synthetic traffic, no real content.",
                "store_payloads": workload.store_payloads,
                "tools_enabled": workload.tools_enabled,
                "include_reasoning": workload.include_reasoning,
                "prompt_caching": workload.prompt_caching,
                # The release (`FRD-308`) is named rather than left open, so a run cannot reach a
                # cloud model even if one were configured — the second half of FR-2.
                "allowed_models": json.dumps(list(workload.models)),
            },
        )

        await connection.execute(
            text("DELETE FROM api_keys WHERE use_case = :slug"), {"slug": workload.slug}
        )
        await connection.execute(
            text("DELETE FROM use_case_members WHERE use_case_slug = :slug"),
            {"slug": workload.slug},
        )
        for index in range(workload.people):
            full = key_for(workload.slug, index)
            prefix = full.split("_")[1]
            await connection.execute(
                text(
                    "INSERT INTO api_keys (id, prefix, key_hash, subject, label, use_case,"
                    " is_active) VALUES (:id, :prefix, :hash, :subject, :label, :slug, true)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "prefix": prefix,
                    "hash": hashlib.sha256(full.encode()).hexdigest(),
                    "subject": subject_for(workload.slug, index),
                    "label": f"loadtest {workload.slug} {index}",
                    "slug": workload.slug,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO use_case_members (id, use_case_slug, subject, role)"
                    " VALUES (:id, :slug, :subject, 'user')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "slug": workload.slug,
                    "subject": subject_for(workload.slug, index),
                },
            )

        # Budgets and rate limits are **configured, generous and enabled**. A run with the controls
        # switched off measures a gateway nobody deploys; one with them tight measures the refusal
        # path. What is wanted is every control doing its work and admitting the request.
        await connection.execute(
            text("DELETE FROM budgets WHERE use_case = :slug"), {"slug": workload.slug}
        )
        await connection.execute(
            text(
                "INSERT INTO budgets (use_case, scope, subject, period, limit_tokens,"
                " limit_cost_nanos, enabled) VALUES (:slug, 'use_case', '', 'day',"
                " 2000000000, 100000000000000, true)"
            ),
            {"slug": workload.slug},
        )
        await connection.execute(
            text("DELETE FROM rate_limits WHERE use_case = :slug"), {"slug": workload.slug}
        )
        await connection.execute(
            text(
                "INSERT INTO rate_limits (use_case, scope, subject, limit_rpm, burst, enabled)"
                " VALUES (:slug, 'use_case', '', :rpm, :burst, true)"
            ),
            {
                "slug": workload.slug,
                "rpm": workload.people * workload.rpm_per_person,
                # **The burst has to be at least the largest batch**, because a batch weighs its
                # own size (`FRD-405` §4.2) and a request that cannot fit the bucket's capacity is
                # refused immediately however long the caller waits. Seeded at `people * 10` this
                # read `burst 80` for the embedding use case, and every 128-text batch came back
                # `RESOURCE_EXHAUSTED` — the rpm was 4 800 and irrelevant. Kept as a floor rather
                # than a note, because the same arithmetic waits for any installation running a
                # nightly re-embedding.
                "burst": max(workload.people * 10, MAX_BATCH_WEIGHT),
            },
        )
        print(f"seeded {workload.slug}: {workload.people} keys, budget and rate limit enabled")


async def clean(connection: Any) -> None:
    like = f"{PREFIX}%"
    for statement, params in (
        ("DELETE FROM request_logs WHERE use_case LIKE :like", {"like": like}),
        # `budget_usage` is keyed by a **scope key**, not by a use case: `uc:<slug>` for a use
        # case's own bucket and `member:<slug>:<subject>` for a person's (`FRD-400`). Matching the
        # column name that every other table here uses fails with `UndefinedColumn`, which is the
        # cheap version of this mistake — the expensive one is a pattern that matches nothing and
        # leaves a run's spend counted against a bucket forever.
        ("DELETE FROM budget_usage WHERE scope_key LIKE :key", {"key": f"%{PREFIX}%"}),
        ("DELETE FROM budgets WHERE use_case LIKE :like", {"like": like}),
        ("DELETE FROM rate_limits WHERE use_case LIKE :like", {"like": like}),
        ("DELETE FROM use_case_members WHERE use_case_slug LIKE :like", {"like": like}),
        ("DELETE FROM api_keys WHERE use_case LIKE :like", {"like": like}),
        ("DELETE FROM pipeline_configs WHERE use_case LIKE :like", {"like": like}),
        ("DELETE FROM access_suspensions WHERE use_case LIKE :like", {"like": like}),
        ("DELETE FROM use_cases WHERE slug LIKE :like", {"like": like}),
        (
            "DELETE FROM model_catalog WHERE provider = :provider",
            {"provider": PROVIDER},
        ),
    ):
        result = await connection.execute(text(statement), params)
        print(f"{statement.split(' FROM ')[1].split(' ')[0]:<20} removed {result.rowcount}")


async def reclaim(engine: Any) -> None:
    """Give the space back. A run writes tens of thousands of rows carrying whole prompts —
    50 000 of them measured at 100 MB — and deleting them only marks the pages reusable by this
    one table. `VACUUM` cannot run inside a transaction, so it needs a connection of its own."""
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text("VACUUM (ANALYZE) request_logs"))
    print("vacuumed request_logs")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true", help="remove everything the seed made")
    arguments = parser.parse_args()

    engine = build_engine(GatewaySettings().database_url(use_sqlite=False))
    try:
        async with engine.begin() as connection:
            if arguments.clean:
                await clean(connection)
            else:
                await create(connection)
        if arguments.clean:
            await reclaim(engine)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
