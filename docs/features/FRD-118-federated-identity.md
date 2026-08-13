# FRD-118 — Several Keycloak backends, and groups from UserInfo

> Phase: 8 (KIRA parity) · Status: **Draft — requirement not yet confirmed** · Owner: Vadim Scheibe
>
> Origin: the predecessor's contract, programme: `ADR-0010`. Touches `FRD-101`, `FRD-102`, `FRD-200`.

> **Read §11 before starting this one.** Two of its three requirements may exist in the predecessor
> for reasons that do not apply to us, and one of them would make every authenticated request
> depend on Keycloak being up. This FRD is written so the requirement can be confirmed or dropped
> on evidence rather than assumed because the predecessor has it.

## 1. Problem

The predecessor's authentication differs from ours in three ways:

1. **Several Keycloak backends at once.** A list of issuers; the right one is chosen from the JWT
   header's `kid`, with a JWKS refresh on a cache miss.
2. **`aud` may be a list.** A token is accepted if it carries any of the configured audiences.
3. **Groups come from the UserInfo endpoint, not the token.** After validating the JWT, it calls
   `GET /userinfo` with the bearer token and takes `groups` from the response, checking that the
   returned `sub` matches the token's.

AIRA has exactly one issuer, one optional audience, and reads groups from the token claim
(`FRD-102`). For a deployment with one realm and groups in the token — which is what our realm
does — items 1 and 3 have no effect on what a caller can do.

## 2. Goals & Non-Goals

**Goals** (each conditional on §11)
- Validate tokens from more than one issuer, selected by `kid`.
- Accept a configured set of audiences.
- Optionally resolve group membership from UserInfo where the token does not carry it.

**Non-Goals**
- Non-Keycloak identity providers.
- Replacing `FRD-102`'s group→use-case mapping. Only the *source* of the group list is in question.
- Making UserInfo the default. If it is built at all it is opt-in per issuer (§5.3).

## 3. User Stories
- As an **operator** migrating between realms, I want both accepted during the overlap, so that the
  cut-over is not a flag day for every client.
- As an **operator** whose realm does not put groups in tokens, I want membership resolved anyway,
  so that the realm's configuration is not something AIRA dictates.

## 4. Functional Requirements

- **FR-1 Several issuers.** Each with its own JWKS and audiences. The verifier picks by `kid`, and
  refreshes a backend's JWKS at most once per interval on a miss — a `kid` that matches nothing is
  a rejection, not a fetch, or an attacker chooses when we call Keycloak.
- **FR-2 Audience sets.** A string or a list, normalised to a set; a token matching any configured
  audience passes. Absent configuration keeps today's behaviour of not checking.
- **FR-3 UserInfo groups, opt-in per issuer.** Where enabled, groups come from UserInfo, the
  returned `sub` **must** equal the token's, and the result is **cached** per subject with a short
  TTL (§5.3).
- **FR-4 UserInfo failure means no groups, not no authentication.** The predecessor's behaviour and
  the right one: authentication succeeded, so the caller is who they say; they simply have no
  memberships and will be refused by authorization with a message that says so.
- **FR-5 One implementation.** Gateway and Management share `aira_common.oidc`, as they do today.

## 5. Design & Architecture

### 5.1 The verifier becomes a small router

`JwtVerifier` today holds one issuer, one audience and one JWKS client. It gains a sibling that
holds several and dispatches on `kid`, delegating to a per-issuer verifier. Single-issuer
deployments configure one and behave identically — the existing configuration keeps working
unchanged, which is a requirement and not a courtesy.

The `kid` selection must not be a way to make us fetch: an unknown `kid` is rejected after at most
one refresh per issuer per interval (FR-1).

### 5.2 Where the audience check lives

Already in `JwtVerifier`; it becomes a set membership test. `verify_aud` stays off when no audience
is configured, which is today's documented behaviour.

### 5.3 UserInfo is a per-request call on the hot path, and that is the problem with it

The predecessor calls `/userinfo` after validating every token. That means **every authenticated
request depends on Keycloak being reachable and fast**. Keycloak becoming slow turns into the
gateway becoming slow; Keycloak going down turns into every caller losing their group memberships
and, by FR-4, being refused authorization. The failure is quiet — callers see 403, not 503 — which
is the worst shape for an incident.

