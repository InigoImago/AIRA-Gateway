# FRD-625 — The privacy notice, written from a register the schema is checked against

> Phase: 6 · Status: **Done** · Owner: Vadim Scheibe
>
> Origin: the owner: the system may be used in large companies, which need a privacy notice on the
> data processing and everything regulatory that comes with it — not least because the audit trail
> could be used to monitor employees. A person should get an official notice as a window at their
> first sign-in, or better at the start of every month; it must be possible to force it permanently
> through configuration, so the notice can be reviewed; it must be readable in several languages;
> and *"when we change the data processing, it has to go into the privacy notice too"*.
>
> Related: [`ADR-0027`](../adr/ADR-0027-the-privacy-notice-is-generated-and-checked.md),
> [`ADR-0016`](../adr/ADR-0016-content-is-readable-and-every-read-is-recorded.md) (content reads),
> [`ADR-0025`](../adr/ADR-0025-aira-defines-what-a-role-may-do.md) (roles as data),
> [`FRD-608`](FRD-608-governance-overview.md) (the register of processing per use case),
> [`FRD-404`](FRD-404-retention.md), [`FRD-622`](FRD-622-platform-administration-and-content-reads.md).

**Not legal advice.** The texts are a sound starting point, grounded in what this code does, and
must be reviewed by the controller's data protection officer and — in Germany — agreed with the
works council before a rollout. Statements that depend on the controller's contracts (processors,
third-country transfers) are phrased as the controller's, and say so.

## 1. What an employee needs to be told, and what the code already answers

Art. 13 DSGVO asks for: the controller and DPO, purposes and legal bases per processing, recipients
and third-country transfers, retention, rights, and automated decisions. In an employment context
two more things matter: `BetrVG` §87(1) no. 6 — a system *objectively capable* of monitoring
behaviour or performance is co-determined — and a clear purpose limitation.

An inventory of both planes (2026-09-16, recorded in the DEVLOG) found twelve processing activities,
from sign-in to telemetry. Three findings shaped the text, because a notice has to describe what
the system does rather than what its FRDs intend:

- per-person consumption figures are visible to **every member** of a use case, and the
  "own requests only" switch does not restrict reports;
- a `new_source_ip` finding writes IP addresses into its `detail`, visible to whoever reads
  findings;
- no table besides request content has an automatic deletion — accounts, findings, suspensions,
  content-read records, API keys and counters are kept until somebody deletes them.

The notice states all three plainly. Whether to change the behaviour is a separate decision (§11).

## 2. Goals & non-goals

- **Goals:** a complete Art. 13 notice in several languages; shown as a window that must be
  acknowledged, on a configurable schedule; readable at any time; **a change to what is stored
  cannot ship without the notice changing** (FR-8).
