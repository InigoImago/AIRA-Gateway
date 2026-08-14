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

#: The two KIRA-style integer ids this script owns. They name a **role** — "the local chat model",
#: "the local embedding model" — not a particular model, so re-running for a different model
#: *moves* the id rather than adding a second claim to it. Fixed rather than derived because a
#: caller's configuration holds the number, and changing it would break them silently.
CHAT_NUMERIC_ID = 9001
EMBED_NUMERIC_ID = 9002


CHAT_MODEL = os.environ.get("AIRA_SEED_LOCAL_CHAT_MODEL", "qwen3:0.6b")
EMBED_MODEL = os.environ.get("AIRA_SEED_LOCAL_EMBED_MODEL", "all-minilm")
REGION = os.environ.get("AIRA_OLLAMA_REGION", "on-premises")

#: Nano-units per million tokens. Chosen to look like a small cloud model, so the arithmetic in a
#: report is recognisable, and so a budget set in the UI actually bites inside a demonstration.
CHAT_INPUT = 100_000_000
CHAT_OUTPUT = 400_000_000
EMBED_PRICE = 10_000_000

#: What each model was **measured** to do, on 2026-08-08 against this Ollama. Keyed by model,
#: because two models of the *same family on the same server in the same minute* answered the same
#: field differently — and a declaration is a claim about a model, not about a vendor.
#:
#:   qwen3:0.6b  `reasoning_effort: "none"` → 3 completion tokens, content "OK." — genuinely off.
#:   qwen3:4b    `reasoning_effort: "none"` → 103 tokens and 480 characters of raw chain-of-thought
#:               **as the answer**, with no separate `reasoning` field. `none` does not mean "do
#:               not think" on this model; it means "do not emit a separate reasoning channel", so
#:               the thoughts become the answer. `disabled` is therefore **not declared** for it —
#:               `FRD-111` then refuses a request asking for it by name, which is a far better
#:               outcome than a 200 carrying somebody's reasoning.
#:   both        `minimal` is refused **by name** by the server (`high|medium|low|max|none`).
#:
#: A model that is not in this table gets **no thinking declaration at all**: absence of
#: information is not permission (`FRD-114` FR-7), and the baseline is that the model thinks
#: however it likes and the gateway strips the separate reasoning field.
#:
#: `TOOLS_BY_MODEL` below exists for the same reason and was added the same evening, after making
#: the mistake it prevents: `qwen2.5-coder:7b` lists `tools` in `ollama show` and **does not do
#: them** — asked to call a function it returns the JSON *as prose*, with `tool_calls: null` on the
#: wire. Its siblings `qwen3:0.6b` and `qwen2.5:0.5b` both answer with a real call, so it is
#: neither the family nor the size; it is that particular build's template. A vendor's capability
#: flag is a claim, and this catalog is supposed to hold evidence.
THINKING_BY_MODEL: dict[str, dict] = {
    "qwen3:0.6b": {
        "modes": ["disabled", "low", "medium", "high"],
        "default": {"mode": "disabled"},
    },
    "qwen3:4b": {
        "modes": ["low", "medium", "high"],
    },
}


