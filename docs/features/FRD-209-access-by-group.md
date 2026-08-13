# FRD-209 — Access by group, and one place to search for whoever gets it

> Phase: 2 · Status: **Done** · Owner: AIRA
> Related: [`FRD-102`](FRD-102-attribution.md) (attribution from Keycloak groups),
> [`FRD-201`](FRD-201-keycloak-rbac.md) (roles → Django groups), [`FRD-202`](FRD-202-usecase-crud.md)
> (membership), [`FRD-206`](FRD-206-console-truthfulness.md), `ADR-0007`, `ADR-0009`

## 1. Why

Access to a use case is granted **one person at a time**, in this console, by username. Two things
are wrong with that, and they are the same thing seen from two sides.

**It does not scale to how organisations actually work.** A department has a group. Somebody joins,
somebody leaves, and the identity provider already knows — but AIRA does not, so a use case's
membership drifts from the moment it is written.

**There are two answers to "is this person a member", and they disagree.** The gateway derives
membership from **Keycloak groups** named `/use-cases/<slug>` (`FRD-102`). Management derives it
from `UseCaseMembership` rows and `django-guardian`. A use case created in this console creates the
second and not the first — which is exactly the defect `FRD-208`'s round surfaced: an administrator
opened their own use case's Traces tab and was told, correctly, that the identity provider does not
consider them a member.

This closes both: **a grant binds a principal to a use case, and a principal is a group or a
person.** The identity provider stays the source of truth about who is in a group.

## 2. What a grant is

```
grant := (use case, subject, role)
subject := group(path) | user(username)
role    := admin | user
```

- **`user`** — may make requests through the gateway attributed to this use case, and may see it
  and its figures in the console.
- **`admin`** — additionally may change what happens inside it: members, keys, pipeline, budgets,
  limits, rules.

