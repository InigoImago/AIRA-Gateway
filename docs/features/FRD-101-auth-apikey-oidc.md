# FRD-101 — Authentication: API keys + OIDC bearer

> Phase: 1 · Status: **Done (Phase 1)** · Owner: Vadim Scheibe
> Related: `docs/PRD.md` §5 (FR-GW-2), §9; `docs/ROADMAP.md` Phase 1; `ADR-0005`; builds on FRD-100

## 1. Summary
Authenticate every gateway request via **either** a **self-generated API key** **or** an **OIDC
bearer token** validated against Keycloak. Both resolve to a common `Principal` (a subject +
auth method) that later phases use for attribution (FRD-102) and authorization (Phase 2). Credentials
are accepted the Gemini way (`x-goog-api-key` header / `?key=` query param) and the standard way
(`Authorization: Bearer …`). This FRD also establishes the **gateway's database layer** (for the
API-key store), which FRD-103 (persistence) will build on.

## 2. Goals & Non-Goals
**Goals**
- Issue API keys (prefix + high-entropy secret), store only a **hash**, support prefix lookup,
  revocation, an active flag, and a subject/label.
- Verify a presented API key in constant time; reject unknown/revoked keys.
- Validate Keycloak **OIDC JWTs**: signature via JWKS, issuer, audience, expiry.
- A single **auth dependency** that resolves a `Principal` from any accepted credential and guards
  the Gemini routes; health endpoints stay open.
- Establish the gateway **DB layer** (SQLAlchemy async) + an `api_keys` table.
- A CLI to mint keys, and a **demo key** seeded for demo mode.

**Non-Goals**
- Authorization / RBAC scoping (Phase 2) — this FRD authenticates, it does not authorize per
  use-case. Attribution to user/project/use-case is FRD-102.
- Managing keys via the UI (Phase 2). Alembic migrations (FRD-103; Phase 1 uses `create_all`).

> **Architecture note (ADR-0006):** API-key **issuance/lifecycle** ultimately belongs in
> **Management** (self-service UI, show-once, bound to use case), distributed to the Gateway via
> **Kafka** into a local **read-model**; the Gateway keeps only **validation**. The gateway-side
> generation + CLI in this FRD are a **Phase-1 bootstrap**; issuance moves to Management in Phase 2
> (ROADMAP `FRD-205`). The `api_keys` table here is the future read-model. **OIDC validation stays
> in the Gateway.**

## 3. User Stories
- As a **client project**, I authenticate to AIRA exactly as I do to Gemini — an API key in
  `x-goog-api-key` (or `?key=`) — and it just works.
- As a **user with SSO**, I call the gateway with a Keycloak `Bearer` token and am authenticated.
- As an **operator**, I mint and revoke API keys from a CLI; secrets are never stored in the clear.

## 4. Functional Requirements
- **FR-1 API-key format**: `aira_<prefix>_<secret>` — `prefix` (lookup handle, ~8 chars) and
  `secret` (≥32 chars, URL-safe random). The full key is shown **once** at creation.
- **FR-2 Storage**: persist `prefix`, `key_hash` (SHA-256 of the full key), `subject`, `label`,
  `is_active`, `created_at`, `revoked_at`. Never store the plaintext.
- **FR-3 Verify**: parse prefix → look up active key → constant-time compare the hash → resolve a
  `Principal(subject, method="api_key", label)`; else reject 401.
- **FR-4 Revoke**: mark a key inactive (`is_active=false`, `revoked_at=now`); revoked keys fail.
- **FR-5 OIDC validation**: fetch and cache Keycloak **JWKS**; validate JWT signature, `iss`, `aud`
  (configurable), and `exp`; resolve `Principal(subject=sub, method="oidc")`; else 401.
- **FR-6 Credential extraction** (precedence): `Authorization: Bearer <jwt-or-key>` →
  `x-goog-api-key: <key>` → `?key=<key>`. A `Bearer` value that is an AIRA key (`aira_…`) is treated
  as an API key; otherwise as a JWT.
- **FR-7 Auth dependency**: guards all Gemini routes; returns Gemini-shaped 401 on failure. Since
  ADR-0007 it also guards the pipeline dry-run and the usage endpoint. Only the health endpoints
  (`/healthz`, `/readyz`) remain unauthenticated.
