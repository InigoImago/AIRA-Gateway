# FRD-627 — Strict data mode

> Phase: 6 (reporting) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner, so that a works council has no reason to fear the system as a tool for judging
> employees' conduct or performance (`BetrVG` §87(1) no. 6). Related: `FRD-622` (content
> visibility), `FRD-603` §5.5 (figures per person), `FRD-505` (requests and prompts), `FRD-501`
> and `FRD-503` (findings and stops), `FRD-614` (roles), `FRD-625` (privacy notice),
> `ADR-0036`.

## 1. Summary

An installation-wide mode, **on by default**, in which nobody inside a use case — member or
administrator — and no role short of Global Administrator and IT Security can learn from AIRA what
one named colleague did, how much, or how fast. Content is either not stored or readable by those
two roles only; every figure split by person, every person and key on a request row, and every
person a finding or a stop names is withheld from everybody else. Totals and every breakdown that
is not keyed on a person stay, so a use case still sees what it costs and what fails, and every
reader still sees their own figures and their own requests.

`FRD-622` and `FRD-603` §5.5 built the per-use-case ladder (`members`, `own`, `oversight`). Its top
rung was meant to give this guarantee and did not: under `oversight` every member still saw each
colleague's request rows — time to the second, name, key, latency, cost — and the findings that
named them, from which the withheld split by person rebuilds itself; and `report.read_all` (IT
Steuerung by default) saw every split by person in the installation, an hour-by-hour profile of
one named person included. A use case's administrator could also leave the rung at any time.

## 2. Goals & Non-Goals

**Goals**
- One switch an operator sets, not a choice each use case's administrator can undo.
- Content: two options only — not stored, or Global Administrator and IT Security.
- No per-person figure, row identity or named finding reaches anybody but those two roles and the
  person concerned.
- Latency per person is not computed for anybody: it is a performance measure and serves no cost
  question.
- The privacy notice states which mode the installation runs.

**Non-Goals**
- **Spans and logs keep the key prefix and the source address.** IT Security needs them in an
  incident; the name behind them is pseudonymised (FR-14), and the key's owner is withheld (FR-15).
- **Membership lists keep names.** Who may use a use case is not conduct.
- **The content-read log keeps its readers' names.** It is the control on the two oversight roles.
- **Per-person budgets and rate limits stay.** They are enforced per person; what is withheld is
  everybody else's consumption against them.

## 3. User Stories
- As a **works council**, I want an assurance that holds by configuration, not by each
  administrator's restraint, so that I can agree to the rollout.
- As a **use-case administrator**, I want to see what my use case costs, what fails and which models
  are used, without being able to watch a colleague.
- As a **member**, I want to see my own requests and my own consumption.
- As **IT Security**, I want to investigate an incident down to the person, source address and key.

## 4. Functional Requirements

- **FR-1 — One switch, on by default.** `AIRA_STRICT_DATA_MODE` (shared by both planes, default
  `true`). Not changeable in the console. Turning it off restores each use case's own setting.
- **FR-2 — The people roles.** In strict mode `payload.read_any` and `incident.investigate` are held
  by Global Administrator and IT Security only: no stored role holds them (a stored grant is
  dropped when read), and Management refuses granting them with `400`. "May see people" means
  holding `incident.investigate`.
- **FR-3 — Content.** Every use case that stores content is treated as `oversight`, whatever its
  stored value. Management refuses saving `members` or `own` with `400`, and reports the effective
  value. The console offers *Not stored* and *Oversight roles only*.
- **FR-4 — Figures per person.** `by_member` and `by_person` carry the caller's own row only unless
  the caller may see people — `report.read_all` no longer exempts. `person=` naming somebody else
  answers an empty report. The report says `people_limited` and `strict_data`.
- **FR-5 — No latency per person.** Average and maximum latency are absent from every row of
  `by_member` and `by_person`, and the member CSV has no latency columns, for every caller.
