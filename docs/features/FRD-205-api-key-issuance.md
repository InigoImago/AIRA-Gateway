# FRD-205 — Self-service API-key issuance (Management → Gateway)

> Phase: 2 · Status: **Done** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §5 (FR-GW-2, FR-MG-*); `docs/ROADMAP.md` Phase 2; **ADR-0006**; builds on FRD-201/202/204

## 1. Summary
Move API-key **issuance** to Management (per ADR-0006): a use-case member creates an API key **bound
to a use case**, the plaintext is shown **once** in the UI, and Management stores only the hash +
metadata. The key (prefix + hash + scope, **never plaintext**) is distributed to the Gateway via
Kafka into its `api_keys` read-model, where validation already lives (FRD-101). The Gateway CLI
becomes a break-glass tool only.

## 2. Goals & Non-Goals
**Goals**
- Management `ApiKey` model (hash, prefix, subject/owner, **use_case**, label, active, timestamps).
- `POST /api/v1/use-cases/{slug}/api-keys` issues a key (returns plaintext once); list/revoke.
- Publish `api_key.created`/`api_key.revoked` (prefix, hash, subject, use_case, status — no plaintext)
  to Kafka; Gateway consumer upserts its read-model.
- Gateway: API-key principals carry the bound **use_case**, so key requests are attributed/authorized
  to that use case **without** a selector (implicit binding — the counterpart to OIDC's selector).

**Non-Goals**
- Changing OIDC (unchanged). Rotating keys / expiry policies (later).

## 3. Functional Requirements
- **FR-1 Issue**: a use-case admin/member issues a key for a use case they may access; plaintext
  shown once; only hash+metadata stored.
- **FR-2 List/revoke**: list keys (masked, prefix only) for a use case; revoke → publish revocation.
- **FR-3 Distribution**: `api_key.*` events via Kafka (FRD-204) → Gateway read-model upsert/deactivate.
- **FR-4 Gateway binding**: the Gateway's `ApiKey` read-model gains `use_case`; a verified API-key
  `Principal` carries it; `require_attribution` uses the key's use case (no selector needed) and does
  not require group membership.
- **FR-5 Security**: plaintext never leaves Management except once to the browser; never in events/logs.

## 4. Design & Architecture
- Management `apikeys` app: model + serializers/viewset (issue/list/revoke) + guardian scoping.
- Reuse the key format/hash from the gateway (`aira_<prefix>_<secret>`, SHA-256) — extract the
  generation/hash helpers to `aira_common` so both sides agree.
- Gateway consumer (FRD-204) writes the `api_keys` read-model; `use_case` column added by migration.

## 5. Testing & Acceptance
- Hermetic tests: issue returns plaintext once + stores hash; event carries no plaintext; Gateway
  applies the event and validates the key with the bound use case; revoke deactivates. Keep green.
- **Acceptance**: issue a key in the UI for `demo-uc`, call the gateway with it (no `/uc` selector) →
  attributed to `demo-uc`; revoke → the key stops working after propagation.

## 6. Dependencies & Risks
- Builds on FRD-201/202/204. Realizes ADR-0006. Risk: eventual-consistency for revocation → bounded +
  monitored; break-glass CLI remains for emergencies.