- **FR-8 Demo/open toggle**: `auth_required` (default **true**). When false (pure demo) routes are
  open and a synthetic `demo` principal is used. Demo mode also **seeds a deterministic demo key**.
- **FR-9 CLI**: `python -m aira_gateway.cli api-key create --subject … [--label …]` prints the new
  key once; `… api-key revoke --prefix …`.
- **FR-10 Gateway DB layer**: async SQLAlchemy engine/session from settings (Postgres `aira_gateway`);
  `create_all` for Phase 1; test suite uses in-memory SQLite (hermetic).

## 5. Design & Architecture
```
request ─▶ extract credential (Bearer / x-goog-api-key / ?key=) ─▶ Principal resolver
             ├─ aira_… key ─▶ ApiKeyService.verify (DB: prefix lookup + hash compare)
             └─ JWT        ─▶ OidcValidator.validate (JWKS: signature/iss/aud/exp)
           Principal ─▶ request.state.principal ─▶ (FRD-102 attribution, Phase 2 authz)
```
- **Modules** (gateway): `db/` (engine, session, `ApiKey` model), `auth/` (`Principal`,
  `ApiKeyService`, `OidcValidator`, `credentials` extractor, `dependencies.require_principal`),
  `cli.py`.
- **Keycloak**: a `aira` realm (import file under `deploy/compose/keycloak/realms/`) with a client
  and issuer AIRA validates against. JWKS cached with a TTL; refresh on unknown `kid`.
- **Hashing**: SHA-256 is sufficient for high-entropy random keys (not passwords); compare with
  `hmac.compare_digest`.

## 6. Data Model
`api_keys`: `id` (uuid), `prefix` (unique, indexed), `key_hash`, `subject`, `label` (nullable),
`is_active` (bool), `created_at`, `revoked_at` (nullable).

## 7. API / Interface Contract
- No new public endpoints; adds an auth requirement to existing Gemini routes.
- Failure → Gemini error envelope, HTTP 401 `{"error":{"code":401,"message":"…","status":"UNAUTHENTICATED"}}`.

## 8. Security & Privacy
- Only key **hashes** stored; plaintext shown once. Constant-time comparison. JWTs verified against
  Keycloak's JWKS (no shared secrets). Keycloak admin/dev creds stay in Vault/env, not code.
- Auth failures are logged (with key prefix / token subject, never secrets) for the audit trail.

## 9. Observability
- Reuse FRD-001: add span attributes `auth.method` and `principal.subject`; a counter for auth
  failures by reason. No secrets in spans/logs.

## 10. Testing & Acceptance Criteria
- **Tests** (hermetic, SQLite + mocked JWKS): key generate/hash/verify/revoke; unknown/revoked/
  malformed key → 401; credential precedence; OIDC valid/expired/wrong-iss/wrong-aud/bad-signature;
  dependency guards Gemini routes (200 with valid key, 401 without); `auth_required=false` opens
  routes; CLI mints a working key. Coverage gate stays green.
- **Acceptance**:
  - **Given** a valid API key, **when** I call `:generateContent` with `x-goog-api-key`, **then** 200
    and a `Principal(method=api_key)` is attached.
  - **Given** no/invalid credential and `auth_required=true`, **then** a Gemini-shaped **401**.
  - **Given** a valid Keycloak bearer token, **when** I call a Gemini route, **then** 200 with
    `Principal(method=oidc)`.
  - **Given** a revoked key, **then** 401.

## 11. Dependencies & Risks
- Builds on FRD-100 (routes to guard). Establishes the DB layer FRD-103 extends. Feeds FRD-102.
- New deps: SQLAlchemy (async), asyncpg, aiosqlite (tests), a JWT/JWKS library (e.g. `pyjwt` +
  `cryptography`), httpx (JWKS fetch).
- Risk: Keycloak realm/JWKS setup friction → ship a realm import + document; cache JWKS with refresh.
- Risk: async DB in tests → in-memory SQLite with a shared connection pool.

## 12. Implementation slices
- **Slice A** — gateway DB layer + API-key model/service/CLI + credential extraction + auth
  dependency (API-key path) guarding Gemini routes + demo toggle/seed + tests.
- **Slice B** — OIDC bearer validation (Keycloak JWKS) + realm import + tests; plug into the same
  dependency.