- **FR-6 — KIRA `/ki-usage`** reports one row per **use case**, its slug in `user_id`, to every
  caller with `report.read_all` — the incident roles included (owner's decision): the shape a KIRA
  client parses, and no person.
- **FR-7 — Request rows.** Every row in scope is listed. For a caller who may not see people, a row
  that is not their own carries `subject: null`, `credential: null` and `person_withheld: true`;
  the `subject` and `credential` filters answer `403`. `source_ip` is unchanged
  (`incident.investigate`).
- **FR-8 — Findings and stops.** A finding or a stop whose target is a person or a key, and not the
  caller, carries `target_value: null` and `target_withheld: true` for a caller who may not see
  people.
- **FR-9 — The Members tab.** *What each person used*, including what is left of each person's
  allowance, is not shown to a reader for whom the report is `people_limited` in strict mode. Each
  reader's own figures stay on the Overview tab.
- **FR-10 — The register** reports the effective content visibility.
- **FR-11 — The privacy notice** states whether the mode is on and what it means; new edition.
- **FR-12 — The roles screen** shows the two permissions as not grantable while the mode is on.
- **FR-13 — Grafana.** The shipped dashboard shows no caller, and anonymous access is a Viewer, who
  cannot query traces ad hoc.
- **FR-14 — Pseudonyms in telemetry and logs.** Spans (`aira.subject`) and every log line's person
  fields carry `p-` and an HMAC of the name, keyed by `AIRA_PSEUDONYM_SECRET` (Vault, both planes):
  one person, one pseudonym, no name. Without the secret each process draws a key and warns once.
  Re-identification is the audit trail's: a span's trace id leads to the request row.
- **FR-16 — Opening a request's details is recorded**, for every reader: `GET /v1beta/traces/{id}`
  returns one row as the list shows it and writes an access record (`kind: metadata`) first. The
  access log carries `kind` and filters by it. The console fetches the details this way, and offers
  the prompt and response only to a reader who can read them.
- **FR-15 — A key's person.** The use case's key list shows owner and issuer only to them and to
  whoever sees people; everybody else reads `owner_withheld: true` and "another member".

## 5. Design & Architecture

The rule is decided on read, in each plane, from the shared setting — not by rewriting stored
configuration — so that turning the mode off is lossless and an event from an older Management
cannot weaken it. In the gateway, `payloads.effective_visibility` and `sees_people` are the two
questions every endpoint above asks. In Management, the serializer, the role editor and the
privacy notice read the same setting; `/api/v1/me` tells the console (`strict_data`).

## 7. API / Interface Contract

| Where | Field / behaviour |
| --- | --- |
| `GET /v1beta/reporting` | `strict_data: bool`; per-person rows without `avg_latency_ms`, `max_latency_ms` |
| `GET /v1beta/traces` | `person_withheld: bool` per row; `subject`/`credential` filters `403` |
| `GET /v1beta/anomalies`, `GET /v1beta/suspensions` | `target_withheld: bool` |
| `GET /kira/api/external/ki-usage` | `403` without `incident.investigate` in strict mode |
| `GET /api/v1/me` | `strict_data: bool` |
| `PATCH /api/v1/use-cases/{slug}/` | `400` for `payload_visibility` other than `oversight` |
| `PATCH /api/v1/roles/{slug}/` | `400` for `payload.read_any`, `incident.investigate` |

## 8. Security & Privacy
The mode narrows only. Every refusal names who can see the withheld data, so a legitimate need
finds its way to IT Security.

## 10. Testing & Acceptance Criteria
Hermetic tests per FR in both planes and the console, run with the mode on and off; a mutation per
FR that switches the withholding off.

## 12. Rollout / Demo
On by default, so a fresh installation and the demo run strict. An installation that relied on
`members` or `own` sets `AIRA_STRICT_DATA_MODE=false` and keeps its settings.
