# FRD-613 — One person, one identity, from the door to the audit row

> Phase: 6 (governance) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner: *"go through every aspect that has to do with auth — the
> references to username and budgeting in use cases, general possible bugs in auth handling,
> whether users with the same username get compared — and think of at least 150 more cases where
> errors could occur, so that everything about user handling is securely implemented and tested."*
>
> Related: [`FRD-606`](FRD-606-per-person-consumption.md) (the name beside the subject),
> [`FRD-604`](FRD-604-credential-accountability.md) (who answers for a credential),
> [`FRD-209`](FRD-209-access-by-group.md) (grants and the directory),
> [`FRD-205`](FRD-205-api-key-issuance.md), [`FRD-503`](FRD-503-incident-response.md),
> [`FRD-505`](FRD-505-requests-and-prompts.md), `ADR-0007`, `ADR-0017`.

## 1. Problem

Nothing here was a new feature. This is a **sweep**: one question — *who is this caller* — asked of
every place in both planes that answers it, because the project's own record says that is the
question it keeps getting wrong in a different place each time.

`LESSONS.md` §1 already carries the shape under *"the same column read in two alphabets"*, with
five instances and an instruction: **correct the definition, then grep for the comparison.** The
definition was corrected three rounds ago (`aira_gateway.scopes.person`). The grep had never been
done. Ten of the fourteen findings below are readers that were never visited; three are places
where an identity crosses a boundary and is *chosen* rather than derived; one is an anchor
character.

The sweep found **fourteen defects**, four of them serious enough to be called by name:

| | What | Where |
| --- | --- | --- |
| **1** | The seeded `admin` is a Django **superuser**, so object-permission checks bypass the directory entirely and removing the role in Keycloak changes nothing | Management, seed |
| **2** | Any token whose `preferred_username` matched an **unbound local account** was handed that account, its memberships and its permissions | Management, provisioning |
| **3** | A use-case **user** could issue an API key **owned by the use case's administrator**, and then read every stored prompt in a use case set to show each member only their own — recorded against the administrator | both planes |
| **4** | Removing somebody from a use case left every **API key** they held for it active and serving | Management |
| 5 | A `subject` suspension stopped one of a person's two credentials | gateway |
| 6 | Anomaly detection grouped one person into two buckets, halving every `subject` threshold | gateway |
| 7 | The trace query and its own predicate disagreed about whose a row is, for exactly one kind of caller | gateway |
| 8 | `DELETE …/members/<username>` could not address a username containing a dot (`first.last`) | Management |
| 9 | A person the directory offers, who has not signed in, could not be granted access at all | Management |
| 10 | A `preferred_username` longer than the column was a `DataError` — a 500 on a first sign-in | Management |
| 11 | A `groups` claim containing a non-string was an `AttributeError` inside token validation — a 500 on every request that caller makes | gateway |
| 12 | `^…$` accepted a **trailing newline**, in the use-case slug, the group path and the selector | both planes |
| 13 | A suspension's author was recorded as a directory id, which names nobody a human can look up | gateway |
| 14 | A payload read recorded the reader's subject and not their name, so one person's reads were filed under two names | gateway |

## 2. Why the four serious ones are serious

**They compose.** Individually each has a mitigating sentence. Together, #2 and #1 are an account
takeover with permanent authority: an installation that has run the seed — which includes the
shipped demo and `make showcase` — carries an unbound `admin` account with `is_superuser`, and
`is_superuser` short-circuits both `user.has_perm` and guardian's `get_objects_for_user`, so the
claim survives every role sync the directory could ever perform. #3 and #4 are the same story about
credentials: a member can mint a key that speaks as somebody more privileged, and a key outlives
the access it was issued under.

**None of them is a bug in a rule.** Every rule involved reads correctly. What was missing in each
case was that the rule had **two readers and one of them had never been visited** — which is the
whole content of `LESSONS.md` §1 and the reason this document is a sweep rather than a fix.

## 3. What an identity is here

One paragraph, because everything below follows from it.

> A caller has a **subject** and, usually, a **name**. The two credentials spell the subject
> differently: an OIDC token's is the directory's user id, an API key's is its owner's *username*.
> The **person** — `scopes.person(subject, username)` — is the name where there is one and the
> subject otherwise, and it is what every *allowance* and every *"is this mine"* is keyed on. The
> subject stays what an audit row is **about**, because a name can be reassigned and a subject
> cannot.

Two consequences that are not obvious and are now asserted:

- **A suspension aimed at a person must be typed as the name.** A directory id stops tokens from
  that realm and nothing else, because an API key never carries one — Management cannot put a `sub`
  on the wire, since a key is issued against a username. That is not a gap this side can close
  without asking the directory on the request path, which `FRD-204` forbids. It is a documented
  instruction (`docs/INTEGRATIONS.md` §2), and `test_a_directory_id_stops_the_token_and_says_
  nothing_about_a_key` is why the sentence has to stay there.
- **The whole scheme rests on one deployment property**: the directory must not let somebody rename
  themselves onto a colleague's name. Stated in `scopes.person` since it was written, and now
  restated where an administrator will read it.

## 4. Functional requirements

**FR-1** — Every reader of a caller's identity resolves **the person**: the per-head budget, the
rate-limit bucket, the kill switch, the detector, the trace list, the payload gate, the membership
lookup and the findings filter. A credential-scoped control (a key prefix) stays keyed on the
credential, because *"block this leaked key"* and *"stop this person"* are different acts.

