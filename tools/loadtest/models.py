"""The simulated models a load run addresses (`FRD-136` FR-1).

One declaration, read by three processes that must agree about it: the double that answers as the
model, the seeder that writes the catalogue row, and the driver that decides what to ask for. Two
copies of a token price would make a spend figure meaningless in a way no assertion would catch.

**Every number here is invented**, and the display names say so, exactly as
`tools/seed_local_catalog.py` does. What is *not* invented is the shape: a declared time to first
token and a declared token rate, so that the gateway's own cost is what remains when they are
subtracted (`FRD-136` FR-5).
"""

from __future__ import annotations

from dataclasses import dataclass

#: The provider name the gateway knows these by — one entry in `AIRA_OPENAI_SERVERS`, so the real
#: OpenAI-dialect adapter, transport and streaming are on the path (`FRD-123`).
PROVIDER = "modelsim"

#: Inside the stack's network the double always listens here; which host port that is published on
#: is `tools/loadtest/addresses.py`'s question, not this module's.
CONTAINER_PORT = 8099
CONTAINER = "modelsim"

#: Marker a driver puts in a prompt to make the double answer with a tool call. The prompt is the
#: only channel that survives the canonical mapping — a header or an unknown body field does not,
#: and a control that does not reach the double is a control that silently does nothing.
TOOL_MARKER = "<<sim:tool>>"

#: What a tool call costs instead of an answer. A model that decides to call a function stops
#: generating prose, so a turn that ends in one is **short** — and a driver that subtracted the
#: full answer's promised time from it would report a large negative overhead for every such turn.
TOOL_CALL_TOKENS = 12


