# FRD-143 — Current Gemini clients, without losing the older ones

> Phase: 8 (KIRA parity) · Status: **Built** · Owner: Vadim Scheibe
> Origin: the owner's integration on Google Cloud with OpenCode: tools declared in
> `parametersJsonSchema` were refused, Claude's tools sent `strict` broke an organisation policy,
> and a gateway with a missing key file crash-looped while the console blamed the provider list.
> The owner's constraint: older CLIs on the Gemini interface must keep working.
> Related: `FRD-131` (tools), `FRD-112` (structured output), `FRD-111` (thinking), `FRD-142`
> (Google identities), `FRD-141` (why a provider refused).

## 1. Summary

Google's API takes a schema two ways — the OpenAPI subset (`parameters`, `responseSchema`) and JSON
Schema (`parametersJsonSchema`, `responseJsonSchema`) — and current clients send the second: the AI
SDK's Google provider declares every tool in JSON Schema since its fourth major, and asks for Gemini
3's `thinkingLevel`. Its first major, which older agents still run, sends a single tool as an object.
This surface accepts all of it, carries JSON Schema to every dialect as written, and proves it with
the real clients of every generation.

## 2. Goals & Non-Goals

**Goals**
- Every major of the AI SDK's Google provider and the `google-genai` SDK work unmodified: a tool call
  and its result, structured output, a stream.
- JSON Schema is carried as written — bounded, never translated, never dropped.
- Claude on Vertex is not sent a feature an organisation policy forbids by default.
- On Google Cloud, a gateway with nothing configured uses the machine's account.

**Non-Goals**
- `toolConfig` modes other than `AUTO` (including the fourth major's `VALIDATED` for strict tools);
  refused by name as before.
- Translating JSON Schema into the OpenAPI subset, or checking it against a dialect: a schema a
  provider cannot take is its 400, with its reason.

## 4. Functional Requirements

- **FR-1 — `parametersJsonSchema`** (and `parameters_json_schema`) on a function declaration,
  exclusive with `parameters`; bounded like a `responseSchema` (bytes, depth, properties); carried as
  `parametersJsonSchema` to Gemini, as `input_schema` to Anthropic (never `strict`), as `parameters`
  to the OpenAI dialect; counted in the reservation.
- **FR-2 — `responseJsonSchema`**, exclusive with `responseSchema`, requiring `responseMimeType`
  `application/json`; carried as `responseJsonSchema`, as Anthropic's `output_config` schema, as an
  OpenAI `json_schema` response format with `strict: false`; asks the model for structured output and
  treats an incomplete document as `FRD-112` does.
- **FR-3 — `thinkingLevel`** (and `thinking_level`) is the canonical mode — the model's own level word,
  checked against the levels the catalogue declares — exclusive with a budget.
- **FR-4 — `tools`** as a list or a single tool object.
- **FR-5 — Claude's tools strict only on request**: `AIRA_VERTEX_PARTNER_STRICT_TOOLS` (default
  `false`). On Vertex a strict tool is the partner-model feature `structured_outputs`, which
  `constraints/vertexai.allowedPartnerModelFeatures` forbids unless allowed. A `FAILED_PRECONDITION`
  is explained as possibly that policy (`FRD-141`).
- **FR-6 — The machine's account when nothing is configured.** With `AIRA_VERTEX_PROJECT` and no
  credential, the gateway asks the metadata server at start (one second, never through a proxy, its
  answer recognised by Google's `Metadata-Flavor` header) and uses the machine's account. A credential
  that is configured and unreadable stops the start, saying how to fix or unset it — never a silent
  switch to another identity.
- **FR-7 — The console** says that the service behind it did not answer when a proxy answers 502, 503
  or 504 without an envelope, beside what the page was asking for.
- **FR-8 — Real clients in CI**: `tools/clients` runs every provider major with its `ai` release, and
  `google-genai`, against the app on a socket, records what each sent, and fails rather than skips in
  the `clients` job (`make test-clients`), which gates a release.

## 8. Security & Privacy

A JSON Schema document is bounded before it is carried. The metadata probe is direct and short, and
cannot change a configured identity. Nothing new is stored or transmitted about a person.

## 10. Testing & Acceptance Criteria

`gateway/tests/test_modern_gemini_clients.py` (hermetic), `gateway/tests/test_real_gemini_clients.py`
(the real clients). The OpenCode binary, 1.19.3 and 1.0.142, was run by hand against the app with the
mock model: a tool call, its result and the answer. Those are both **OpenCode 1**; the owner's
rollout was OpenCode 2 (`@opencode/cli`), a different package with a Gemini client of its own, which
`FRD-144` measured and put in the suite — it declares tools in `parametersJsonSchema`, the case FR-1
built. Mutations `MG-1`–`MG-22`.
