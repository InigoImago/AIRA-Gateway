# ADR-0007 — Security hardening baseline (authorization boundaries, safe defaults, input bounds)

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** Vadim Scheibe

## Context

Phases 0–4 delivered the gateway, the control plane, and the SPA feature by feature. Several
decisions taken for convenience during that build-out are not defensible once AIRA is treated
as what it is: a governed, multi-tenant chokepoint in front of paid LLM capacity, holding an
audit trail that a compliance function is meant to rely on.

A review of the whole codebase surfaced defects across three categories.

**1. Missing authorization boundaries.**
- `POST /v1beta/pipeline:dryRun` and `GET /v1beta/usage/{use_case}` were deliberately
  unauthenticated ("builder utilities"). But the dry-run executes the real pipeline engine,
  including LLM-backed filter and routing steps, against a caller-supplied `model`,
  `instruction`, and prompt. Anyone who can reach the gateway could use it as a free relay to
  the configured upstream — outside budgets, attribution, and request logging. The usage
  endpoint leaked per-use-case consumption for any guessable slug.
- Issuing an API key required only *visibility* of a use case. Because the governance roles
  (`global-admin`, `it-steuerung`) get organisation-wide read visibility from `scope_queryset`,
  a read-only oversight user could mint a data-plane key bound to **any** use case in the
  organisation — an oversight role escalating into full data-plane access.
- Django users were provisioned by `preferred_username`. Keycloak usernames can be renamed and
  re-issued, so a username freed by a departing employee and handed to someone new would carry
  the previous holder's object-level permissions and use-case memberships to that new person.

**2. Unsafe defaults.**
- `SECRET_KEY`, `DEBUG`, and `ALLOWED_HOSTS=*` shipped with development values that are
  actively dangerous outside a laptop, with nothing to stop a deployment inheriting them.
- `X-Forwarded-For` was trusted unconditionally, so any client could dictate the `source_ip`
  written to `request_logs` — the audit trail was forgeable by design.
- Gemini clients authenticate with `?key=<api key>`; the ASGI instrumentation copied the raw
  query string onto server spans, shipping live credentials to the trace backend.
- The Keycloak dev realm used `redirectUris: ["*"]` on a **public** client — the textbook
  precondition for stealing an authorization code — plus the direct-access (password) grant.
- The SPA set `requireHttps: false`, permitting the code flow over plaintext HTTP to any
  issuer, and had no CSP.
- A re-delivered `api_key.created` event (delivery is at-least-once) reactivated a key that had
  already been revoked.

**3. Unbounded input.**
- Request bodies were buffered with no ceiling.
- The use-case selector (`X-AIRA-Use-Case`) was passed through unvalidated into the audit log,
  read-model lookups, and span attributes.
- Operator-supplied injection-filter patterns were compiled and run, unbounded, against the
  full prompt on the gateway's hot path and in the browser's UI thread. A nested quantifier
  (`(a+)+`) backtracks exponentially: one saved pipeline could stall a shared gateway worker.

## Options considered

- **Leave the builder endpoints open, document the risk.** Simplest, and it keeps the SPA
  working with no gateway configuration. Rejected: an unauthenticated endpoint that reaches a
  paid upstream with attacker-controlled prompts is a cost and data-exfiltration channel, not a
  documentation problem. FRD-306 already listed "authenticated dry-run" as pending hardening.
- **Rate-limit the open endpoints instead of authenticating them.** Cheaper for the SPA, but
  rate limiting bounds abuse rather than preventing it, and does nothing about the usage-data
  leak.
- **Detect pathological regexes exactly / run them under a timeout.** Python's `re` cannot be
  interrupted mid-match, and deciding "is this regex safe" in general is not tractable.
  Rejected in favour of a validation heuristic at authoring time plus hard bounds at execution.
- **Key Django users on `sub` and migrate by rewriting usernames.** Correct but disruptive:
  usernames are the handle used by memberships, budgets, and key ownership, and are shown in
  the UI. Rejected in favour of a separate identity binding.

## Decision

Adopt a hardening baseline across all three components. It changes no features; it constrains
who may invoke them, what they may submit, and what the system does by default.

**Authorization**
- `pipeline:dryRun` and `usage/{use_case}` require an authenticated principal; `usage` also
  authorizes the principal against the requested use case (`authorize_use_case`, shared with
  `require_attribution` so both paths cannot drift).
- Issuing an API key requires **membership** of the use case (or `global-admin`). Read
  visibility never implies the right to act inside a use case.
- Keycloak subjects are bound to Django users through an `OidcIdentity` record. Existing
  unbound users are adopted on first login (trust on first use) so nothing loses its
  permissions; afterwards a reused username can never resolve to the earlier account.

