# FRD-406 — Redaction of credentials in stored payloads

> Phase: 4 · Status: **Done — credential half only (2026-08-08)** · Owner: Vadim Scheibe · Last
> updated: 2026-08-08
> Related: `FRD-103` (the hook), `FRD-404` (retention and the storage switch), `ADR-0007`,
> `ADR-0015`, `ADR-0016` (why the PII half was declined rather than deferred)

## 1. Problem

The `Redactor` hook has existed since Phase 1 and was a no-op. A stored prompt is therefore a
verbatim copy of whatever a caller sent, kept for as long as the use case's retention says and
readable by anyone who can read the table.

Callers paste credentials into prompts. "Here is our API key, write me a curl command" is not an
exotic input. Storage (`FRD-404`) decides *whether* a payload is kept and retention decides *how
long*; neither can help with a value that should never have been written down at all.

## 2. Requirements

* **FR-1** Credential-shaped strings are replaced in every stored request and response payload.
* **FR-2** Business content is **not** touched. Names, customer numbers, addresses and prose are
  the reason the payload is stored.
* **FR-3** The payload keeps its structure, so it stays readable next to the response it produced.
* **FR-4** A deployment may add its own patterns; they are **additive**.
* **FR-5** An unusable pattern stops the gateway rather than silently matching nothing.
* **FR-6** Redaction runs after attachment stripping and cannot be switched off by swapping the
  redactor (unchanged from `FRD-110` §5.4).

## 3. Design

`PatternRedactor` walks the JSON and rewrites **strings**; keys are structure and are left alone,
because rewriting one changes the shape and breaks every reader that indexes into it. Matches
become a fixed `[REDACTED]`, which a reader can tell apart from something the caller wrote and
which cannot be mistaken for a short credential worth trying.

The built-in set is deliberately small, and each entry is something that is never legitimate
business content:

| Pattern | Why |
|---|---|
| `aira_<prefix>_<secret>` | ours, and it grants use-case access |
| `AIza…` | the Google key every Gemini client holds |
| `sk-…` | OpenAI-style secret keys, including project-scoped |
| `Authorization: …` | a header a caller has pasted in |
| a JWT | three base64url segments; nothing else looks like that |
| a PEM private key block | body and all, or the fix removes only the label |

`AIRA_REDACT_PATTERNS` (`;`- or newline-separated) adds deployment-specific formats. Additive and
never replacing: a deployment naming its own token format must not thereby stop redacting Google
keys, which is exactly what a replacing setting would do the first time anybody used it.

Patterns are compiled at construction. An invalid regex raises `RedactionMisconfigured` and the
gateway does not start — a rule that silently compiles to nothing is an absent control wearing a
present one's badge, which is the failure `FRD-125` fixed in the injection filter. A **nested
quantifier** is refused for the ReDoS reason `ADR-0007` refuses one in a pipeline config: these run
over caller-supplied text on the write path.

## 4. What is deliberately not done

* **No PII detection.** Redacting personal data would remove the content the payload exists for,
  and the honest control for "this data must not be persisted" is `FRD-404`'s per-use-case switch,
  which is already there and already off-able.
* **No redaction of the response by a different rule.** Same patterns both ways: a model that
  echoes a key back has produced the same problem.

## 5. Verification

`gateway/tests/test_redaction.py` (15 cases) proves the class; `test_store_payloads.py` proves the
**wiring** by posting a prompt containing a Google key and reading the row back — the lesson
`FRD-124` and the CSV export both recorded, that a requirement exercised only against the class
leaves the route undefended and coverage cannot see the difference. Mutations `H15`–`H17`.