#: Which models were **seen** to emit a real tool call, by sending one (`FRD-131`). Absent means
#: the capability is not declared, so the dispatch chain refuses a tool request against that model
#: **by name** — far better than prose a client will try to parse as a function call.
#:
#: Entries are added **after** a run, never in anticipation of one. `qwen2.5:7b` was written here
#: while it was still downloading and taken out again before the file was saved — the fourth
#: instance in one evening of the same reflex, which is why the rule is stated rather than assumed.
#:
#: **`qwen2.5:0.5b` is deliberately absent, and it is the most instructive entry on this list.**
#: It emitted a correct tool call the first time it was asked, which was used to argue that the
#: whole family can do tool calling at any size. Asked again it answered in 124 tokens of prose;
#: asked twice more it called, with **invented arguments** — once naming a parameter (`file_path`)
#: that is not in the declared schema at all. One successful call is not a capability. Measured
#: 2026-08-08:
#:
#:     qwen2.5:0.5b   inconsistent   0.8–4.2s   24–124 tokens   invents paths, violates the schema
#:     qwen2.5:1.5b   calls          1.9s        23 tokens      invented path
#:     qwen2.5:3b     calls          2.0–4.4s    21 tokens      correct: "hello.py"
#:     qwen2.5:7b     calls          6.3s        21 tokens      correct — and three times slower
#:     qwen2.5-coder:7b  **never**   —           —              prose, `tool_calls: null`
TOOLS_BY_MODEL: frozenset[str] = frozenset(
    {"qwen3:0.6b", "qwen3:4b", "qwen2.5:1.5b", "qwen2.5:3b", "qwen2.5:7b"}
)


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
        #
        # **Which modes, though, is a property of the model and not of the family** — see
        # `THINKING_BY_MODEL` below. This entry used to hard-code one set for whatever model was
        # configured, which is how a fact measured against a 0.6B model came to be asserted about
        # a 4B one that behaves differently.
        "thinking": None,  # filled in from the measured table
        "publisher": "local",
        "platform": "ollama",
        "hosting": "self_deployed",
        # Measured, and it had not been — see the note beside the same field in the Management
        # seed. `ollama show` reports `qwen3.context_length = 40960`; the runtime accepts any
        # `max_tokens` and truncates at the window, so the window is the only honest ceiling. The
        # `4096` that stood here refused an agentic coding client's ordinary request.
        "max_output_tokens": 40960,
        "default_max_output_tokens": 512,
        "approved": True,
        "numeric_id": CHAT_NUMERIC_ID,
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
        # Measured against this Ollama, not taken from the model card: two texts of very
        # different length both came back with **384** values. `all-minilm` is the only
        # embedding model this seed declares, and a second one would need its own measurement
        # — a dimension is a property of a model, not of a runtime (`FRD-114` FR-7).
        #
        # Declared because the compatibility surface reports it in `GET /models`, and a client
        # sizing a vector store reads that field. Absent, it had to guess or embed once and
        # count, which is the thing this catalog exists to spare it.
        "embedding": {"supports_batch": True, "dimensions": [384], "default": 384},
        "approved": True,
        "numeric_id": EMBED_NUMERIC_ID,
    },
]


async def main() -> None:
    engine = build_engine(GatewaySettings().database_url(use_sqlite=False))
    try:
        async with engine.begin() as connection:
            for row in DECLARATIONS:
                # Measured, never inherited and never taken from a vendor's own flag: `ollama show`
                # lists `tools` for `qwen2.5-coder:7b`, which answers in prose.
                if str(row["model"]) in TOOLS_BY_MODEL:
                    row["capabilities"] = [*row["capabilities"], "tools"]  # type: ignore[misc]
                if "thinking" in row:
                    # Measured or absent — never inherited from a sibling model.
                    measured = THINKING_BY_MODEL.get(str(row["model"]))
                    if measured is None:
                        row.pop("thinking")
                        row["capabilities"] = [
                            c
                            for c in row["capabilities"]
                            if c != "thinking"  # type: ignore[union-attr]
                        ]
                        print(
                            f"note: {row['model']} has no measured thinking modes — declaring none"
                        )
                    else:
                        row["thinking"] = measured
                await connection.execute(
                    text("DELETE FROM model_catalog WHERE model = :model"), {"model": row["model"]}
                )
                # …and release the id from whatever held it before. The id names a *role*, so a
                # second run for a different chat model has to move it — leaving both rows was the
                # 2026-08-08 defect: two entries claiming 9001, the resolver unable to answer, and
                # every KIRA request naming that id refused. Silent, because the seed printed
                # success and the read-model has no unique constraint (Management does, but this
                # script writes past it).
                await connection.execute(
                    text(
                        "UPDATE model_catalog SET numeric_id = NULL"
                        " WHERE numeric_id = :id AND model <> :model"
                    ),
                    {"id": row["numeric_id"], "model": row["model"]},
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
