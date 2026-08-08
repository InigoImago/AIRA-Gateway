# Pointing OpenCode at AIRA (`FRD-132` stage A)

This is a **measurement harness**, not a supported integration. The question it exists to answer:
*can a real coding assistant be served by the surface AIRA already has, or does it need a new one?*
Choosing that from documentation is how a contract gets built on a guess and maintained forever —
so the answer comes from a client that actually sends requests.

## Why the Gemini surface, and why `@ai-sdk/google`

AIRA serves `/v1beta/models/{model}:generateContent`, which is Google's shape. OpenCode's providers
are AI SDK packages with an overridable `baseURL`, so `@ai-sdk/google` pointed at the gateway is the
cheapest thing that could possibly work. If it does, no new surface is needed at all (`FRD-132` B1).
If it does not, what breaks — and exactly where — is the evidence that decides between an
OpenAI-compatible surface (B2, reviving the withdrawn `FRD-106`) and an Anthropic-shaped one (B3).

## Running it

```bash
# 1. A use case and a key. Keys are always bounded (ADR-0015); 30 days is the default.
#    Issue one in the console, or:
curl -sX POST http://localhost:8002/api/v1/use-cases/coding-assistant/api-keys/ \
     -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"label":"opencode","expires_in_days":30}'

export AIRA_API_KEY=aira_...

# 2. Point OpenCode at this config and ask it something small.
cd <a scratch project>
OPENCODE_CONFIG=/path/to/AIRA/tools/opencode/opencode.json opencode run "list the files here"
```

The key is read from the environment on purpose — a credential in a file that lives in the
repository is the thing `FRD-406` masks out of stored payloads, and it should not be here either.

## What to write down

Per `FRD-132` §3, and **the output rather than a summary of it**: which request shape the client
sends, which auth header, where the first failure lands and what it says, how many requests one
instruction produces, and how many tokens. The last two calibrate `FRD-405` limits and `FRD-400`
budgets for a use-case shape that makes many model calls per human instruction.

## What is expected to fail, today

Tool calling. `tools` is refused with **400** at the surface (`FRD-131` builds it, per use case and
off by default). Reaching that refusal is a successful stage-A run: it means the path, the auth and
the streaming all worked, and the one missing capability is the one already written up.