**Safe defaults**
- The management backend refuses to start outside `environment=local` while it carries the
  default `SECRET_KEY`, a wildcard `ALLOWED_HOSTS`, or `DEBUG`; `DEBUG` is additionally forced
  off anywhere but local. Security headers (nosniff, `X-Frame-Options: DENY`, referrer policy,
  COOP, HSTS outside local) are on by default.
- `X-Forwarded-For` is honoured only when `AIRA_TRUST_FORWARDED_FOR` is set; otherwise the
  socket peer is recorded.
- Credential-bearing query parameters are redacted before the query string reaches a span.
- The dev realm pins redirect URIs and web origins to the local dev hosts and disables the
  direct-access grant; the SPA uses `requireHttps: 'remoteOnly'`, strict discovery validation,
  and ships a CSP that keeps scripts same-origin.
- Revocation is terminal in the gateway read-model: a replayed `created` event refreshes
  metadata but never reactivates a revoked key.
- `seed_demo` (which creates well-known accounts including a superuser) runs only locally or
  with `AIRA_DEMO_MODE`, unless forced.

**Input bounds**
- Request bodies are capped (`AIRA_MAX_REQUEST_BYTES`, default 8 MiB), rejected before
  buffering, whether or not `Content-Length` is declared.
- The use-case selector must match the Management slug charset (`^[a-z0-9-]{1,64}$`).
- Pipeline configs are bounded at authoring time (step, pattern, model, and category counts and
  lengths) and patterns with nested quantifiers are rejected with an explanation. The gateway
  independently bounds what it will execute, and the browser preview bounds what it will match.

## Consequences

- **Positive:** no unauthenticated path reaches an upstream provider or exposes tenant data; an
  oversight role can no longer escalate into data-plane access; the audit trail is no longer
  client-forgeable; credentials stay out of traces; a misconfigured production deployment fails
  loudly instead of quietly serving with a known signing key.
- **Trade-off — the SPA's dry-run and consumption views now need a credential the gateway
  accepts.** In practice that means enabling OIDC on the gateway against the same realm
  (`AIRA_OIDC_ENABLED`/`AIRA_OIDC_ISSUER`, see `.env.example`). With OIDC off, the two views
  degrade: the budget tab still renders limits without consumption, and the dry-run reports
  that the gateway did not accept the login. Everything else is unaffected.
- **Trade-off — the nested-quantifier check is a heuristic.** It rejects the classic
  catastrophic-backtracking shapes, not every possible one; the execution bounds are the
  backstop. Patterns that fail to compile are still matched literally, as before.
- **Trade-off — `directAccessGrantsEnabled: false`** removes the password grant from the dev
  realm, so scripts that fetched a token with username/password must use the code flow (or
  re-enable it locally).
- **Follow-ups:** per-caller rate limiting on the gateway; an explicit `oidc_audience` in
  every deployment (an empty audience means any token from the realm is accepted); moving
  the remaining dev credentials in `deploy/compose` into Vault.

## Addendum (2026-08-05) — what running it against the live stack changed

Verifying this ADR end-to-end (see `e2e/` and `tests/integration/`) surfaced three things the
hermetic suites could not:

1. **The hardened realm broke Keycloak's boot.** The client `description` added here was 259
   characters; Keycloak's `CLIENT.DESCRIPTION` column is `varchar(255)`, so the import aborted
   and the container refused to start. Shortened. Note that Keycloak imports a realm only when
   it does not already exist (`IGNORE_EXISTING`) — an existing deployment keeps the *old*,
   wildcard configuration until the realm is recreated. That is now stated in
   `deploy/compose/README.md` and `e2e/README.md`.
2. **The dev realm had none of the five AIRA roles**, and its single user had no roles at all.
   Since FRD-201 makes Keycloak the source of truth for roles, the documented demo acceptance
   ("log in, see role-appropriate nav, create a use case") could not pass. The realm now carries
   the five roles and one user per role, with usernames matching the Django seed so a login
   adopts the seeded account rather than provisioning a duplicate.
3. **Authorizing `usage` by Keycloak group membership has a consequence** that was not thought
   through here: use cases are administered in Management, but the gateway's notion of
   membership for OIDC callers is the Keycloak group `/use-cases/<slug>` (FRD-102). A use case
   created in the SPA therefore has no group, and its consumption numbers stay hidden from
   everyone. The strict check is kept — it is the safer default and matches how the data plane
   authorizes — and the UI now says precisely that instead of implying an outage. Making
   Management-administered membership visible to the gateway (it already ships a
   `use_case_members` read-model, keyed by username rather than by `sub`) is the proper fix and
   is listed as a follow-up.