If we build this, it must be cached: per `sub`, with a short TTL and a single-flight so that a
thundering herd cannot form, and stale-on-error so that a brief Keycloak outage does not become an
authorization outage. That is a meaningful amount of machinery for what is, in our realm, a claim
already present in the token.

Hence FR-3's opt-in: a deployment whose realm puts groups in tokens should never take this path.

### 5.4 What does not change

`FRD-102`'s mapping from `/use-cases/<slug>` group paths to `Principal.use_cases`, and
`FRD-201`'s realm-role→Django-group synchronisation, are unaffected. Only where the list comes
from is in question.

## 6. Data Model
None. The UserInfo cache is in-process (or Redis, if it is ever shared — not for a short TTL).

## 7. API / Interface Contract

Configuration only — a list replacing the single-issuer fields, with the existing variables kept as
the one-element case:

```yaml
oidc:
  backends:
    - issuer: https://keycloak/realms/aira
      audience: [aira-spa, aira-gateway]
      groups_from_userinfo: false
```

## 8. Security & Privacy

- **`kid` is attacker-controlled input.** FR-1's rate-limited refresh is what keeps it from being
  an amplification vector against our own JWKS fetches.
- **The `sub` cross-check in FR-3 is mandatory**, not advisory: without it a UserInfo response for
  a different subject would confer that subject's groups.
- More issuers means a larger trusted set. Each must be configured deliberately; there is no
  discovery and no wildcard.
- Tokens are not logged, as today.

## 9. Observability

The issuer that validated a token is recorded on the span and the audit row — otherwise a
multi-issuer deployment cannot answer "which realm was this caller from", which is the question a
migration exists to track. UserInfo cache hit/miss counted, so its cost is visible before it is
felt.

## 10. Testing & Acceptance Criteria

- **Unit** — a token from each configured issuer validates; an unknown `kid` is rejected without a
  second fetch; each audience in a set is accepted and a fourth is rejected; single-issuer
  configuration behaves exactly as today.
- **Unit (UserInfo)** — groups come from the response; a mismatched `sub` is **rejected** (written
  to fail first against an implementation that skips the check); a failure yields no groups and
  still authenticates; the cache serves a second call without a second fetch; concurrent misses
  produce one fetch; a failure after a hit serves stale rather than dropping groups.
- **Integration** — two realms in the dev Keycloak, a token from each, both accepted.
- **Mutation** — the `sub` cross-check actually compares; the audience set actually restricts.

**Acceptance**
- *Given* two configured issuers, *when* a caller presents a token from either, *then* it is
  accepted and the audit row names the issuer.
- *Given* an issuer with `groups_from_userinfo`, *when* Keycloak's UserInfo is unavailable, *then*
  the caller authenticates, has no memberships, and receives a 403 whose message says membership
  could not be determined — not a generic denial.

## 11. Dependencies & Risks — **confirm before building**

Three questions, in the order they should be asked:

1. **Is there more than one Keycloak realm or instance in the target environment?** If not, FR-1
   and FR-2 are speculative generality. The predecessor's configuration supports a list; that does
   not prove one is used.
2. **Does the target realm put group membership in the access token?** Ours does, which is why
   `FRD-102` works. If the target realm does too, FR-3 should not be built at all — it would add a
   per-request dependency on the IdP (§5.3) to obtain a claim already in hand.
3. **If FR-3 is needed, is a short-TTL cache acceptable?** It means a membership revocation takes
   up to the TTL to bite. That is a security trade and should be an explicit choice, not a
   consequence of a performance fix.

My reading: **FR-2 is cheap and worth doing regardless** (an audience list costs nothing and
removes a real constraint). **FR-1 is worth doing if a realm migration is foreseeable.** **FR-3
should not be built unless question 2 answers "no"** — it is the one requirement in this programme
where copying the predecessor makes the system measurably worse.

## 12. Rollout / Demo

If built: a second dev realm in Compose and a documented two-realm login. If FR-3 is built, the
demo must include a Keycloak-down scenario, because that is the behaviour people will need to
recognise.
