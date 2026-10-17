# ADR-0037 — Google is reached without a Google key, through the installation's own identity

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Vadim Scheibe

## Context
Claude on Vertex accepts only a service account, and the only way the gateway had to be one was a
service-account **key**: a private key valid until somebody deletes it, passed as one environment
value, rotated by hand with a restart. An organisation created on Google Cloud since May 2024 may not
create such a key — `iam.managed.disableServiceAccountKeyCreation` is enforced by default — and the
owner, setting up a standalone installation against an external Keycloak, found the key both the
most secure option on offer and the least workable one.

Google's own answer is workload identity federation: an identity the workload already has is
exchanged at Google's Security Token Service for a short-lived token that acts as the service
account. AIRA already runs against a Keycloak, which can issue such an identity.

## Options considered
- **Keep the key, make it bearable** — a key file re-read on change, a rotation runbook. Built as
  well (`FRD-141` FR-5), because some installations will keep a key. Does not help where keys are
  forbidden, and a key remains a credential that outlives every mistake.
- **Google's client library (`google-auth`)** — handles every credential type Google has. A second
  token-caching layer beside `aira_common.tokens`, a synchronous transport on an async path, and an
  executable credential source that runs commands named in a file; and it would still need code for
  the one source that matters here, a client-credentials grant at Keycloak.
- **Federation, in the gateway's own token layer** — two HTTP exchanges (STS, then impersonation)
  behind the existing `TokenAcquirer` hook; Google's `external_account` JSON as the configuration, so
  `gcloud` writes it; the subject token from Keycloak by client credentials, or from a file or a local
  URL as Google's format allows. Plus the metadata server for installations on Google Cloud.

## Decision
Federation in the gateway's own token layer (`FRD-142`). The credential's own `type` chooses between a
key and a federation configuration, as it does for Google's libraries. The identity provider is a
setting of three values, `AIRA_VERTEX_FEDERATION_*`; the configuration names no secret. A
configuration that would send a token anywhere but `https://*.googleapis.com`, or run an executable,
is refused at start. Federation is the documented recommendation; a key is the fallback.

## Consequences
- Positive: no Google secret exists anywhere; what rotates is one client secret in the installation's
  own Keycloak; revoking access is disabling a client or removing one IAM binding; it works where
  keys are forbidden. The same mechanism takes a Kubernetes projected token from a file, which a
  Helm deployment will want.
- Negative / trade-offs: two more round trips per hourly refresh (Keycloak and STS). A Keycloak that
  Google cannot reach needs its realm keys uploaded to the provider, and uploaded again whenever the
  realm rotates them — a step an operator can forget, which is why that refusal is named
  (`SIGNATURE_NOT_VERIFIED`). The gateway now holds a Keycloak client secret.
- Follow-ups: Helm should use the file source with a projected token. Foundry could federate the
  same way with Entra; not built.
