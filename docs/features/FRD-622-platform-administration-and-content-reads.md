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

Together they express exactly three modes, and the panel now offers them as one choice:

| mode | `store_payloads` | `restrict_members_to_own_requests` |
| --- | --- | --- |
| **Not stored** | off | — |
| **Administrators, and each person's own** | on | on |
| **Every member of the use case** | on | off |

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

- **FR-1 — Three modes, one control.** The data-protection panel offers the three modes of §1 as
  one choice. The retention period is asked only when content is stored. The use case's
  administrator owns the choice, as before. A reader who may not change it sees the current mode
  in words.
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

  It filters by use case and by reader, and pages with "Load more". An empty log says that no
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
  - the panel maps the three modes to the two switches in both directions;
  - the header control is shown to the platform roles only;
  - the content-read page renders its rows and its empty state.
- **e2e:** a Global Administrator opens a request's content, then finds that read on the
  content-read page.
- **Mutations:** for the roles snapshot, the role gate and the panel mapping.

## 5. Rollout

Gateway migration `0044` adds `payload_access.roles` as a nullable column; existing rows keep NULL.
The console changes need no data migration: the three modes are the two existing fields.