- **Non-goals:** a consent mechanism (the legal bases are not consent — acknowledging is taking
  note, not agreeing); a list of who acknowledged, for administrators (the rows exist; a view would
  be a new processing and a separate decision); the notice for callers who only use API keys
  (their organisation informs them; the notice's scope says it covers them).

## 3. User stories

- As an **employee**, I read how AIRA processes my data before I use it, in my language.
- As the **data protection officer**, I review the notice as users see it without waiting a month
  (`AIRA_PRIVACY_NOTICE_MODE=always`).
- As a **developer**, I cannot add a column that stores data about people without the suite telling
  me to describe it in the notice.

## 4. Functional requirements

- **FR-1 A window that must be answered.** After sign-in the console asks whether the notice is
  due; if so it opens a window with no ✕, which Escape and the backdrop do not close, whose one
  action is *acknowledge*. A notice that fails to **load** does not lock the console — it is an
  alert with a retry, and the notice stays reachable (FR-2).
- **FR-2 Always reachable.** `/privacy`, linked from every page's footer, shows the notice, its
  edition and when the reader last acknowledged it.
- **FR-3 Languages.** A language is a file in `apps/privacy/texts/`. The server negotiates
  `Accept-Language` (primary subtag, by `q`), falling back to `AIRA_PRIVACY_DEFAULT_LANGUAGE`; an
  explicit `?language=` it has no text for is refused by name. The window and the page switch
  language in place. The console's own words around the notice come from the same file.
- **FR-4 When it is due** (`AIRA_PRIVACY_NOTICE_MODE`, decided on the server):
  `once` — when this person has not acknowledged this version; `monthly` (default) — that, or their
  last acknowledgement of it is before the first of the current month (UTC); `always` — every start
  of the console. A **changed** version is due in every mode. The reason (`first`, `changed`,
  `month`, `always`) is shown in the window.
- **FR-5 The acknowledgement** names the version and language read. A version that is not the
  current one is refused (`409`) — the console then shows the current notice and keeps asking. One
  row per person and version, with the first and latest time; deleted with the account.
- **FR-6 Parity.** A language file lacking a section, activity, label, value or permission phrase
  the others have — or carrying one they do not, or asking for a placeholder nothing fills — is
  refused. Placeholders agree across languages per part.
- **FR-7 The edition.** The notice carries the date its content last changed. `SOURCE_DIGEST` pins
  the register and every text; a change without a new digest fails the suite, whose message says
  to move `EDITION`. The **version** is the edition plus a digest of every language as rendered by
  this installation.
- **FR-8 The register is checked against the schema.** Every column of every table of both planes
  is named by an activity or declared not personal; a column shaped like a person in a table
  declared not personal fails too; every name in the register exists. Every installation-wide
  permission is either listed per role in the notice or declared to reach nobody's data.
- **FR-9 Stated from configuration, not remembered.** The controller, DPO contact and works
  agreement (settings); whether content may be stored and the retention of records and of
  unassigned content (`AIRA_STORE_PAYLOADS`, `AIRA_LOG_RETENTION_DAYS`,
  `AIRA_DEFAULT_RETENTION_DAYS`, now shared by both planes); API key lifetimes; and, **for every
  role the installation defines, what it may do to other people's data** — so a role changed in the
  console changes the notice and asks everybody again.
- **FR-10 Production requires a controller.** Outside `local`, Management refuses to boot with
  `AIRA_PRIVACY_CONTROLLER` empty. Locally the notice prints the gap.

## 5. Design

```
texts/de.toml, en.toml ─┐
activities.py (register)├─ edition.source_digest() ── pinned by a test (FR-7)
                        │
settings + role_definitions() ─► notice.render(lang) ─► notice.version() = EDITION.sha256(all langs)
                                                           │
NoticeAcknowledgement ─► schedule.acknowledgement_due(mode, version, last, any, now) ─► "due"
```

- `apps/privacy/activities.py` — the register: twelve activities, the columns each writes, the
  tables and columns declared not personal, `PERSON_SHAPED`, and which permissions reach other
  people's data. Django-free.
- `texts.py` — loads and validates a language (FR-6). `notice.py` — renders and versions it.
  `schedule.py` — FR-4 as one pure function. `views.py` — the API.
- Console: `core/privacy/` (service, body, language picker, `PrivacyGate` in the shell),
  `features/privacy/privacy-page`, `Modal`'s new `dismissible` input.

## 6. Data model

`privacy.NoticeAcknowledgement(user FK CASCADE, version, language, first_at, last_at)`, unique
`(user, version)`. Migration `privacy/0001_initial`.

## 7. API

- `GET /api/v1/privacy-notice[?language=]` → `{language, title, labels, sections[{key, title,
  paragraphs, items, activities?[{key, title, fields[{key, label, text}]}]}], version, edition,
  languages[{code, name}], mode, due, acknowledged_at}`. `400` for an unknown language.
- `POST /api/v1/privacy-notice/acknowledgements {version, language}` → `201` (first) or `200`
  (again) `{version, language, first_at, last_at}`; `409` when the version is not current; `400`
  for a missing version or unknown language.

Both require sign-in and act only for the caller.

## 8. Security & privacy

The acknowledgement is itself a processing activity and is in the register. The console stores no
language preference in the browser: anything it stored would have to be declared too. Server text
is rendered as text, never markup.

## 9. Observability

Nothing new. Acknowledgements are rows, not events.

## 10. Testing & acceptance

- `test_privacy_notice_schedule.py` (FR-4), `test_privacy_notice_texts.py` (FR-3, FR-6),
  `test_privacy_notice.py` (FR-5, FR-7, FR-9, API), `test_the_privacy_notice_names_every_column.py`
  (FR-8), `test_hardening.py` (FR-10).
- Console: `privacy-gate.spec.ts`, `privacy-page.spec.ts`, `privacy-notice.service.spec.ts`,
  `modal.spec.ts` (not dismissible), `app.spec.ts` (footer link).
- Browser: `e2e/tests/privacy-notice.spec.ts`; `login()` acknowledges a due notice from the
  server's own answer.
- Mutations `PN1`–`PN11`.

## 11. Open decisions for the owner

1. **Per-person figures for every member** (§1). Either restrict `by_member`/`by_person` to
   use-case admins and `report.read_all`, and honour "own requests only" in reports — or keep it
   and keep saying so. The notice says so today.
2. **IP addresses in finding details** — mask them, or keep them for incident response.
3. **Automatic deletion** of findings, suspensions, counters, accounts of people who left, and
   `AIRA_LOG_RETENTION_DAYS=0` (records kept forever) as a default. The notice states each honestly;
   Art. 5(1)(e) DSGVO would prefer periods.

## 12. Rollout / demo

Locally the notice shows the "not configured" controller. The demo accounts meet the window at
their first sign-in. `AIRA_PRIVACY_NOTICE_MODE=always` shows it on every start.
