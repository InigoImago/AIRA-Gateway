"""Declare the local models in the gateway's read-model, for a live walk-through (FRD-123).

Management is the authority for the catalog and publishes over Kafka; this writes the same rows
directly, because what a *demonstration* needs is the declaration in place, and the distribution
path has its own suite (`tests/integration/test_model_declaration.py`).

**Every price here is invented and the display name says so.** A local model costs no money. The
price is what makes `FRD-403` demonstrable end to end — the cost column, spend budgets, the
reporting screen — and the distinction between a demonstration figure and a spend figure has to
survive a screenshot, so it lives in the data.

    uv run python tools/seed_local_catalog.py
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import text

from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine

CHAT_MODEL = os.environ.get("AIRA_SEED_LOCAL_CHAT_MODEL", "qwen3:0.6b")
EMBED_MODEL = os.environ.get("AIRA_SEED_LOCAL_EMBED_MODEL", "all-minilm")
REGION = os.environ.get("AIRA_OLLAMA_REGION", "on-premises")

#: Nano-units per million tokens. Chosen to look like a small cloud model, so the arithmetic in a
#: report is recognisable, and so a budget set in the UI actually bites inside a demonstration.
CHAT_INPUT = 100_000_000
CHAT_OUTPUT = 400_000_000
EMBED_PRICE = 10_000_000

DECLARATIONS = [
    {
        "model": CHAT_MODEL,
        "display_name": "Local chat model [fictitious price]",
        "input_price_per_million_nanos": CHAT_INPUT,
        "output_price_per_million_nanos": CHAT_OUTPUT,
        # **Measured** against the running model, not guessed (2026-08-06):
        #   - `response_format: json_schema` is honoured — a schema request came back
        #     `{ "colour": "red" }` with finish `stop`.
        #   - `reasoning_effort` is accepted, and the thinking is billed **inside**
        #     `completion_tokens`: a one-word answer cost 109 of them.
        # `FRD-114` FR-7 says absence of information is not permission; the converse is that
        # presence of *evidence* is what a declaration should rest on.
        "capabilities": ["generate", "structured_output", "thinking"],
        # This dialect takes an effort level and no token budget, so `limited` is deliberately not
        # offered — the adapter refuses it rather than rounding it (`FRD-111` §5.2).
        "thinking": {
            "modes": ["disabled", "minimal", "low", "medium", "high"],
            "default": {"mode": "disabled"},
        },
        "publisher": "local",
        "platform": "ollama",
        "hosting": "self_deployed",
        "max_output_tokens": 4096,
        "default_max_output_tokens": 512,
        "numeric_id": 9001,
    },
    {
        "model": EMBED_MODEL,
        "display_name": "Local embedding model [fictitious price]",
        "input_price_per_million_nanos": EMBED_PRICE,
        "output_price_per_million_nanos": EMBED_PRICE,
        "capabilities": ["embed"],
        "publisher": "local",
        "platform": "ollama",
        "hosting": "self_deployed",
        # The wire format takes the whole batch in one `input` array, so batching is real here.
        # Task types are **not** declared, because this dialect has none — and `FRD-113` refuses an
        # undeclared one rather than sending a field the endpoint would ignore.
        "embedding": {"supports_batch": True},
        "numeric_id": 9002,
    },
]


async def main() -> None:
    engine = build_engine(GatewaySettings().database_url(use_sqlite=False))
    try:
        async with engine.begin() as connection:
            for row in DECLARATIONS:
                await connection.execute(
                    text("DELETE FROM model_catalog WHERE model = :model"), {"model": row["model"]}
                )
                columns = ", ".join(row)
                placeholders = ", ".join(f":{name}" for name in row)
                await connection.execute(
                    text(f"INSERT INTO model_catalog ({columns}) VALUES ({placeholders})"),
                    _bind(row),
                )
                print(f"declared {row['model']}")
    finally:
        await engine.dispose()


def _bind(row: dict[str, object]) -> dict[str, object]:
    """JSON columns need a string on the raw-connection path this script uses."""
    import json

    return {
        key: json.dumps(value) if isinstance(value, dict | list) else value
        for key, value in row.items()
    }


if __name__ == "__main__":
    asyncio.run(main())
