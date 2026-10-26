# ADR-0045 — A model call names its use case, whatever the credential

- **Status:** Accepted
- **Date:** 2026-10-25
- **Deciders:** Vadim Scheibe (owner), with the gateway

## Context

Every control is scoped to a use case, and a request got its use case three ways:

1. a **selector** — the `X-AIRA-Use-Case` header or the `/uc/<slug>` path prefix (`FRD-102`);
2. a **bound API key's own** use case, where the request named none (`FRD-205`);
3. on the KIRA surface, the caller's **only membership**, where they had exactly one (`FRD-107`
   §5.3).

The second and third make a request's meaning depend on the state of its credential rather than on
the request. Under the third, a client that works today starts answering `403` the day its user is
given a second use case — or, had a guess been made instead, bills the wrong one. Under the second,
a person holding keys for two use cases bills whichever key a tool happens to be configured with,
and nothing in the request, the client's configuration or a proxy log says which.

The owner asked that a user always names their use case, and — asked whether that covers API keys
as well — decided that it does.

## Options considered

- **Keep all three.** Nothing for a client to change. The ambiguity above stays.
- **Sign-ins only.** The KIRA shortcut goes; a key keeps supplying its own use case. Breaks nothing
  that runs today, and leaves two rules: one for keys, one for everybody else.
- **Every model call, every credential** — chosen.
- **A setting choosing between the last two.** A rule an installation can switch is two rules to
  document, test and explain, and an installation that has not chosen runs the one it did not
  pick.

## Decision

**A model call names its use case, on both surfaces, whatever the credential.** A bound key's
binding *checks* the name — naming another use case stays `403` — and no longer supplies it.

Bounded on purpose:

- **Readings are not model calls.** A `GET` spends nothing — the catalogue, `/v1beta/me/budget`,
  the KIRA model list — and is answered without a use case; a bound key's reading is its own use
  case's, so a client that reads its allowance before it names anything still gets it.
- **The two exemptions of `ADR-0015` stay**: the unbound break-glass key and demo mode, which have
  no use case to name or no identity to name it for.
- **`AIRA_REQUIRE_USE_CASE=false`**, refused anywhere but `local`, still lets a call go
  unattributed — and a bound key's call there goes to its own use case, never to no budget.
- **The refusal is the fix.** It names the prefix and the header and — where the credential knows
  — which use case: a key's own, or the use cases a person reaches. Gemini's `400 INVALID_ARGUMENT`
  and KIRA's `403 STANDARD_USER_PERMISSION_REQUIRED`, one sentence for both.

## Consequences

- **Positive.** What a request is billed to is in the request — in the client's base URL, on every
  hop that logs one — and means the same thing whatever its caller reaches. One rule for every
  credential, and one sentence in every document.
- **Negative.** **A client configured with a key and a base URL without `/uc/<slug>` stops working
  at the upgrade**: its model calls are answered `400` (Gemini) or `403` (KIRA) until its base URL
  carries the prefix or it sends the header. The release notes say so under *what you have to do*.
- **Everything this product hands out names the use case**: the console's OpenCode configuration
  and connection examples, the showcase's configuration, its demo traffic and its printed
  examples, and the load test.
