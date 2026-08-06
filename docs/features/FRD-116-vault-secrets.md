# FRD-116 — Secrets actually read from Vault

> Phase: 8 (KIRA parity) · Status: **Done (2026-08-06)** · Owner: Vadim Scheibe · Last updated: 2026-08-06
> Origin: `kira_api.md` §10 (Vault AppRole), programme: `ADR-0010`. Extends `ADR-0007`.

## 1. Problem

`CLAUDE.md` §2 says: *"Secrets: only in HashiCorp Vault — never commit secrets."* Vault has been in
the Compose stack since Phase 0.

**No code reads from it.** Every secret the system uses — the database URL, the Google API key,
Django's `SECRET_KEY`, the Keycloak client secret — comes from an environment variable. The stated
policy and the implementation have been apart since the beginning, and the gap is invisible because
environment variables work perfectly well right up until the moment someone asks where the
production credentials live.

`FRD-115` makes it pressing rather than merely untidy: a Google **service-account private key** in
an environment variable is a different proposition from an API key. It appears in `docker inspect`,
in process listings, in orchestrator manifests, and in whatever logs those produce.

## 2. Goals & Non-Goals

**Goals**
- Both services read their secrets from Vault when configured, from the environment when not.
- Authentication by **AppRole**, with the secret-id sourced the way a container actually gets one.
- A deployment that is configured for Vault and cannot reach it **does not start**.
- Which secrets were loaded, and from where, is visible — the values never are.

**Non-Goals**
- **Dynamic database credentials.** Vault can issue short-lived Postgres credentials; that changes
  connection handling and belongs in its own decision.
- **Live rotation without a restart.** §5.4 — deliberate, and stated so nobody assumes otherwise.
- The predecessor's OIDC login fallback. AppRole plus a local development path covers what we need;
  a third authentication route is surface without a use case.
- Storing API-key hashes or user data in Vault. Those belong in the database and already are.

## 3. User Stories
- As an **operator**, I want production credentials to live in Vault so that they are not in a
  deployment manifest or an environment listing.
- As **IT Security**, I want a misconfigured deployment to fail closed rather than fall back to a
  development default.
- As a **developer**, I want `make up` to keep working with no Vault involvement at all.

## 4. Functional Requirements

- **FR-1 Vault-backed settings.** Both services resolve their secrets through one shared loader in
  `aira_common`.
- **FR-2 AppRole.** `role_id` from configuration; `secret_id` from an environment variable, a
  mounted file, or a local development token — in that order, first one wins.
- **FR-3 Precedence.** A key present in Vault wins over the environment. A key absent from Vault
  falls back to the environment. There is no silent third source.
- **FR-4 Fail closed.** Vault configured but unreachable, unauthenticated, or missing a required
  key → the process **refuses to start**, naming which key and which reason. Outside `local` this
  composes with `ADR-0007`'s existing rule that a development default is a boot failure.
- **FR-5 Never a request-path dependency.** Secrets are read once at startup. Vault going down
  later must not affect a running gateway.
- **FR-6 Values never surface.** Not in logs, spans, error messages, `/readyz`, or exception
  tracebacks. What is logged is the *key names* resolved and their source.
- **FR-7 Local is unchanged.** No Vault configuration → environment variables → `make up` and the
  hermetic tests behave exactly as today.

## 5. Design & Architecture

### 5.1 One loader, two services

`aira_common.secrets`: given a Vault address, mount, path and AppRole credentials, return a mapping
of key → value; given no Vault configuration, return empty. Both `GatewaySettings` and the Django
settings module consult it before falling back to the environment (FR-3).

Placing it in `aira_common` matters for the same reason `aira_common.roles` does: two
implementations of "where do secrets come from" will diverge, and the divergence will be discovered
in the service that was not tested.

### 5.2 Where the secret-id comes from

The awkward part of AppRole is that the secret-id is itself a secret, so it must arrive by a path
Vault did not provide. FR-2's order reflects how deployments actually work: an environment variable
(CI, simple deployments), a mounted file (Kubernetes projected volume — the credential is then on a
tmpfs and not in the manifest), and a local token for development. Each attempt and its outcome is
logged by *name*, so "which path did it use" is answerable when it picks an unexpected one.

### 5.3 Failing closed is the whole point

The tempting behaviour when Vault is unreachable is to fall back to the environment and carry on.
That converts a broken secret store into a silent downgrade — and the environment in that scenario
usually holds a stale or development value, so the service starts, appears healthy, and is wrong.

`ADR-0007` already established the principle for `SECRET_KEY` and `DEBUG`: outside `local`, a
development default is a refusal to boot. FR-4 extends it. The error names the key and the reason,
because "Vault unavailable" and "key not found at that path" call for different actions.

### 5.4 Restart-to-rotate, and why that is enough for now

Secrets are read at startup and held for the process lifetime. Rotating one requires a restart.

