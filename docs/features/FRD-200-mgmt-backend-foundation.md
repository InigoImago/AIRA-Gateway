# FRD-200 — Management backend foundation (DRF API + OIDC)

> Phase: 2 · Status: **Done (Phase 2)** · Owner: Vadim Scheibe
> Related: `docs/PRD.md` §4.2, §5 (FR-MG-*); `docs/ROADMAP.md` Phase 2; builds on FRD-101

## 1. Summary
Turn the Django/DRF skeleton into a real API foundation for the management control plane: a
versioned REST API under `/api/v1/`, **OIDC bearer authentication** against Keycloak (the Angular SPA
sends the Keycloak JWT), a consistent DRF error envelope, and a `me` endpoint exposing the
authenticated user and roles. JWT verification is shared with the gateway.

## 2. Goals & Non-Goals
**Goals**
- DRF API under `/api/v1/`; JSON only; consistent error envelope; pagination defaults.
- **OIDC bearer auth**: a DRF authentication class validating the Keycloak JWT (JWKS: signature,
  iss, exp, optional aud) → a Django user (get-or-create from `sub`/claims) + roles/groups on request.
- `GET /api/v1/me` → current user (subject, email, roles, use-case groups).
- Shared JWKS/JWT verification (extract to `aira_common` so gateway + management agree).

**Non-Goals**
- RBAC enforcement/roles model (FRD-201), use-case CRUD (FRD-202), Kafka (FRD-204), UI (FRD-203).

## 3. Functional Requirements
- **FR-1 API layout**: `config/urls.py` mounts `api/v1/` with a DRF router; browsable API off in prod.
- **FR-2 OIDC auth class**: `KeycloakJWTAuthentication` (DRF) verifies the bearer JWT via JWKS and
  resolves/creates a Django `User` (username=`preferred_username` or `sub`, email from claims).
  Missing/invalid token → 401; unauthenticated access to protected views → 401.
- **FR-3 Claims on request**: attach verified claims (roles, groups) to the request for FRD-201.
- **FR-4 `me` endpoint**: returns subject, username, email, realm roles, and use-case groups.
- **FR-5 Error envelope**: a DRF exception handler returns `{"error": {"code","message","details"}}`
  consistent with the gateway style.
- **FR-6 Shared verification**: JWT/JWKS logic lives in `aira_common.oidc` (used by gateway too).

## 4. Design & Architecture
- `aira_common.oidc.JwtVerifier(issuer, audience, jwks_uri)` → returns verified claims or None.
  The gateway's `OidcValidator` and management's DRF auth class both use it (DRY).
- Management settings gain `oidc_issuer`/`oidc_audience` (from env/Vault). JWKS fetched + cached.
- `me` view is DRF `APIView` requiring authentication.

## 5. Security & Privacy
- Bearer-only for the API (no session cookies needed for the SPA); JWT verified against Keycloak
  JWKS. No secrets logged. Users auto-provisioned from verified claims only.

## 6. Testing & Acceptance
- Hermetic tests (SQLite + self-signed RS256 + fake JWKS): valid token → 200 `me` with roles;
  no/invalid token → 401; user auto-provisioned once (idempotent). Coverage gate stays green.
- **Acceptance**: `GET /api/v1/me` with a valid Keycloak token returns the user + roles; without a
  token returns 401 in the error envelope.

## 7. Dependencies & Risks
- Builds on FRD-101 (shared OIDC). Feeds FRD-201/202/203. Risk: JWKS/issuer config drift → shared
  verifier + tests. Risk: user auto-provisioning collisions → key on `sub`.
