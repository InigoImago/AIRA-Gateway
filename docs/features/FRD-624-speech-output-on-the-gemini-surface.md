# FRD-624 — Speech output on the Gemini surface

> Phase: 8 · Status: **Done** — the Gemini surface; KIRA not in scope (§5) · Owner: Vadim Scheibe
>
> Origin: the owner asked whether the text-to-speech models can be reached through AIRA. Measured,
> neither surface could: the Gemini surface refused `speechConfig` and `responseModalities` by name,
> citing `ADR-0013`, and the KIRA surface has no speech endpoint. The owner asked for the Gemini
> surface first.
>
> Related: [`ADR-0026`](../adr/ADR-0026-speech-is-model-access-and-its-audio-is-described.md)
> (why this is in scope, and why the audio is not kept),
> [`ADR-0013`](../adr/ADR-0013-auditable-model-access-not-agents.md),
> [`FRD-124`](FRD-124-no-silently-dropped-fields.md) (refused by name, never dropped),
> [`FRD-114`](FRD-114-model-capability-metadata.md) (capabilities),
> [`FRD-122`](FRD-122-complete-audit-trail.md) (the audit trail),
> [`FRD-135`](FRD-135-a-models-reasoning.md) (thoughts are asked for only while thinking is on).

## 1. Why

Google's speech models are ordinary `generateContent` calls: text in, and one `inlineData` part of
PCM audio out (`audio/L16;codec=pcm;rate=24000`), billed as output tokens. They pass `ADR-0013`'s
test — they make model access better governed rather than making the gateway think for the use
case — so the refusal was a scope line drawn in the wrong place (`ADR-0026`).

## 2. Requirements

- **FR-1 Carried.** `generationConfig.responseModalities: ["AUDIO"]` and `speechConfig`: one voice
  (`voiceConfig.prebuiltVoiceConfig.voiceName`), or several speakers
  (`multiSpeakerVoiceConfig.speakerVoiceConfigs[]`), and an optional `languageCode`. Google's
  camelCase and the snake_case the `google-genai` SDK sends inside `speechConfig` are both accepted.
- **FR-2 Refused by name**, where the provider would answer an unexplained `400` or a wrong answer:
  - a modality other than `AUDIO` alone (`IMAGE`, or `TEXT` with `AUDIO`);
  - `speechConfig` without `AUDIO`, and `AUDIO` without a voice;
  - `systemInstruction`, `includeThoughts: true` or a `responseSchema` together with `AUDIO`.
- **FR-3 Served only by a model that speaks.** A candidate must declare the new capability
  `speech` and be reached through a dialect that can express it (Gemini on Vertex and AI Studio,
  and the mock). Any other candidate is skipped with its reason. A model that declares `speech`
  and not `generate` is refused a text request by name.
- **FR-4 Returned as Google returns it:** one `inlineData` part with the media type the provider
  named, and the usage it reported.
- **FR-5 Streamed as Google streams it.** `streamGenerateContent` carries each audio chunk as it
  arrives.
- **FR-6 Priced and budgeted like any output.** Audio tokens are output tokens
  (`candidatesTokenCount`); the catalogue's output price applies.
- **FR-7 The audit trail describes the audio and does not keep it** (`ADR-0026`). The stored response
  carries the media type, the byte count, the duration where the format states a rate, and the
  SHA-256 of the decoded audio the caller received. For a stream, all chunks in order are hashed.
  The text that was spoken is the prompt, stored as every prompt is.
- **FR-8 The console declares it.** `speech` is offered in the catalogue editor, with what ticking it
  commits the platform to.

## 3. Measured (2026-09-14, Vertex, this installation's project)

- **Regions:**

  | Model | europe-west1 | europe-west4 | global | us-central1 |
  | --- | --- | --- | --- | --- |
  | `gemini-2.5-flash-tts` | 200 | 200 | 200 | 200 |
  | `gemini-2.5-pro-tts` | 200 | 200 | 200 | 200 |
  | `gemini-2.5-flash-lite-preview-tts` | 200 | 200 | 200 | 200 |
  | `gemini-2.5-flash-preview-tts` | 404 | 404 | 500 | 200 |
  | `gemini-2.5-pro-preview-tts` | 404 | 404 | 500 | 200 |

- **Accepted:** one voice, two speakers, `languageCode`, `temperature`, and a thinking budget of 0.
- **Refused by the provider with an unexplained `400`:** a text-only request, `AUDIO` without a
  voice, and `systemInstruction`.
- **Refused by the provider with a reason:** `TEXT` with `AUDIO` ("audio out only"), and
  `includeThoughts` ("only enabled when thinking is enabled").
- **Truncated without saying so:** `maxOutputTokens: 10` returned 4 audio tokens with
  `finishReason: STOP`. The cap still bounds the spend.
- **Streaming:** a paragraph arrived as 286 chunks of about 2.5 KB of audio each. The last chunk
  carries an empty text part, `STOP` and the usage.

## 4. Design

- **Canonical.** `CanonicalRequest.speech` (voice or speakers, language). `CanonicalResponse.audio`
  and `CanonicalChunk.audio_delta` are `DataPart`s: decoded bytes and their media type, like an
  attachment.
- **Requirement.** `SpeechSupported`, per hop, checks the capability and the dialect. The dialect
  declares `speaks`; an adapter that does not declare it cannot.
- **Mapping.** Only the Gemini dialect writes `responseModalities` and `speechConfig`, and it reads
  audio parts back. It never asks for thoughts for a speech request, because no setting switches
  thinking on for it (`FRD-135`).
- **Audit.** The writer already replaces every inline datum in a stored payload with its
  description, responses included (`FRD-110` §5.4, `attachments.strip_attachments`). It now adds
  the duration for audio. A stream arrives in pieces, so its description is built as they pass, in
  the same shape (`aira_gateway.audio`). The caller receives the audio unchanged.
- **Mock.** The mock speaks deterministic PCM whose length follows the text, so the whole path is
  tested without a cloud.

## 5. Non-goals

- The KIRA surface: its contract has no field for audio, and inventing one is not compatibility.
- OpenAI's `/audio/speech` endpoint and audio **input**.
- Keeping the audio (`ADR-0026`).
- A voice list: the provider validates the voice name, and a list kept here would go stale.

## 6. Testing

- **Hermetic:**
  - the schema (carried, refused by name, both spellings);
  - both mappers in both directions, and streaming;
  - the requirement per hop;
  - the stored description and its hash, for a single answer and for a stream;
  - the mock.
- **Live** (`tests/integration/test_speech_live.py`): against Vertex in europe-west1, through the
  gateway.
  - Buffered: 4.89 s of speech in 3.93 s.
  - Streamed: 144 audio chunks, the first after 0.63 s and all after 2.79 s.
  - The audit rows name `europe-west1`, carry the output tokens, and keep the description and the
    hash, which matches the audio the caller received. The stored responses were 426 and 200
    characters, where the audio was about 310 KB as base64.
- **Mutations** for each refusal, the requirement, the description and the hash.