Live re-reading sounds better and is not free: it needs a refresh loop, lease renewal, and a story
for what happens to in-flight work when a credential changes underneath it — and it would put an
availability dependency on Vault that FR-5 deliberately removes. With rolling deployments a restart
is a routine operation, and the credential most likely to rotate (the Vertex service account,
`FRD-115`) already has its own token refresh built in.

This is recorded as a decision so that the absence of live rotation is not later read as an
oversight.

## 6. Data Model

None. No secret is persisted by AIRA; Vault is read, never written.

## 7. API / Interface Contract

Configuration only:

```
AIRA_VAULT_ADDR=https://vault.example.com
AIRA_VAULT_MOUNT=secret
AIRA_VAULT_PATH=aira/prod
AIRA_VAULT_ROLE_ID=…
AIRA_VAULT_SECRET_ID=…          # or AIRA_VAULT_SECRET_ID_FILE=/run/secrets/vault
```

Keys read: `database_url`, `google_api_key`, `vertex_credentials`, `django_secret_key`,
`keycloak_client_secret`, `redis_url` — each optional, each falling back per FR-3.

## 8. Security & Privacy

- FR-6 is the requirement to test hardest: a secret loader is exactly the code that ends up in a
  stack trace. The test asserts against captured logs, spans and exception text, not against
  intent.
- FR-4 removes the silent-downgrade path.
- The AppRole secret-id is itself never logged, and the file path (not its content) is what appears.
- Vault's own audit log records who read what; ours records only that a read happened.

## 9. Observability

One structured line at startup: the resolved key **names**, each with its source
(`vault` / `env` / `default`), and the AppRole path used. That single line answers most of the
questions a misconfiguration produces, and contains nothing sensitive.

## 10. Testing & Acceptance Criteria

- **Unit** — Vault values win over the environment; absent keys fall back; no configuration yields
  pure environment behaviour; each secret-id source is tried in order and the first wins.
- **Unit (fail closed)** — configured-but-unreachable, bad credentials, and missing-required-key
  each refuse to start with distinct messages. Written to fail first against a fall-back-to-env
  implementation, because that is the mistake this FRD exists to prevent.
- **Unit (leakage)** — provoke a failure with a known secret value loaded and assert the value
  appears in **no** captured log record, span attribute or exception string.
- **Integration** — against the Compose Vault: seed a secret, start the gateway configured for
  Vault, and confirm it uses the Vault value and not the environment one. This is the test that
  proves the feature exists at all, given that today it does not.
- **Mutation** — the precedence order actually holds; the fail-closed branch actually refuses; the
  startup log actually omits values.

**Acceptance**
- *Given* a Vault holding `database_url` and an environment holding a different one, *when* the
  gateway starts configured for Vault, *then* it connects using Vault's value and logs
  `database_url: vault`.
- *Given* the same configuration with Vault stopped, *when* the gateway starts, *then* it exits
  with a message naming Vault as unreachable, and does not start on the environment value.

## 10a. What was built (2026-08-06)

`aira_common.secrets` — AppRole login, KV-v2 read, and the precedence — plus a pydantic
`VaultSource` that both planes inherit through `BaseAiraSettings`.

**A settings source, not an injection into `os.environ`**, and that is the security half of the
feature rather than a style choice. Values placed in the environment are readable from `/proc`,
inherited by every subprocess, and reach any library that dumps the environment on a crash. Here
they exist only inside the settings object.

The source is loaded **once** and cached. Reading Vault per settings object would make the number
of calls depend on how often somebody happens to construct one, and FR-5 is explicit that Vault is
a startup dependency and never a request-path one. An integration test asserts the count, because
"once" is the kind of property that quietly becomes "per request" during a refactor.

Verified against the Vault in the stack, including a **real AppRole** the test creates with a
policy scoped to its own path and removes afterwards — so the case that matters most (a credential
that may read one path and not another) rests on Vault's own decision rather than on ours. 33
hermetic tests, 13 integration, mutations **V1**–**V6**.

### One thing worth recording about the tests

The first version of "no value ever reaches a log" used pytest's `caplog`. It passed — and it
would have passed against a loader that printed every secret in full, because these logs go through
structlog and never reach the stdlib handler `caplog` watches. A test that cannot fail is the most
expensive kind here, since the thing it claims to guard is the one that ends careers. It captures
through `structlog.testing.capture_logs` now.

## 11. Dependencies & Risks

- **`FRD-115`** is the strongest motivation; this FRD is independently useful and can ship first.
- **Risk — a broken Vault becomes a deployment outage.** That is FR-4 working as designed, and the
  trade is deliberate: a service running on stale credentials is worse than one that will not
  start. FR-5 confines the exposure to startup.
- **Open** — whether the target environment provides AppRole or something else (Kubernetes auth,
  cloud IAM auth). The loader is written so the auth method is one seam.

## 12. Rollout / Demo

The Compose Vault gains a seeded development secret and a documented `make` target that writes it,
so the Vault path is demonstrable locally — and so the integration test above has something real to
read. Demo mode itself stays on environment variables (FR-7).
