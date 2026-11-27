# Calling AIRA from your framework

Small programs that call AIRA through the libraries people already use for Google's Gemini API —
Google's SDKs, LangChain, LlamaIndex, LangChain4j, Spring AI, the Vercel AI SDK and others — and
through plain HTTP. AIRA serves the Gemini API's shape, so a stock client works once it is told
three things. Each example shows where those three go, with every way of signing in.

## How the pieces connect

```
your program ──► AIRA gateway ──► the model, at the provider the catalogue names for it
                 reads the credential; applies the use case's models, budget and rules

POST <gateway>/uc/<use-case>/v1beta/models/<model>:generateContent
     └──── the address ────┘               └──┬──┘
                                          the model
+ the credential: x-goog-api-key: <AIRA API key>, or Authorization: Bearer <Keycloak token>
```

| What | Where it comes from | Where it goes in a client |
| --- | --- | --- |
| **The address** | The gateway's URL and the use case's slug: `<gateway>/uc/<use-case>`. The use case's **Overview** in the console shows it under *Connecting a client*. | The client's base URL. Most clients append `/v1beta/models/…` themselves; a few want `/v1beta` in the base URL (see [below](#what-each-client-needs-to-be-told)). A client that cannot change its path sends the header `X-AIRA-Use-Case: <use-case>` instead. |
| **The credential** | An **API key** from the use case's **API keys** tab; a **person's** Keycloak access token from a sign-in; or a **service account's** token from Keycloak's client-credentials grant. | The client's API-key setting, which sends `x-goog-api-key`, or an `Authorization: Bearer` header. |
| **The model** | A model released to the use case: **Overview → Models this use case may call**. | The model parameter, by name: `gemini-2.5-flash`, `gemini-embedding-001`, `gemini-2.5-flash-preview-tts`, … |

Every model call names its use case, whatever the credential. The use case decides which models
may be called and which budget, rate limits and rules apply; the credential decides who is calling.

## The credential forms

The gateway reads `Authorization: Bearer` first, then `x-goog-api-key`, then `?key=` in the URL,
and accepts a Keycloak token wherever it accepts a key. Each example takes `AUTH`; the curl script
instead runs every form whose variables are set.

| `AUTH=` | Credential | Sent as |
| --- | --- | --- |
| `key` (default) | an AIRA API key (`API_KEY`) | the client's API-key setting: `x-goog-api-key` |
| `query-key` | the same key; REST examples only | `?key=<key>`, Google's curl form. Every proxy on the way writes the URL, key included, into its log, so prefer the header. |
| `bearer` | a person's Keycloak access token (`ACCESS_TOKEN`) | `Authorization: Bearer <token>` |
| `service-account` | a token the example fetches from Keycloak with the client-credentials grant (`TOKEN_URL`, `CLIENT_ID`, `CLIENT_SECRET`) | `Authorization: Bearer <token>` |
| `token-as-key` | `ACCESS_TOKEN`, or else a service account's token | the client's API-key setting, for a client that has no header setting |

- **An API key** belongs to one use case and is shown once, when it is issued. It expires after
  30 days unless issued for longer, and after 180 at most.
- **A person's token** comes from your application's Keycloak sign-in (the authorization-code
  flow; the realm has the password grant switched off). The person must be a member of the use
  case, or in a group granted to it.
- **A service account** is a confidential Keycloak client with service accounts switched on. Its
  service account is in the group `/use-cases/<use-case>` (or in a group granted to the use case),
  and the client needs two mappers that a new client does not have: group membership with full
  paths, and the audience `aira-gateway`. The steps are in
  [the showcase guide](../docs/deployment/showcase.md#with-its-existing-keycloak-identity).
- **A token expires within minutes** (Keycloak's default). A long-running program fetches a new
  one — the client-credentials grant is a single call — or uses a key instead.
- **A token names the Keycloak address it was fetched from.** Fetch it from the address the gateway
  is configured with: on the local stack that is `http://localhost:8080`, and a token fetched
  through `127.0.0.1` is refused.
- **Where an SDK insists on a key** but the credential travels in a header, the examples pass the
  placeholder `unused`: the gateway reads the bearer header first.

## Settings shared by every example

| Variable | Meaning | Local stack |
| --- | --- | --- |
| `GATEWAY_URL` | the gateway's address | `http://localhost:8001` |
| `USE_CASE` | the use case's slug | |
| `AUTH` | which credential form, as above | `key` |
| `API_KEY` | an API key of that use case | |
| `ACCESS_TOKEN` | a person's Keycloak access token | |
| `TOKEN_URL` | Keycloak's token endpoint | `http://localhost:8080/realms/aira/protocol/openid-connect/token` |
| `CLIENT_ID`, `CLIENT_SECRET` | the service account's Keycloak client | |
| `MODEL` | a chat model released to the use case | `gemini-2.5-flash` |
| `EMBEDDING_MODEL` | an embedding model released to it | `gemini-embedding-001` |
| `SPEECH_MODEL` | a speech model released to it | `gemini-2.5-flash-preview-tts` |

Copy [`.env.example`](.env.example) to `.env`, fill it in, and load it with
`set -a; . ./.env; set +a`. None of the names starts with `AIRA_`: that prefix is the gateway's
own configuration.

## The examples

Each one prints what came back and exits non-zero, with the gateway's message, when a call is
refused. Run it from its own directory.

| Language | Framework | Example | Shows | Run |
| --- | --- | --- | --- | --- |
| shell | curl | [`curl/gemini.sh`](curl/gemini.sh) | every credential form that is set, the use case by path and by header, a stream, an embedding | `./gemini.sh` |
| Python | Google's `google-genai` | [`google_genai_chat.py`](python/google_genai_chat.py) | an answer and a stream | `python google_genai_chat.py` |
| Python | Google's `google-genai` | [`google_genai_embeddings.py`](python/google_genai_embeddings.py) | three texts embedded in one call, ranked by similarity | `python google_genai_embeddings.py` |
| Python | Google's `google-genai` | [`google_genai_speech.py`](python/google_genai_speech.py) | text to speech, written to `speech.wav` | `python google_genai_speech.py` |
| Python | Google's `google-genai` | [`google_genai_thinking.py`](python/google_genai_thinking.py) | the model's reasoning, asked for explicitly | `python google_genai_thinking.py` |
| Python | LangChain, `langchain-google-genai` | [`langchain_chat.py`](python/langchain_chat.py) | chat, a stream, an embedding | `python langchain_chat.py` |
| Python | LlamaIndex | [`llamaindex_rag.py`](python/llamaindex_rag.py) | an index embedded and queried, both models through the gateway | `python llamaindex_rag.py` |
| Python | `httpx`, the REST API by hand | [`rest_httpx.py`](python/rest_httpx.py) | every credential form including `?key=`, the use case by header (`USE_CASE_VIA=header`), a stream, an embedding, AIRA's response headers | `python rest_httpx.py` |
| Java | Google's `google-genai` | [`java/google-genai`](java/google-genai/src/main/java/Main.java) | an answer, a stream, an embedding | `mvn -q compile exec:java` |
| Java | LangChain4j | [`java/langchain4j`](java/langchain4j/src/main/java/Main.java) | an answer, a stream, an embedding | `mvn -q compile exec:java` |
| Java | Spring AI | [`java/spring-ai`](java/spring-ai/src/main/java/Main.java) | an answer, a stream, an embedding | `mvn -q compile exec:java` |
| JavaScript | Google's `@google/genai` | [`google-genai.mjs`](javascript/google-genai.mjs) | an answer, a stream, an embedding | `node google-genai.mjs` |
| JavaScript | Vercel AI SDK, `@ai-sdk/google` | [`ai-sdk.mjs`](javascript/ai-sdk.mjs) | text, a stream, an embedding | `node ai-sdk.mjs` |
| JavaScript | LangChain.js, `@langchain/google-genai` | [`langchain.mjs`](javascript/langchain.mjs) | chat, a stream, an embedding | `node langchain.mjs` |
| Go | Google's `google.golang.org/genai` | [`go/main.go`](go/main.go) | an answer, a stream, an embedding | `go run .` |
| C# | `HttpClient`, the REST API by hand | [`csharp/Program.cs`](csharp/Program.cs) | every credential form including `?key=`, a stream, an embedding | `dotnet run` |
| PHP | `google-gemini-php/client` | [`php/chat.php`](php/chat.php) | an answer, a stream, an embedding | `php chat.php` |
| Ruby | RubyLLM | [`ruby/chat.rb`](ruby/chat.rb) | an answer, a stream, an embedding | `bundle exec ruby chat.rb` |
| Rust | `gemini-rust` | [`rust/src/main.rs`](rust/src/main.rs) | an answer, a stream, an embedding | `cargo run` |

Installing first, once per language: `pip install -r requirements.txt` (in a virtual
environment) · `npm install` · `composer install` · `bundle install`. Maven, Go, .NET and Cargo
fetch what they need on the first run. The versions the examples were run with: Python 3.13 with
`google-genai` 2.34, `langchain-google-genai` 4.4 and LlamaIndex 0.14; Java 25 (17 or later
needed) with `google-genai` 2.6, LangChain4j 1.24 and Spring AI 2.1; Node 26 (20 or later) with
`@google/genai` 3.6, `ai` 7 with `@ai-sdk/google` 4, and `@langchain/google-genai` 2.3; Go 1.26
with `google.golang.org/genai` 1.79; .NET 10; PHP 8.5 with `google-gemini-php/client` 2.8;
Ruby 3.3 with RubyLLM 2.2; Rust 1.100 with `gemini-rust` 2.1.

**How they were checked.** Each example ran in every credential form it offers, against a
gateway with authentication on, its own Keycloak-like token issuer and mock models under these
names. That proves the address, the credential and the requests; what a mock answers is an echo,
so the answers and the similarity ranking printed there mean nothing. The Python examples on
Google's SDK and on plain HTTP run so in the gateway's own test suite, on every change; the others
ran so when they were written.

## What each client needs to be told

Measured against the gateway, client by client.

- **With or without `/v1beta` in the base URL.** Google's SDKs (Python, Java, JavaScript, Go),
  LangChain in Python and JavaScript, LlamaIndex and Spring AI take `<gateway>/uc/<use-case>` and
  add the version themselves. The Vercel AI SDK, LangChain4j and RubyLLM take
  `<gateway>/uc/<use-case>/v1beta`; the PHP client and `gemini-rust` take
  `<gateway>/uc/<use-case>/v1beta/`, with the final slash.
- **The API version.** Google's JavaScript and Go SDKs call `/v1` unless told otherwise. The
  gateway serves `/v1` as well as `/v1beta`; the examples set `v1beta`, as Google's Python and
  Java SDKs call it.
- **No header setting.** LangChain4j's embedding model, LangChain.js's embeddings and RubyLLM's
  `embed` cannot send a header. They name the use case in the path, and a token goes in their key
  setting — the examples do exactly that in the `bearer` and `service-account` forms.
- **Embedding options are declared per model.** Several texts in one call, a task type and an
  output dimension are each served only where the model's catalogue entry declares them, and
  refused by name otherwise (a list of one text is always served). LangChain in Python and
  LlamaIndex send a task type with every embedding, and LlamaIndex embeds documents as a list.
- **`gemini-rust`** marks `generate_content` deprecated in favour of Google's Interactions API,
  which AIRA does not serve; the example keeps `generate_content`. Its `text()` returns the first
  part only.
- **gRPC is not served**: a client that offers a transport uses REST. **Vertex mode is not
  served** either — `google-genai` with `vertexai=True`, Spring AI's and LangChain4j's Vertex
  modules — configure a client for the Gemini API, as here.
- **Spring Boot's auto-configuration** was not measured. The Spring AI example builds its models
  from Google's `Client`, which is where the base URL and the credential go.

## Not here, and why

- **LiteLLM**: its embeddings send no credential at all once `api_base` is set, and its speech
  call ignores `api_base` and sends the key it was given to Google. Its chat works, with
  `api_base="<gateway>/uc/<use-case>/v1beta"`.
- **Semantic Kernel** in Python: its Google connector runs Google's deprecated SDK over gRPC with
  no endpoint setting, and sent its key to Google.
- **The `gemini-ai` Ruby gem**: it has no base-URL setting.
- **LangChainGo** has no endpoint option of its own (an option function of your own sets it). It
  sends a safety threshold for every category on every call, which a Gemini model applies and a
  model of another vendor refuses; `googleai.WithHarmThreshold(googleai.HarmBlockUnspecified)`
  makes it send none.
- **Genkit's speech**: Genkit sends `gemini-2.5-flash-tts` to Google's Interactions API; its chat
  and embeddings work.
- **C# libraries**: Google's .NET SDK (`Google.GenAI`), `Mscc.GenerativeAI` and Semantic Kernel's
  .NET connector were not measured, so the C# example uses the REST API with nothing but .NET.
- **Ranking**: Google's ranking API is served too, on its own path;
  [the Gemini migration guide](../docs/MIGRATION-GEMINI.md#4-verified-clients) shows how each
  ranking client is pointed at the gateway.

## Reasoning comes back only when asked for

A thinking model's reasoning is returned only when the request asks for it —
`thinkingConfig.includeThoughts: true`, `include_thoughts=True` in Python — and only where the use
case returns reasoning; where its administrators have switched it off, asking is refused by name
(`400 FAILED_PRECONDITION`). It arrives as parts marked `thought: true`, ahead of the answer, which
is why the REST examples skip such parts when they read the answer.
[`google_genai_thinking.py`](python/google_genai_thinking.py) asks for it.

## When a call is refused

The gateway answers in Google's error envelope, `{"error": {"code", "message", "status", …}}`.

| Answer | What it means |
| --- | --- |
| `400` *Missing use case: …* | The base URL lacks `/uc/<use-case>`, and no `X-AIRA-Use-Case` header was sent. For a key, the message names its use case. |
| `401` *Missing or invalid credentials.* | No credential, a wrong or expired key, or a token the gateway does not accept: expired, fetched from another Keycloak address, or without the audience `aira-gateway`. |
| `403` *API key is bound to use case '…'.* | The key belongs to another use case. |
| `403` *Not a member of use case '…'.* | The person or service account is not in the use case — or its token carries no groups, which is what a client without the group-membership mapper sends. |
| `403` *Use case '…' is not known to this gateway* | The use case's configuration has not yet arrived from Management. |
| `400` *No model could serve this request (… has not been released to use case '…')* | The model is not released to the use case. |
| `404` *Model '…' not found.* | No model of that name in the catalogue. |

Every response carries an `X-AIRA-Request-Id` header, and every answer names the model that
served it in `X-AIRA-Model`. In the console, **the use case → Requests → Request id** shows
what happened to a request: which models were tried, what the use case's rules decided, and the
message its caller was given.