**FR-2** — A local account is claimed by a `sub` only where somebody **invited** it
(`PendingIdentity`). An invitation is created deliberately, records who made it, is claimed once,
and is deleted by the claim. An account nobody invited is claimable by nobody.

**FR-3** — Granting access to somebody the **directory** knows but who has not signed in creates
their account and its invitation. A name the directory does not know is refused; a directory that
cannot be asked says so, in words that separate the operator's problem from the typist's.

**FR-4** — No account this system creates carries `is_staff`, `is_superuser` or a usable password,
and the seed **clears** all three on the accounts it owns, so an installation that ran an older
seed is repaired by the next run.

**FR-5** — Issuing a key **owned by somebody else** requires administering the use case, and the
owner must hold a grant on it that can be taken away — not the Global Administrator blanket.

**FR-6** — When somebody's access to a use case ends — a membership removed, or the group grant
they reached it through revoked — every active API key of that use case whose owner no longer holds
a grant is revoked, and the answer says which.

**FR-7** — Nothing a directory can put in a claim makes a first sign-in fail: a name is bounded to
its column and validated, an unusable one falls back to the subject, a malformed `groups` claim
confers nothing rather than raising.

**FR-8** — A member may be addressed by any username Django will store, including one containing a
dot.

## 5. Design notes

**`PendingIdentity` rather than a setting.** The alternative was to keep trust-on-first-use behind
a flag defaulting to off, which leaves the demo needing the dangerous value and every operator
choosing between a working walkthrough and a safe installation. An invitation is the same trust
made explicit, attributable and single-use, and it is *also* the mechanism FR-3 needs — one model
answers a security hole and a missing capability, because they were the same missing idea.

**Key revocation asks about the owner, not about who was removed.** The same sentence has to be
true after a group grant is revoked, where nobody was named at all. `holds_a_grant` is the
predicate, and it deliberately excludes the Global Administrator blanket: an owner has to be
somebody whose access can *end*, or the key rests on something that never closes.

**The detector groups by `coalesce(nullif(username, ''), subject)`.** Not a new column: the pairing
`FRD-606` introduced, read the way `payloads` already reads it. `nullif` because SQL reads `''` as
a value and `person` reads it as absence — no writer produces one today, and the two spellings of
one rule have to agree for reasons that do not depend on that staying true.

**`\Z`, not `$`.** Python's `$` also matches before a trailing newline. Three validators whose whole
purpose is that a string carries a restricted character set accepted one with a newline appended —
including the use-case slug, which is a **primary key on the other plane**.

## 6. Data model

- `api_pendingidentity` — one row per invited account (`user`, `invited_by`, `created_at`).
- `usecases.0014` — the slug validator's anchor, recorded so the model state matches the field.
- `payload_accesses.username` — the reader's name beside their subject, indexed, NULL before this.

## 7. Interface changes

`DELETE /api/v1/use-cases/<slug>/members/<username>/` and
`DELETE /api/v1/use-cases/<slug>/groups/revoke/` answer **200 with `{"revoked_keys": [...]}`**
rather than 204. A removal that silently deactivated two of somebody's credentials would be a
control whose whole effect the screen cannot state, which is `FRD-206` read backwards; the console
names them in the confirmation.

`POST …/api-keys/` answers **403** when a non-administrator names an owner other than themselves.

## 8. Testing

**248 hermetic cases** — 239 in five new files, 6 added to three existing Python suites and 3 to the console's access panel — and **ten mutations**
(`ONE1`–`ONE10`) — one per property that was live-broken on 2026-08-30.

| File | What it holds |
| --- | --- |
| `gateway/tests/test_one_identity_across_credentials.py` | every reader of a caller's identity, asked with both credentials |
| `gateway/tests/test_a_credential_is_answered_for.py` | what a key resolves to, and what it never confers |
| `gateway/tests/test_the_identity_boundary_takes_any_value.py` | every value a caller or a realm can put where an identity goes |
| `management/backend/tests/test_an_identity_is_claimed_once.py` | provisioning, invitations, collisions, column bounds |
| `management/backend/tests/test_access_ends_completely.py` | getting in, getting out, and who a key may be owned by |

Three of the ten mutations **survived their first run**, which is the whole reason the harness
exists: the kill-switch property was tested at the service and not at the call site that hands it
the person (the wire, again); the malformed-claim tolerance was tested one layer above the guard,
so both planes' own narrowing made the property look defended; and the superuser property was
tested on a fresh database, where the flag is already absent — the case that matters is the
installation that ran the *older* seed.

## 9. Open, deliberately

- **A key acts with its owner's standing** on the console endpoints the gateway serves, so a shared
  technical account's key reads content as that account. The control is FR-5 — only an
  administrator may point a key at somebody else — and the fact is stated rather than removed,
  because a key issued *for* a technical account is the feature `FRD-604` FR-5 exists to provide.
- **Username case.** Two directory accounts differing only in case are two people here, and neither
  inherits the other. Folding case would be a second answer to "who is this" beside the `sub`
  binding, which is the mechanism `ADR-0017` abolished roles to avoid.
- **A suspension typed as a directory id** stops tokens only — §3.