Deliberately the same two-value vocabulary `UseCaseMembership` already has. A third level ("may
read but not spend") is a real idea and not this one; adding it later means adding a value, not
reworking the model.

### 2.1 A group is a path, not a convention

A grant names a Keycloak **group path** — `/ai/kundenservice`, `/abteilungen/vertrieb/nord` —
whatever the realm actually uses. It is *not* required to be `/use-cases/<slug>`.

That convention stays working, because the dev realm and the demo depend on it and because it is a
perfectly good way to run a small installation. It is now one of two ways in, not the only one:

> **A caller's use cases are the union of** the `/use-cases/<slug>` groups their token carries,
> the group grants matching a group their token carries, and the user grants naming them.

Union, not precedence: a caller who is a member twice over is a member. Where the *roles* differ,
the **strongest wins** — being an admin by one route and a user by another makes you an admin,
because the alternative is an access rule that depends on the order rows happen to be read in.

### 2.2 Guardian already knows how to do this

`django-guardian` assigns object permissions to a user **or to a Django group**. `FRD-201` already
syncs realm roles onto Django groups on every authenticated request. So:

- A **group grant** assigns the object permissions to a Django `Group` mirroring the Keycloak group
  path.
- On every authenticated request, the caller's Keycloak group paths are synced onto their Django
  group membership, exactly as their roles already are.

Then `scope_queryset`, `may_admin`, `may_manage` and `is_member` need **no change at all**: guardian
resolves user-and-group permissions in one query. The one place that does change is `is_member`,
which reads `UseCaseMembership` directly and must learn about groups.

This is the whole reason to do it this way. The alternative — a second permission path beside
guardian's — is a second chance to forget one, which is the mistake this project has already made
between the two planes.

## 3. Searching for whoever gets the grant

A grant needs a principal, and typing a group path from memory is how a grant ends up naming a group
that does not exist — silently, since a path that matches nobody simply never applies.

`GET /api/v1/directory/?q=` returns **groups and users together**, from Keycloak's Admin API:

```json
{ "results": [
  { "kind": "group", "id": "/ai/kundenservice", "label": "kundenservice", "detail": "/ai" },
  { "kind": "user",  "id": "ada",               "label": "Ada Lovelace", "detail": "ada@example.org" }
] }
```

One endpoint rather than two, because the question a person is asking is "who should get this",
not "am I about to name a group or a person". The kind is in the answer.

**Without an admin client configured, it still works** — degraded, and it says so: the directory
falls back to the users Management already knows (everyone who has signed in) and the group paths
already granted somewhere. That is enough to run the demo and to re-grant an existing group, and it
cannot invent a group nobody has ever used. The console says which of the two it is showing, because
"no results" from a degraded directory and "no such group" are different answers.

**Read-only, and no write ever reaches Keycloak.** AIRA does not create groups, does not add people
to them, and does not delete them. The identity provider is the source of truth; a console that
edited it would be a second place to change who works where.

## 4. What the gateway does

The gateway cannot ask Management on the request path — that has been true since `FRD-204` and it
does not change here. Group grants are distributed over Kafka into a read-model table
`use_case_groups(use_case, group_path, role)`, exactly as members, keys, budgets and limits already
are.

A token's group paths are already in the claim (`groups`), so resolving membership is a lookup
against that table. It is cached for a few seconds — the same shape as the suspension cache
(`FRD-503` §4.1) and for the same reason: read every request, written rarely.

**Degradation is decided.** If the read-model is unreachable, the `/use-cases/<slug>` convention
still resolves from the token alone, because it needs no lookup. A caller who was a member *only*
by group grant is refused, not admitted: the moment a control cannot be evaluated is the worst
moment to assume it passes (`FRD-405`, `FRD-125`).

## 5. Functional requirements

**FR-1** — A use case's access list holds **group grants and user grants**, each with a role.

**FR-2** — Granting to a group requires the same permission granting to a user does: administering
that use case, or being a Global Administrator.

**FR-3** — `GET /api/v1/directory/?q=` searches groups **and** users, returns at most 25 of each,
and states whether it is answering from Keycloak or from what Management already knows.

**FR-4** — The console's access panel searches, shows the kind of each result, takes a role, and
grants. It lists both kinds together, each with its kind and role, and can revoke either.

**FR-5** — A revoked grant removes the object permissions it created, and nothing else. Revoking a
group grant must not remove access somebody also holds directly.

**FR-6** — The gateway resolves a caller's use cases as the **union** described in §2.1, with the
strongest role winning, and refuses membership it cannot evaluate.

**FR-7** — Every grant and revocation is an event on `aira.use-cases`, so the gateway's read-model
converges without a poll.

**FR-8** — A group grant naming a group nobody is in is **not an error** — the identity provider
may fill it tomorrow. It is shown as granted, and the console says how many known people it
currently reaches so an empty one is visible rather than silent.

## 6. What this does not do

- **No writes to Keycloak.** See §3.
- **No nested-group expansion.** A grant on `/ai` does not cover `/ai/kundenservice` unless the
  token carries both paths — and Keycloak's default is that it does, because a member of a subgroup
  carries the parent path too. Inventing our own hierarchy on top would be a second answer to a
  question the identity provider already answers.
- **No change to the role vocabulary.** Two roles, as today.

## 7. Testing, as built

| Layer | Count | Where |
|---|---|---|
| Shared vocabulary + directory client | 36 | `libs/tests/test_access.py`, `test_directory.py` |
| Management | 41 | `test_group_grants.py`, `test_directory.py`, `test_outbox_routing.py` |
| Gateway | 15 | `gateway/tests/test_group_grants.py` |
| Console | 21 | `access-panel.spec.ts` |
| Live stack | 85 | `tests/integration/test_access_round.py`, `test_access_by_group.py` |
| Browser | 7 | `e2e/tests/access-by-group.spec.ts` |
| Mutations | `N30`–`N39` | 271 properties in total |

The properties that matter, each asserted where only that layer can see it:

- A person in a granted **group** calls the gateway and sees the use case in the console, **without
  any row naming them** — asserted against a real realm, a real token and the running gateway.
- Leaving the group removes both, on the next token.
- The **strongest role wins** when two grants overlap, and lowering a grant actually lowers it.
- Revoking a group leaves a direct grant intact.
- The directory never returns a credential and never writes — there is no endpoint that could.
- A grant that reaches nobody is visible as such.
- A request that arrived by a group grant leaves **the same audit row** as any other.

## 8. What the round found

Three defects, none of which any hermetic suite could have seen, and all of one family: **a correct
half with nothing carrying it to the other side.**

### 8.1 An event with no topic — the third instance of this shape

The first grant was written, listed, and shown in the console, and reached the gateway **never**.
`record_to_outbox` matches an event type against a hand-written map and, for anything unknown,
**returns silently**. That branch is deliberate — forward compatibility, so an older Management does
not crash on a newer event — and it is exactly what made the missing entry invisible. Nothing failed
on either plane.

`aira.rate-limits` and `aira.anomaly-rules` were both, previously, topics created by nothing. The
answer has been the same each time and is now a test: `test_outbox_routing.py` parses every
`emit(...)` in the source and compares it against the map, **in both directions**.

The reverse half found a second thing immediately: **`pipeline.deleted` had a topic and no
emitter**. Management has no endpoint that deletes a pipeline — clearing one is a `PUT` with no
steps. Dead configuration that reads as a working path, and the next person to need that event would
have assumed it already worked. Removed from the map; the gateway keeps its handler, because that
branch *is* forward compatibility.

### 8.2 A compacted topic needs a key per grant

The config topics are compacted: only the last message per key survives. Two group grants on one use
case keyed by the slug alone would mean the second **erased the first from the log**, and a gateway
rebuilding its read-model would silently lose access somebody actually holds. The key is
`slug|group_path` now, and a test asserts two grants do not collide.

### 8.3 A token with no `groups` claim grants nothing

The group-membership mapper existed on the SPA's client and on none of the service-account clients,
so their tokens carried no `groups` at all — and a group grant is unreachable without it. This is a
**configuration requirement of the feature**, not a bug in it, and it is now in `INTEGRATIONS.md`,
in the dev realm, and asserted by the live round.

### 8.4 Two the mutation harness found afterwards

Both were gaps in the tests rather than in the code:

- **`N33`** — "membership that cannot be evaluated is refused" was covered only for a read that
  fails on the **first** attempt, where there is nothing cached to serve anyway. The property that
  matters is failing *after* a successful load: the moment a control stops being evaluable is the
  moment its last answer stops being evidence.
- **`G5`** — "adding a membership grants the permission it promises" stopped being defended when
  the console's member form moved into the access panel and its tests moved with it. The property
  was still true and nothing checked it. Both kinds of grant go through the same `_grant`, so one
  of them silently breaking would take the other with it.

### 8.5 One from refusing to leave an assertion weak

A grant on the bare realm root `/` was accepted. It can never match — every path a token reports
begins with a name — so it was permanently inert while reading to a person as "the whole realm". A
grant that cannot match anything is precisely what the path validation exists to catch; it is
refused now.
