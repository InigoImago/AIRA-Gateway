# FRD-134 — Clock skew in token validation

> Phase: 1 (platform) · Status: **Draft** · Owner: Vadim Scheibe
> Related: `FRD-101` (authentication), `FRD-107` §5.4 (the compatibility surface's auth),
> `ADR-0015` (credentials), `docs/CONFIGURATION.md`

## 1. Summary

`JwtVerifier` passes no `leeway` to `jwt.decode`, so PyJWT uses `0`: every time claim is compared
against this process's clock with no tolerance at all. Nobody chose that number — it is a default
that arrived with the library, is not configurable, is not documented, and has no test.

Read as a security posture it is the strict end and perfectly defensible. Read as an availability
property it is not, because of one interaction we created deliberately elsewhere: **we require
`iat`**, and PyJWT rejects a token whose `iat` lies in the future.

```python
# jwt/api_jwt.py, PyJWT 2.13
if iat > (now + leeway):
    raise ImmatureSignatureError("The token is not yet valid (iat)")
```

So if the gateway's clock is **one second behind** the issuer's, every freshly minted token is
refused — not the ones at the edge of expiry, all of them, at the first call, as
`401 INVALID_TOKEN`. That answer is indistinguishable from a wrong client secret, which is how an
outage of this shape costs a day rather than a minute.

This FRD makes the tolerance a decision, and splits it in two, because the two halves cost
different things.

## 2. Goals & Non-Goals

**Goals**
- A clock that is **behind** the issuer's no longer refuses valid tokens.
- The tolerance is **configured, documented and tested**, on both planes, rather than inherited.
- The concession that has a security cost — accepting a token *after* `exp` — is separate from the
  one that has none, and is off by default.

**Non-Goals**
- Fixing clocks. NTP is the actual answer to skew; this is what keeps a service answering while
  somebody applies it.
- Token introspection or revocation checks (`ADR-0015` stands: a token is valid until it expires).
- Any change to which claims are required. `exp`, `iat` and `sub` stay mandatory (`libs/oidc.py`).

## 3. User Stories

- As an **operator** deploying against a corporate Keycloak on separate infrastructure, I want a
  few seconds of clock difference not to reject every token, so that day one is not spent
  suspecting the client secret.
- As an **application owner migrating from the predecessor**, I want the tolerance my clients have
  relied on to be available, so that a migration does not fail for a reason no log names.
- As **IT Security**, I want the amount of extra life granted to an expired token to be a written
  number I can review, not a library default nobody looked at.

## 4. Functional Requirements

- **FR-1 Two settings, not one.**
  - `AIRA_OIDC_CLOCK_SKEW_SECONDS` — how far the issuer's clock may run **ahead** of ours;
    applies to `iat` and `nbf`. **Default `60`.**
  - `AIRA_OIDC_EXPIRY_LEEWAY_SECONDS` — how long after `exp` a token is still accepted.
    **Default `0`.**
- **FR-2 Both planes.** The setting lives in `aira_common.oidc.JwtVerifier`, which the gateway and
  the management backend both build. A tolerance that holds on one plane and not the other is the
  shape `FRD-126` exists to prevent: the console would sign in and the gateway refuse the same
  token, or the reverse.
- **FR-3 Bounded.** Both values are non-negative and refused above **300 seconds**. A tolerance
  larger than Keycloak's own access-token lifespan (300 s in this realm) means a token could be
  accepted for longer than it was ever valid, which is not skew tolerance, it is a second lifetime.
- **FR-4 Named in the refusal.** When a token is rejected for a time claim, the log entry says
  which claim and by how much, e.g. `iat 4s ahead of this host, tolerance 60s`. The *caller* still
  gets `401 INVALID_TOKEN` and nothing more — a refusal that reports our clock to an unauthenticated
  caller is a disclosure, and this is for whoever reads the logs.
- **FR-5 Documented in `docs/CONFIGURATION.md`**, with the trade-off stated: the first setting
  costs nothing, the second extends a credential's life.

## 5. Design & Architecture

### 5.1 Why one number is the wrong shape

PyJWT applies one `leeway` to `iat`, `nbf` and `exp`. Those are not one question:

| Claim | What a violation means | What tolerating it costs |
| --- | --- | --- |
| `iat` / `nbf` in the future | **our** clock is behind the issuer's | **nothing.** The token was genuinely minted; accepting it extends nobody's access |
| `exp` in the past | the credential's life is extended past what the issuer granted | real though small: 60 s on a 300 s token is **+20 %** of its window |

Collapsing them means an installation that only wants the first — and the first is the one that
takes a service down — has to buy the second. Hence two settings, with the free one on by default
and the costly one off.

### 5.2 Implementation

`jwt.decode` is given `leeway=clock_skew`, which covers `iat` and `nbf`. `exp` is then re-checked
against `expiry_leeway`:

```python
claims = jwt.decode(..., leeway=self._clock_skew, options={...})
if float(claims["exp"]) + self._expiry_leeway < time.time() - self._clock_skew:
    return None   # accepted by decode's leeway, refused by ours
```

The re-check is subtractive only: it can refuse what `decode` accepted, never accept what `decode`
refused, so no verification is reimplemented and `exp` stays required. With both settings at their
defaults the behaviour is *exactly* today's for `exp` and 60 seconds more forgiving for `iat`.

### 5.3 Where the number 60 comes from

It is what the predecessor grants (its JWT validation allows 60 seconds of drift), it is what most
OIDC libraries default to, and it is well under the 300-second token lifespan this realm issues.
Matching it is also the compatibility argument: `FRD-107`'s promise is that a consumer changes a
base URL, and a client that worked yesterday failing today for a reason no message names is the
precise opposite of that promise.

The predecessor's 60 seconds applies to `exp` as well as `iat`. **We deliberately differ**, and it
goes in `FRD-107` §5.5 with the rest: a client using a token after it expired is a client with a
broken refresh strategy, and an installation that wants to absorb that can set the second value.

## 6. Data Model

None. No migration, no stored state.

## 7. API / Interface Contract

Unchanged. The refusal stays `401` — `INVALID_TOKEN` on the KIRA surface when a credential was
presented and rejected, `UNAUTHENTICATED` in Gemini's envelope, `NOT_AUTHENTICATED` when nothing
was presented at all (`api/kira/errors.py`). Only *which* tokens reach it changes.

## 8. Security & Privacy

- The default configuration grants **no** additional life to any credential: `iat`/`nbf` tolerance
  cannot extend access, and `exp` tolerance is `0`.
- FR-3's ceiling exists so the second setting cannot be turned into a lifetime extension by a typo.
- FR-4 keeps the clock difference in the logs and out of the response body.
- Not weakened: required claims, signature algorithm (`RS256`), issuer, audience. In particular
  `AIRA_OIDC_AUDIENCE` stays enforced by `security.py` outside `local`.

## 9. Observability

- One log field on every time-claim refusal: the claim, the difference in seconds, the tolerance.
  That is what turns "the client secret must be wrong" into "the hosts differ by 4 seconds".
- A skew refusal is a failed authentication like any other and is already counted by `ADR-0015`'s
  bound, so a badly-clocked client cannot use this path to hammer the endpoint.

## 10. Testing & Acceptance Criteria

Unit (`libs/tests/test_oidc.py`, both planes' auth tests):
- a token with `iat` 5 s in the future **verifies** at the default and **is refused** at
  `AIRA_OIDC_CLOCK_SKEW_SECONDS=0`;
- a token 5 s past `exp` **is refused** at the default and **verifies** at
  `AIRA_OIDC_EXPIRY_LEEWAY_SECONDS=60`;
- a token 5 s past `exp` is refused even at `AIRA_OIDC_CLOCK_SKEW_SECONDS=60` — proving the two
  settings are genuinely separate and that the re-check in §5.2 is load-bearing;
- values above 300 or below 0 are refused at startup, naming the setting.

Mutation (`tools/mutation_check.py`): removing the `exp` re-check must turn a test red — that line
is the entire difference between "tolerant of a slow clock" and "tolerant of expired credentials",
and it is exactly the kind of line a later refactor deletes as redundant because `decode` looks
like it already checked.

**Acceptance**
- *Given* a gateway whose clock is 5 seconds behind Keycloak's, *when* a client presents a token
  minted a moment ago, *then* it is served — where today every such request is `401`.
- *Given* the same gateway, *when* a client presents a token that expired 5 seconds ago, *then* it
  is refused, and the log names `exp` and the difference.
- *Given* `AIRA_OIDC_CLOCK_SKEW_SECONDS=400`, *when* either plane starts, *then* it refuses to
  start and names the setting and the ceiling.
