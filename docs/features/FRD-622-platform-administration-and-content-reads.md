# FRD-622 — Platform administration, and who read which content

> Phase: 6 (reporting) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner asked for two things with a data-protection review in mind. First, one setting
> per use case that decides what happens to prompts and responses: not stored; readable by the use
> case's administrators, with every person seeing their own requests; or readable by every member.
> Second, a place for the platform roles to see when somebody fully opened a request — prompt and
> response — from which use case and in which role.
> Related: [`FRD-505`](FRD-505-requests-and-prompts.md) (content by role, and the read record),
> [`FRD-404`](FRD-404-retention.md) (retention), [`ADR-0016`](../adr/ADR-0016-content-is-readable-and-every-read-is-recorded.md),
> [`FRD-206`](FRD-206-console-truthfulness.md) (which role sees what).

## 1. Summary

**Content visibility.** Most of this existed already, as two switches on a use case's
data-protection panel:
- `store_payloads` — whether prompts and responses are written at all;
- `restrict_members_to_own_requests` — whether a member sees only their own requests.

Together they expressed exactly three modes, and the panel offers them as one choice. **A fourth
was asked for on 2026-10-11** — *stored, and readable by Global Administrators and IT Security
only* — and a boolean cannot carry it, so the second switch became a three-valued
`payload_visibility`:

| mode | `store_payloads` | `payload_visibility` | who reads content |
| --- | --- | --- | --- |
| **Not stored** | off | — | nobody; there is nothing |
| **Global Administrators and IT Security only** | on | `oversight` | the two installation roles that may read any content |
| **Administrators, and each person's own** | on | `own` | those two · the use case's administrators · a member for their own requests |
| **Every member of the use case** | on | `members` | those two · everybody in the use case |

The new mode is the first one that withholds content from the use case's **own administrators**,
which is the whole of it: a mode that let them read would promise something it cannot keep.

**Platform administration.** The console gets a platform-administration area for the three
platform roles, reached from the header beside the user's name. It has a menu on the left and the
selected page on the right. The first page is the **content-read log**, the record `FRD-505` FR-6
has kept since August. Until now nobody could see that record.

## 2. Goals & Non-Goals

**Goals**
- One choice per use case for content, in the words a data-protection review uses.
- An area for platform-wide administration that the next platform page can join without redesigning
  the navigation.
- The content-read log, readable by the platform roles: when, who, which request, which use case,
  on what ground, and with which roles.