@dataclass(frozen=True, slots=True)
class SimModel:
    """A model that generates, and the latency it promises to have."""

    name: str
    display_name: str
    #: Time to first token, milliseconds. What a real model spends reading the prompt.
    ttft_ms: float
    #: How fast the answer arrives once it starts.
    tokens_per_second: float
    #: Tokens per streamed event. One is what the large vendors send; more is what a batching
    #: runtime sends, and it is the difference between 18 000 and 4 500 SSE events per second at
    #: 300 streams — a figure about the *gateway*, so it is declared rather than incidental.
    tokens_per_chunk: int = 1
    #: What share of the output is thinking (`FRD-135`). Counted in `reasoning_tokens` and carried
    #: in the `reasoning` field, which is what a reasoning model on this dialect does.
    reasoning_share: float = 0.0
    #: What the double emits when the caller names no cap.
    default_max_tokens: int = 256
    context_window: int = 200_000
    max_output_tokens: int = 32_000
    supports_tools: bool = True
    #: Nano-units per million tokens. Invented, like `seed_local_catalog`'s, so that budgets and
    #: the spend report engage under load instead of being skipped as unpriced.
    input_price_nanos: int = 300_000_000
    output_price_nanos: int = 1_200_000_000

    @property
    def seconds_per_chunk(self) -> float:
        if self.tokens_per_second <= 0:
            return 0.0
        return self.tokens_per_chunk / self.tokens_per_second

    def plan(self, max_tokens: int | None, *, thinking: bool = True) -> tuple[int, int]:
        """How the double will split an answer into visible tokens and thinking.

        Shared with the driver rather than reimplemented there: the driver subtracts what this
        promises, and two copies of the arithmetic would make the subtraction quietly wrong.
        """
        total = max_tokens if max_tokens and max_tokens > 0 else self.default_max_tokens
        total = min(total, self.max_output_tokens)
        reasoning = int(total * self.reasoning_share) if thinking else 0
        return total - reasoning, reasoning

    def declared_seconds(self, answer_tokens: int, reasoning_tokens: int = 0) -> float:
        """What this model promises one answer will take.

        The figure `FRD-136` FR-5 subtracts, and it counts **events, not tokens**: the double
        emits `ceil(n / tokens_per_chunk)` of them for each of the two streams and waits one gap
        before each, so a model batching eight tokens to an event owes eight times fewer gaps. The
        formula here was wrong by one gap per stream until it was checked against the clock
        (measured: 7.020 s against a promised 6.928 s), which is the whole argument for a double
        whose promise is verified rather than assumed.
        """
        if self.tokens_per_second <= 0:
            return self.ttft_ms / 1000.0
        per_chunk = max(1, self.tokens_per_chunk)
        chunks = -(-answer_tokens // per_chunk) + -(-reasoning_tokens // per_chunk)
        return self.ttft_ms / 1000.0 + chunks * self.seconds_per_chunk


@dataclass(frozen=True, slots=True)
class SimEmbeddingModel:
    """A model that embeds. Its cost is per text, not per token, which is what a batch tests."""

    name: str
    display_name: str
    dimensions: int
    #: Fixed cost of the call, and the marginal cost of each text in the batch.
    base_ms: float = 20.0
    ms_per_text: float = 0.8
    max_batch: int = 256
    input_price_nanos: int = 20_000_000


#: The generating models. Four, and each exists to answer a different question.
CHAT_MODELS: tuple[SimModel, ...] = (
    # No latency at all: with the upstream contributing nothing, everything the clock shows is the
    # gateway. This is the model that answers "what does one request cost us".
    SimModel(
        name="sim-instant",
        display_name="Simulated — answers instantly (a double, not a model)",
        ttft_ms=0.0,
        tokens_per_second=0.0,
        default_max_tokens=32,
        reasoning_share=0.0,
    ),
    # Agentic coding: slow to start, thinks, carries tools, streams token by token.
    SimModel(
        name="sim-coder",
        display_name="Simulated — coding model with reasoning (a double, not a model)",
        ttft_ms=900.0,
        tokens_per_second=55.0,
        tokens_per_chunk=1,
        reasoning_share=0.4,
        default_max_tokens=600,
        context_window=400_000,
        input_price_nanos=300_000_000,
        output_price_nanos=1_500_000_000,
    ),
    # A RAG chatbot's model: large prompt, quick short answer, no thinking.
    SimModel(
        name="sim-chat",
        display_name="Simulated — chat model (a double, not a model)",
        ttft_ms=350.0,
        tokens_per_second=90.0,
        tokens_per_chunk=2,
        reasoning_share=0.0,
        default_max_tokens=220,
        supports_tools=False,
        input_price_nanos=100_000_000,
        output_price_nanos=400_000_000,
    ),
    # A model that batches its stream, so the SSE event rate is a quarter of the token rate. The
    # comparison with `sim-coder` is the whole point of `tokens_per_chunk` being declared.
    SimModel(
        name="sim-chat-batched",
        display_name="Simulated — chat model, 8 tokens per event (a double, not a model)",
        ttft_ms=350.0,
        tokens_per_second=90.0,
        tokens_per_chunk=8,
        reasoning_share=0.0,
        default_max_tokens=220,
        supports_tools=False,
        input_price_nanos=100_000_000,
        output_price_nanos=400_000_000,
    ),
)

EMBEDDING_MODELS: tuple[SimEmbeddingModel, ...] = (
    SimEmbeddingModel(
        name="sim-embed",
        display_name="Simulated — embedding model (a double, not a model)",
        dimensions=1024,
        base_ms=20.0,
        ms_per_text=0.8,
    ),
)

BY_NAME: dict[str, SimModel] = {model.name: model for model in CHAT_MODELS}
EMBEDDING_BY_NAME: dict[str, SimEmbeddingModel] = {model.name: model for model in EMBEDDING_MODELS}


def server_spec(url: str, region: str = "") -> str:
    """The `AIRA_OPENAI_SERVERS` entry that puts every simulated model on the gateway.

    One entry, so every row on the audit trail names `modelsim` as its provider and a load run is
    separable from anything else the stack did.

    **No region by default.** A declared one is checked against `AIRA_ALLOWED_REGIONS` at startup
    and the default list is the EU's real regions (`residency.DEFAULT_ALLOWED_REGIONS`), so an
    invented datacentre name here would stop the gateway booting rather than fail the run.
    """
    chat = ",".join(model.name for model in CHAT_MODELS)
    embed = ",".join(model.name for model in EMBEDDING_MODELS)
    return f"{PROVIDER}={url}|{chat}|{embed}|{region}"
