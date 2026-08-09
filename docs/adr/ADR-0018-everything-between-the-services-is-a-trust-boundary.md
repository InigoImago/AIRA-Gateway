# ADR-0018 — Everything between the services is a trust boundary

- **Status:** Accepted
- **Date:** 2026-08-09
- **Deciders:** Vadim Scheibe
- **Extends:** [ADR-0007](ADR-0007-security-hardening-baseline.md) and
  [ADR-0015](ADR-0015-a-convenience-default-is-a-production-default.md), which hardened what
  reaches AIRA **from a caller**. This is about what reaches it from its own infrastructure.

## Context

A security read of the whole codebase (2026-08-09) found the request path in good order: keys are
192-bit and compared in constant time, JWTs pin their algorithm and require `exp`/`iat`/`sub`, every
route is authenticated, payload access is gated and recorded, bodies and schemas are bounded, and
no `eval`, raw SQL or disabled TLS verification exists anywhere.

Three of the four real findings were in the **space between the services**, and they share a shape:
each is a link AIRA trusts completely and could not be told to verify.

- **The event bus.** `apply_event` writes what arrives on the config topics straight into the
  read-model the gateway's authorization is derived from. That is the right design — `FRD-204`'s
  consumer is deliberately simple — but the producer and consumer connected with
  `bootstrap_servers` and nothing else, and there was **no setting** for a protocol, a mechanism or
  a credential. Anyone who could reach the broker could publish `api_key.created` with a hash of
  their choosing, or `use_case_group.granted` naming a group they belong to, and hold
  administrator access to any use case. No credential presented, and **no audit row**, because from
  the gateway's side nothing unusual happened: configuration arrived, as configuration does.
- **The identity provider.** Nothing required the issuer or JWKS URL to be `https`. Over plaintext,
  anyone on the path substitutes a key set and mints tokens that verify — every role, every use
  case, every audit identity. The same for Vault, whose address carries the AppRole login.
- **The reverse proxy.** `X-Forwarded-For` was read from the **left**, on a docstring that assumed
  a proxy which *overwrites*. The nginx this repository ships **appends**, as every default
  configuration does, so the left end was the caller's to write. It reaches the audit trail, the
  incident view's address filter, and — because the failed-authentication bound keys on it — the
  bucket that bounds brute force, which rotating the header therefore defeated.

Each was safe in the deployment we run and unsafe in the one an enterprise would.

## Decision

**A link between AIRA and anything else is configured, authenticated and refusable — and the
refusal is a startup failure, not a warning.**

Concretely, added to `unsafe_settings` on both planes (so they are reasons in one list, `ADR-0015`'s
shape, not four consecutive deploy attempts):

| Link | Refused outside `local` |
|---|---|
| Kafka | `AIRA_KAFKA_SECURITY_PROTOCOL=PLAINTEXT` while a broker is configured |
| Identity provider | `http://` issuer or JWKS URI |
| Vault | `http://` address |

And a parsing rule rather than a setting: the forwarded address is read `AIRA_TRUSTED_PROXY_HOPS`
entries **from the right**, so the part a caller controls is never the part that is believed.

**Loopback is exempt from the plaintext rules.** A sidecar terminating TLS on `127.0.0.1` is a
normal deployment and its traffic never reaches a network. Refusing it would push operators to
`AIRA_ENVIRONMENT=local`, which switches *every* check off — a rule that is worked around is worse
than a narrower rule that is kept.

**Defaults do not change.** `PLAINTEXT`, one proxy hop, and the Compose stack's plain HTTP all keep
working, because `ADR-0015` already settled that a hardening pass which breaks the demo gets
reverted. What changes is that the unsafe configuration is now impossible to reach *by accident*
outside development.

## Consequences

- An existing non-local deployment will refuse to start until it names a broker identity. That is
  intended and it is a migration step, documented in `INTEGRATIONS.md` with the ACLs that make the
  identity worth having — an authenticated principal allowed to publish anywhere is the same hole
  with a login.
- The trust question is now asked in **one** place per link, and each is guarded by a mutation
  (`W1`–`W4`), so the answer cannot quietly revert.

## What this does not claim

Only what was reachable by reading was reviewed. Three things are named rather than fixed:

- **`version-info` discloses build identity unauthenticated**, on both surfaces. `routes/health.py`
  argues the case (a commit hash is what somebody correlating a bug report needs) and making it
  authenticated would be a fourth documented deviation from the predecessor. It is now **listed**
  in `test_every_route_is_guarded.py` rather than merely absent from anybody's attention — a
  decision to review, not an oversight. `_may_see_detail` is the pattern to reuse if the answer
  changes.
- **`trust_forwarded_for` with no proxy actually in front** trusts a header nobody rewrote. No
  parsing rule can rescue that; it is what enabling the setting means.
- **Content redaction still masks credentials only** (`FRD-406`), deliberately.
