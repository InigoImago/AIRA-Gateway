# FRD-632 — Configuration changes have a history

> Phase: 6 (governance) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner's ticket from Stage T — adding a user to a use case left an access line and
> `published 1 events`, neither in the log store and neither saying who added whom with which role;
> `GAP-ANALYSIS.md` §3.1 had named it. Related: `FRD-204` (the outbox and its events), `FRD-614`
> (the roles' own history, `RoleChange`), `FRD-616` §1 (the fourth question a SIEM asks),
> `FRD-631` (the line), `FRD-627` (who may see people), `FRD-625` (the notice).

## 1. Summary

Every change to the configuration the gateway enforces is recorded twice: as a row in Management's
database — who made it, whom it concerns, when, and the state before and after — and as a
`config.changed` line that says only *that* it changed, with the kind, the id, the action, the
actor and the person as pseudonyms, and the names of the fields. The row is readable by the roles
that may see people, under *Platform → Configuration changes*, and kept for
`AIRA_CONFIG_HISTORY_DAYS` (730 by default, owner's decision).

## 2. Goals & non-goals

**Goals:** answer "who changed this permission, budget, limit, rule, model or role, and what was
there before" for every such change, without a value with a person in it in the log.

**Non-goals:** Management-only content (the question catalogue, test runs and ratings, which keep
their author on their own rows) and a person's own acknowledgement of the notice. A directory search
is not stored (`FRD-631` FR-12): its line says who searched.

## 3. User stories

- As **IT Security**, I want to see who granted a person access, when, and with which role, so that
  an incident can be traced to a decision and its author.
- As an **auditor**, I want a change's state before and after, so that "what was there before" has
  an answer.

## 4. Functional requirements

- **FR-1 — One place.** Every change the gateway enforces is announced on `usecases.events`, from
  inside its transaction; the history subscribes there, after the outbox. A row per event, in the
  same transaction: a change that rolled back has no row.
- **FR-2 — The row**: time, actor (or the process when no person acted), event type, kind, action,
  the thing's key, use case, person, before, after, the names of the fields that changed, trace id.
  A key's hash is never kept.
- **FR-3 — Before** is the last row about the same thing; for a thing last changed before the history
  began, what the outbox last sent for it; otherwise unknown. **Action**: the event's verb; an upsert
  is `updated` where the state before is known, `created` where a `POST` made it, `saved` otherwise.
- **FR-4 — The line**, `config.changed`, once the transaction commits: category `configuration`,
  object, object id (never a person), action, event type, use case, actor, person, granted role,
  fields changed, change id. No value.
- **FR-5 — Reading**: `GET /api/v1/configuration-changes/`, newest first, paged, filtered by kind,
  use case, actor or person; `incident.investigate` only.
- **FR-6 — Retention**: the relay deletes rows older than `AIRA_CONFIG_HISTORY_DAYS` (1–36500,
  default 730) on every run, and says so (`config_history_pruned`).
- **FR-7 — Coverage**: every write route of Management's API is recorded or exempt with a reason;
  a test reads the routes from Django's resolver and fails on one in neither list.

## 5. Design

`aira_management.apps.history`: model, recorder (subscriber), view, migration. The outbox's key
function became `address_of`, shared by both. The actor is read from the request being answered
(`apps.api.access.current_request`, set by the access-line middleware). The console page is
`features/platform/configuration-changes-page`.

## 6. Data model

`history_configurationchange` (Management): `id`, `changed_at`, `actor`, `process`, `event_type`,
`kind`, `action`, `object_key`, `use_case`, `person`, `before`, `after`, `fields_changed`,
`trace_id`. Migration `history.0001_initial`, applied by `management-migrate` like every migration.

## 7. API

`GET /api/v1/configuration-changes/?kind=&use_case=&actor=&person=&page=&page_size=`.

## 8. Security & privacy

The row names people in plain text and is therefore behind `incident.investigate`. It is a new
stored processing: in the register under *administration*, with its retention and recipients, in
both languages (new edition).

## 10. Testing

`management/backend/tests/test_configuration_history.py`, `test_every_write_is_in_the_history.py`;
the console spec. Mutations `CH-*`.

## 11. Stated gaps

- A thing changed for the first time after the upgrade, whose last change is older than the outbox's
  thirty days, has no state before; a `POST` that changes a member's role then reads `created`.
- A change made directly in the database or by the gateway's CLI is not recorded here.