**Non-Goals**
- **No change to who reads content** (owner's decision). Global Administrator and IT Security
  read any stored content, on the ground `incident`. IT Steuerung reads none (`FRD-505` FR-3).
- **No record of refused reads.** The log shows who actually saw content. In the restricted mode a
  member is not shown other people's requests at all, list included (`FRD-505` FR-4a), so there is
  no row to open.
- **Existing screens stay where they are.** Security, Register and Requests are not moved into the
  new area.

## 3. Functional requirements

- **FR-1 — Four modes, one control.** The data-protection panel offers the modes of §1 as one
  choice. The retention period is asked whenever content is stored — including in the mode nobody
  in the use case can read, where it is the only thing standing between "kept for oversight" and
  "kept". The use case's administrator owns the choice, as before. A reader who may not change it
  sees the current mode in words.
- **FR-1a — One question, one field.** `payload_visibility` is `members`, `own` or `oversight`;
  `store_payloads` stays beside it and answers the different question of whether anything is
  written. Two booleans would have spelled the four modes and two combinations that mean nothing,
  and retention reads `store_payloads` as a period of zero — which is a fact about storage, not
  about reading.
- **FR-1b — `oversight`: nobody inside the use case.** Not its members, not its administrators.
  Only the roles holding `payload.read_any` — Global Administrator and IT Security — and every one
  of their reads is recorded (FR-3), which is the bargain `ADR-0016` makes. The refusal says who
  *can* read it, that such a read is recorded, and that the use case's administrator chose this
  and can change it: somebody who needs that prompt for a complaint or an incident has to learn
  from the refusal who to ask. The use case's administrator may set this mode and may leave it
  again — the panel belongs to whoever administers the use case (owner's decision, 2026-10-11),
  so the mode is a deliberate restriction and not a lock against them.
- **FR-1c — It hides the content and the split by person; it leaves the rows and the totals.**
  Which *rows* a member sees is what `own` answers, and `oversight` leaves the list alone: a use
  case has to be able to see what failed and what it cost. What it does take, since the works
  council asked on 2026-10-11, is **figures per person** — requests, tokens, cost and response
  times broken down by human (`FRD-603` §5.5): `members` shows everybody's to everybody, `own`
  shows them to the use case's administrators, `oversight` to nobody inside the use case. Totals,
  every other breakdown, the series and each reader's own figures stay in every mode, and
  `report.read_all` is never narrowed. The gateway says which it did (`people_limited`), so the
  console does not decide the rule twice.
- **FR-1d — Erasure does not depend on it.** `retention_days` applies as written in every mode,
  and storage switched off clears what is already there on the next sweep whatever the visibility
  said (`FRD-404`). A visibility is not a legal basis, and the record of who *read* a payload
  outlives the payload (`ADR-0016`).
- **FR-1e — Both ends, and one upgrade window.** Management validates the three values where they
  are typed; the gateway re-applies the bound and reads an **unknown** value as `oversight`, the
  narrowest, because a value it does not recognise came from a newer Management that meant to
  restrict something (`FRD-114`'s rule for capabilities, applied to a value). Management is
  upgraded first (`FRD-204`), so its event carries the retired boolean as well, derived — dropping
  it would unrestrict every restricted use case on a gateway that has not been upgraded yet. The
  migration announces every live use case again so the read-model learns the new field without
  waiting for an edit.
- **FR-2 — The panel says who else reads.** Beside the choice, the panel states that Global
  Administrators and IT Security can read stored content for incidents, and that every read is
  recorded.
- **FR-3 — The reader's roles are kept with the read.** A content read records, as it did before,
  who, which request, which use case, when and on what ground (`incident`, `use_case_admin`,
  `use_case_member`). It now also records the organisation-wide roles the reader held at that
  moment. `incident` does not say whether a Global Administrator or IT Security read the content,
  and a role held today is not evidence of the role held then.
- **FR-4 — The log is an API.** `GET /v1beta/content-reads` lists reads newest first, with cursor
  paging and filters by use case and by reader. The platform roles may call it (Global
  Administrator, IT Security, IT Steuerung); everyone else gets `403`. It returns metadata only, so
  IT Steuerung, which reads no content, may read the log. A read recorded before FR-3 carries
  `roles: null`, not an empty list.
- **FR-5 — Platform administration in the header.** Its own control at the far right of the header,
  after Logout and set apart by a divider, shown to the three platform roles, opens `/platform`.
  The area has a left menu and shows the selected page on the right. It has one entry for now:
  **Content reads**.
- **FR-6 — The content-read page.** A table with:
  - when (`dd.MM.yyyy HH:mm`);
  - who read it (the name, and the subject when there is no name);
  - which use case;
  - which request, as a link to its row in the use case's requests — shown and marked, with the
    content one deliberate click away, because opening it is a recorded read of its own;
  - the ground, in words;
  - the reader's roles at the time.

  It filters by use case and by reader, and is paged at the server: the console holds one page
  of 50 and shows the total ("1–50 of 120 reads", previous and next). The API answers by cursor
  with `count`, the reads the filters match across all pages, so a read recorded while somebody
  pages cannot show a row twice as an offset would. An empty log says that no
  content has been read yet. The requests view takes the linked request from `?request=<id>` and
  asks the gateway for it (`request_id` on `/v1beta/traces`), inside the caller's scope and
  restriction. It says so when the request is no longer in the audit trail.

## 4. Testing & Acceptance Criteria

- **Gateway (hermetic):**
  - a content read records the reader's roles;
  - the log refuses a member and is served to each platform role;
  - it filters, pages newest first, and exposes only the allow-listed fields;
  - an old row keeps `roles: null`.
- **Console (specs):**
  - the panel maps the four modes to the two fields in both directions, and round-trips each of
    them — `fieldsFor` is what a save sends and `modeOf` is what the next open reads, so anything
    but a bijection opens the panel on a mode nobody chose;
  - the header control is shown to the platform roles only;
  - the content-read page renders its rows and its empty state.
- **e2e:** a Global Administrator opens a request's content, then finds that read on the
  content-read page.
- **Mutations:** for the roles snapshot, the role gate and the panel mapping, plus `WR1`–`WR8` for
  the visibility (below).

### 4a. Who may read, as a table (`gateway/tests/test_payload_access.py`)

Seven readers × three stored visibilities, **keyed by both** rather than listed: a list is where a
new column gets added only for the rows somebody thought of, and this mode's interesting cells are
exactly the ones a list would have missed. A completeness check fails on a missing or stale cell,
so a fifth reader or a fourth visibility is a diff that does not pass until it is answered. The
*reason* is asserted and not only the status: every authority refusal is a `403`, and which one it
is decides who the reader goes and talks to.

Beside the table: the refusal names who can read and that the reading is recorded; an oversight
read leaves a `payload_access` row and a refused one leaves none; the rows stay visible while the
content does not (FR-1c), for a colleague as well as for the administrator; and *"not stored"*,
*"you may not"* and *"expired"* remain three different answers — with the authority refusal coming
first, because telling somebody who may not look that a payload *would* have been there is itself
a disclosure.

### 4b. Erasure, across every mode (`gateway/tests/test_content_is_erased_whoever_may_read_it.py`)

The second half of the owner's question, and the one with an instinct to resist in both
directions: *"nobody can see it, so the period hardly matters"* and *"it is only for oversight, so
keep it"*. Parametrised over **every** value rather than the new one, because the claim is that
erasure does not read this field at all:

- the period is the period, and nothing inside it is taken early;
- two use cases that differ only in who may read them are cleared alike (the comparison, which is
  stronger than two separate assertions);
- storage switched off clears what is there whatever the mode was, including from today;
- `oversight` does not exempt a use case from its own `retention_days`;
- the **record of who read it outlives the content** (`ADR-0016`), and record retention still
  takes the row itself.

### 4c. The upgrade (`management/backend/tests`, `gateway/tests/test_consumer_apply.py`)

The carry-over is checked by **running the migrations** backwards and forwards on the test
database, because it reads a field the same migration removes and a test calling its function
directly can only see today's model — the reason `0016` has two `RunPython` steps instead of one.
The event is checked in both directions: a payload carrying only the retired boolean is read as
`own`, a payload carrying both takes the field, an unknown value is read as the narrowest, and an
empty string is silence rather than a choice.

## 5. Rollout

Gateway migration `0044` adds `payload_access.roles` as a nullable column; existing rows keep NULL.

**The visibility (2026-10-11)** is two migrations and one event, in this order: Management's `0016`
adds `payload_visibility`, carries `restrict_members_to_own_requests` over (`true` → `own`),
removes the boolean and **announces every live use case again**; the gateway's `0047` does the same
to its read-model. Management is deployed first (`FRD-204`), and its event keeps emitting the
retired boolean, derived, so a gateway that has not been upgraded yet still restricts what was
restricted. Nothing is deleted and nothing changes meaning: every existing use case lands on the
mode it already had.
