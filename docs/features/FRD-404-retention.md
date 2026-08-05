# FRD-404 — Payload storage and retention (per use case, one week by default)

> Phase: 4 · Status: **Done** · Owner: Vadim Scheibe · Last updated: 2026-08-05
> Builds on FRD-103 (request/response persistence). Closes the retention gap listed in
> `docs/DEPLOYMENT.md §7`.

## 1. Problem

FRD-103 stores the full request and response body of every dispatched call, `store_payloads` is
on by default, and **nothing ever deleted them**. The redaction hook that was supposed to strip
sensitive content is a `NoOpRedactor` — it returns the payload unchanged.

The result: prompts, which routinely contain personal data, accumulated indefinitely in
`request_logs` with no expiry and no way for anyone to state a retention period. For a product
sold to a governance function in Germany, that is the single least defensible property it had.

## 2. Goals & Non-Goals

**Goals**
- A retention period **per use case**, because different use cases process different data and
  the people accountable for them are different.
- A **default of 7 days**, applied without anyone configuring anything — including on upgrade.
- Deletion that actually runs, on a schedule, and can be triggered by hand.
- Keep the accounting: spend and usage reporting must not go blind after a week.

**Non-Goals**
- Content-level redaction (masking a card number inside a kept payload). That is the
  `Redactor` hook and remains a no-op; retention is about *when it goes*, not *what is masked*.
- Legal-hold or per-request exemptions.
- Retention for the Kafka topics or the management database.

## 3. Functional Requirements

- **FR-0 Storage at all**: `UseCase.store_payloads`, default **on** (today's behaviour). Off means
  no prompt or response is written for that use case — the only control that helps for data which
  must not be persisted in the first place. The installation-wide `AIRA_STORE_PAYLOADS` is a
  **kill switch above it**: a use-case admin may decline storage but cannot re-enable it where the
  operator forbade it.
- **FR-0a Switching off purges**: turning storage off is treated as a retention period of zero, so
  what is already stored goes on the next pruner run rather than lingering for the old period.
- **FR-1 Per use case**: `UseCase.retention_days`, 1–3650, default **7**. Editable by a use-case
  admin, distributed to the gateway with the existing `usecase.upserted` event.
- **FR-2 Default on upgrade**: the gateway column defaults to 7, so an installation that
  upgrades without touching anything starts deleting rather than continuing to keep everything.
- **FR-3 Unclaimed traffic**: requests with no use case (unbound break-glass keys, demo mode)
  follow `AIRA_DEFAULT_RETENTION_DAYS` (7). They are not exempt just because nobody claimed them.
- **FR-4 What is removed**: the request and response **payloads**. The row and its metadata —
  subject, timestamp, model, tokens, latency, trace id, cost — are kept.
- **FR-5 Record retention**: `AIRA_LOG_RETENTION_DAYS` deletes whole rows when set. **0 (keep
  forever) by default**, because that is the historical behaviour and the reporting horizon is
  an organisational decision, not something a release should make silently.
- **FR-6 Runs**: `python -m aira_gateway.retention` (`make prune`), hourly as a container in the
  reference stack. Idempotent: a second pass over the same data clears nothing.

## 4. Design

### 4.1 Two clocks, on purpose

| | Control | Default | Effect |
|---|---|---|---|
| Storage | per use case (`store_payloads`) | **on** | whether bodies are written at all |
| Payload retention | per use case (`retention_days`) | **7 days** | when written bodies are removed |
| Record retention | installation-wide (`AIRA_LOG_RETENTION_DAYS`) | off | when the whole row is removed |

Deleting whole rows on the short clock was the obvious first design and is wrong: `request_logs`
is where per-request **cost** lives (FRD-403), so a seven-day row retention would leave the spend
reporting able to see one week and no more. Separating the two lets the sensitive content go
quickly while the figures somebody is accountable for stay.

### 4.2 Absent payload means SQL NULL

The pruner sets the payload columns to `NULL` and only touches rows that still have one, so
repeated runs are cheap and the reported count is the number actually cleared.

That only works because the columns are declared `JSON(none_as_null=True)`. By default
SQLAlchemy writes the JSON value `null` instead of SQL `NULL`, which makes "has no payload"
indistinguishable from "has a payload that happens to be null" — the pruner then rewrites the
same rows on every run, forever, and its count means nothing. Migration `0008` normalises rows
written before the fix.

An index on `(use_case, created_at)` keeps the scan from walking a table that only grows.

### 4.3 Scheduling

The pruner is a one-shot process, like the outbox relay: it does one pass and exits. The
reference stack runs it in a loop container (`AIRA_RETENTION_INTERVAL`, hourly); a real
deployment should use cron, a systemd timer or a Kubernetes CronJob. **If nothing schedules it,
nothing is deleted** — the period configured in the UI is a promise that only this process keeps.

## 5. Testing & Acceptance

- **Unit** (SQLite): the boundary either side of the period; metadata survival; per-use-case
  periods applied independently; unclaimed traffic following the default; a use case the gateway
  no longer knows; idempotence of a second run; record retention off by default and on when
  configured; nonsensical periods clamped rather than deleting everything.
- **Integration** (Postgres): the pruner clears the old payload, keeps the fresh one, keeps
  tokens and cost, and a second run clears nothing — the JSON-null trap only reproduces on a real
  database. Plus: the index exists.
- **Frontend**: the period shown, range validation, saving, and a refused change reported rather
  than silently appearing to work.
- **Storage switch**: declined storage writes no payload but still accounts the request; the
  installation kill switch overrides a use case that wants storage; requests without a use case
  follow the installation setting; an unknown use case keeps the previous behaviour; switching
  off removes what was already stored even though the period has not passed.
- **Acceptance** (verified on the live stack): with storage off for `demo-uc`, a request
  containing a personnel number returned 200, its tokens and cost were recorded, and the number
  appeared **nowhere** in `request_logs`. Separately: two rows aged 10 and 2 days on a use case with the
  default period → the older payload removed, the fresher one kept, both rows retaining tokens
  and cost; a second run cleared nothing; lowering the period to 1 day then removed the second
  payload as well.

## 6. Consequences & follow-ups

- Existing installations start deleting payloads older than a week as soon as the pruner runs the
  first time. That is the intended behaviour and worth announcing before an upgrade.
- The UI states the period on the use-case overview, so it is visible to whoever is accountable
  rather than buried in configuration.
- Switching storage off does not affect requests already in flight, and the purge happens on the
  next pruner run rather than immediately.
- **Follow-ups**: content redaction (the `Redactor` hook is still a no-op) — tracked as `FRD-406`
  in the ROADMAP backlog and **deliberately deferred** until after rate limiting; a retention period for
  the management database's own records; exporting or archiving before deletion for use cases
  with a statutory retention duty; surfacing "payload removed" distinctly from "never stored" in
  a future request-log viewer.
