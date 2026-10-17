# FRD-142 — Reaching Google without a Google key

> Phase: 8 (KIRA parity) · Status: **Built** · Owner: Vadim Scheibe
> Origin: the owner, after a standalone installation against an external Keycloak: a service
> account is the safest way to reach Vertex and, as a key that must be rotated, the most awkward
> one. Organisations created on Google Cloud since May 2024 may not create such keys at all.
> Decision: `ADR-0037`. Related: `FRD-115` (Vertex), `FRD-116` (Vault), `FRD-141` (why a provider
> refused; the key as a file).

## 1. Summary

The gateway can present itself to Google as a service account without holding that account's key:
by **workload identity federation**, exchanging a token from the installation's own OIDC provider —
its Keycloak — at Google's Security Token Service, or, on Google Cloud, by asking the **metadata
server**. The configuration is Google's own `external_account` JSON; the only secret left is the
gateway's client secret in Keycloak.

**No link between Keycloak and Google Workspace is needed or created.** Federation is a trust in
the GCP project for one Keycloak *client*; no user, directory or single sign-on crosses between the
two. What it does need is somebody with IAM rights in that project, and — where an organisation
policy restricts identity providers — the realm's issuer on its allowed list (`docs/INTEGRATIONS.md`,
Vertex D).

## 2. Goals & Non-Goals

**Goals**
- No Google key: works where `iam.managed.disableServiceAccountKeyCreation` is enforced.
- Keycloak as the identity, by a client-credentials grant; a token file or a local URL as Google's
  format also allows.
- The machine's own account on Google Cloud.
- Every misconfiguration that would fail at the first exchange, or send a credential elsewhere,
  stops the start; every refusal at an exchange names its cause (`FRD-141`).

**Non-Goals**
- AWS and executable credential sources. An executable source runs a command named in a file.
- Federation for Foundry with Entra; possible the same way, not built.
- Uploading Keycloak's keys to Google automatically.

## 4. Functional Requirements

- **FR-1 — Federation through an OIDC provider.** With `AIRA_VERTEX_FEDERATION_TOKEN_URL`,
  `_CLIENT_ID` and `_CLIENT_SECRET` set and `AIRA_VERTEX_CREDENTIALS(_FILE)` holding an
  `external_account` configuration, each refresh: (1) a client-credentials grant at the provider,
  the client authenticated by HTTP Basic; (2) an RFC 8693 token exchange at the configuration's
  `token_url` — `audience`, `subject_token_type`, the subject token, scope `cloud-platform`;
  (3) where `service_account_impersonation_url` is set, `generateAccessToken` with the federated token
  and the configured lifetime (default 3600 s), whose token is used; otherwise the federated token
  itself.
- **FR-2 — The configuration's own sources.** Without the three settings, the configuration's
  `credential_source` supplies the subject token: a `file` (read at every exchange; text, or a JSON
  field) or a `url` answered on a GET with its `headers`. A file comes first, as AIP-4117 orders it.
- **FR-3 — The metadata server.** `AIRA_VERTEX_METADATA_SERVER=true` takes the machine's token from
  `metadata.google.internal` with `Metadata-Flavor: Google`. Exclusive with the credentials and with
  federation.
- **FR-4 — Refused at start.** An audience that is not a provider's full name; a `token_url` or an
  impersonation URL that is not `https://*.googleapis.com`; an AWS or executable source; a
  configuration with no source and no federation settings; federation settings without a client,
  or with a key instead of a configuration; a credential of another `type`; a lifetime outside
  600–43200 s.
- **FR-5 — Refusals name their cause.** The token exchange's audience mismatch, attribute condition,
  unreachable issuer, signature, expiry and organisation policy; the impersonation's missing
  `roles/iam.workloadIdentityUser`; Keycloak's rejected client and disabled service accounts — each a
  reason word with an explanation, the provider's text matched and never repeated.

## 5. Design & Architecture

`upstreams/vertex/federation.py` holds the acquirers, `credentials.py` chooses one from the settings
at start. Both feed `aira_common.tokens.TokenSource`, which caches, refreshes ahead of expiry and
serves a held token through a failed refresh, as for a key. Nothing above the transport changes.

## 8. Security & Privacy

No Google secret is stored. The Keycloak client secret is a credential (Vault; refused by the config
renderer). The subject token is sent only to Google hosts, checked at start. Nothing about a person
is transmitted: the identity exchanged is the gateway's client, not a user. The privacy register is
unchanged.

## 10. Testing & Acceptance Criteria

`gateway/tests/test_keyless_google_access.py`, one `httpx.MockTransport` playing Keycloak, STS, IAM
credentials, the metadata server and Vertex: what each is sent, which token reaches Vertex, every
start-time refusal, every named cause. Mutations `KG-1`–`KG-19`. Not tested against Google itself:
the runbook (`docs/INTEGRATIONS.md`, Vertex D) is the procedure for the first real installation.
